import dataclasses
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from controlplane.domain.activity import ActivityInput, ActivityOutput, ActivityStatus
    from controlplane.domain.execution import (
        WorkflowContext,
        WorkflowState,
        WorkflowStatus,
    )
    from controlplane.domain.human_gate import GateAction, HumanGateDefinition
    from controlplane.domain.template import (
        ControlPointDefinition,
        WorkflowTemplate,
    )
    from controlplane.runtime.control_point import ControlPointExecutor
    from controlplane.runtime.human_gate import HumanGateRuntime
    from controlplane.runtime.transition import TransitionResolver


@workflow.defn(name="TemplateWorkflow")
class TemplateWorkflow:
    def __init__(self):
        self._template: WorkflowTemplate | None = None
        self._state: WorkflowState | None = None
        self._cp_executor = ControlPointExecutor(now_fn=workflow.now)
        self._gate_runtime = HumanGateRuntime()
        self._transition_resolver: TransitionResolver | None = None
        self._search_attributes_enabled: bool = False

    @workflow.run
    async def run(self, template_ref: dict) -> dict:
        # Load template through activity
        self._template = await workflow.execute_activity(
            "load_template",
            arg=template_ref,
            result_type=dict,
            start_to_close_timeout=timedelta(seconds=10),
        )
        # Reconstruct WorkflowTemplate from dict
        self._template = self._dict_to_template(self._template)

        self._transition_resolver = TransitionResolver(self._template)
        self._state = WorkflowState(
            workflow_context=WorkflowContext(
                template_id=template_ref["template_id"],
                template_version=template_ref["template_version"],
                workflow_id=workflow.info().workflow_id,
            )
        )

        current_cp = self._template.entry_point
        self._upsert_cp_attributes(current_cp)

        while current_cp is not None:
            cp_def = self._resolve_cp(current_cp)
            self._cp_executor.enter_cp(self._state, current_cp)

            # 1. Human Gate check
            if cp_def.human_gate:
                self._cp_executor.set_waiting_gate(self._state, current_cp)
                action, redirect_target = await self._wait_human_gate(cp_def)
                if action == GateAction.TERMINATE:
                    self._cp_executor.fail_cp(self._state, current_cp, None)
                    self._state.status = WorkflowStatus.TERMINATED
                    return self._build_result()

                if action == GateAction.REDIRECT:
                    target = redirect_target or cp_def.human_gate.redirect_target
                    if target is None:
                        self._cp_executor.fail_cp(self._state, current_cp, None)
                        self._state.status = WorkflowStatus.FAILED
                        return self._build_result()
                    self._cp_executor.resume_from_gate(self._state, current_cp)
                    self._cp_executor.record_transition(self._state, current_cp, target, "REDIRECT")
                    current_cp = target
                    self._upsert_cp_attributes(current_cp)
                    continue

                self._cp_executor.resume_from_gate(self._state, current_cp)

            # 2. Execute Activity (if bound)
            result = None
            if cp_def.activity_binding:
                result = await self._execute_activity(cp_def)
                if result and result.status == ActivityStatus.ERROR:
                    self._cp_executor.fail_cp(self._state, current_cp, result)
                    self._state.status = WorkflowStatus.FAILED
                    return self._build_result()
                self._cp_executor.complete_cp(self._state, current_cp, result)
            else:
                self._cp_executor.complete_cp(self._state, current_cp, ActivityOutput(status=ActivityStatus.OK))

            # 3. Resolve transition
            condition = self._transition_resolver.result_to_condition(result)
            next_cp = self._transition_resolver.resolve_next(current_cp, condition)
            self._cp_executor.record_transition(self._state, current_cp, next_cp, condition)

            # 4. Observability
            self._upsert_cp_attributes(next_cp or "COMPLETED")

            current_cp = next_cp

        self._state.status = WorkflowStatus.COMPLETED
        return self._build_result()

    @workflow.update(name="submit_gate_action")
    def submit_gate_action(self, action: GateAction, redirect_target: str | None = None) -> None:
        self._gate_runtime.set_action(action, redirect_target)

    @submit_gate_action.validator
    def validate_gate_action(self, action: GateAction, redirect_target: str | None = None) -> None:
        self._gate_runtime.validate_action(action, redirect_target)

    @workflow.query(name="current_state")
    def query_current_state(self) -> dict:
        if not self._state:
            return {}
        return {
            "current_cp": self._state.current_cp,
            "status": self._state.status.value,
        }

    @workflow.query(name="awaiting_gate")
    def query_awaiting_gate(self) -> str | None:
        if self._gate_runtime.is_awaiting and self._state and self._state.current_cp:
            cp = self._resolve_cp(self._state.current_cp)
            if cp.human_gate:
                return self._state.current_cp
        return None

    def _resolve_cp(self, cp_id: str) -> ControlPointDefinition:
        for cp in self._template.control_points:
            if cp.id == cp_id:
                return cp
        raise ValueError(f"Control point not found: {cp_id}")

    async def _execute_activity(self, cp_def: ControlPointDefinition) -> ActivityOutput:
        binding = cp_def.activity_binding
        policy = cp_def.execution_policy
        from temporalio.common import RetryPolicy

        retry = RetryPolicy(
            initial_interval=policy.retry_policy.initial_interval,
            backoff_coefficient=policy.retry_policy.backoff_coefficient,
            maximum_interval=policy.retry_policy.maximum_interval,
            maximum_attempts=policy.retry_policy.max_attempts,
            non_retryable_error_types=policy.retry_policy.non_retryable_error_types,
        )

        activity_input = ActivityInput(
            control_point_id=cp_def.id,
            workflow_context=dataclasses.asdict(self._state.workflow_context),
            params=cp_def.params,
        )

        # Temporal requires at least one of start_to_close or schedule_to_close
        start_to_close = policy.timeout_policy.start_to_close
        schedule_to_close = policy.timeout_policy.schedule_to_close
        if start_to_close is None and schedule_to_close is None:
            start_to_close = timedelta(seconds=30)

        output_dict = await workflow.execute_activity(
            binding.activity_name,
            arg=activity_input,
            result_type=dict,
            task_queue=binding.task_queue or workflow.info().task_queue,
            retry_policy=retry,
            schedule_to_close_timeout=schedule_to_close,
            start_to_close_timeout=start_to_close,
            heartbeat_timeout=policy.timeout_policy.heartbeat,
        )

        return ActivityOutput(
            status=ActivityStatus(output_dict["status"]),
            data=output_dict.get("data"),
            error=output_dict.get("error"),
        )

    async def _wait_human_gate(self, cp_def: ControlPointDefinition) -> tuple[GateAction, str | None]:
        self._gate_runtime.reset()
        timeout = cp_def.human_gate.timeout
        try:
            if timeout:
                await workflow.wait_condition(lambda: not self._gate_runtime.is_awaiting, timeout=timeout)
            else:
                await workflow.wait_condition(lambda: not self._gate_runtime.is_awaiting)
        except TimeoutError:
            # Gate timeout expired without action — terminate workflow
            self._gate_runtime.set_action(GateAction.TERMINATE, None)
            return GateAction.TERMINATE, None

        action = self._gate_runtime.pending_action
        redirect_target = self._gate_runtime.resolve_redirect_target(cp_def.human_gate)
        return action, redirect_target

    def _upsert_cp_attributes(self, current_cp: str | None) -> None:
        if not self._search_attributes_enabled:
            return
        from temporalio.common import SearchAttributeKey

        cp_key = SearchAttributeKey.for_text("ControlPlaneCurrentCP")
        status_key = SearchAttributeKey.for_text("ControlPlaneStatus")
        template_key = SearchAttributeKey.for_keyword("ControlPlaneTemplateID")

        workflow.upsert_search_attributes([
            cp_key.value_set(current_cp or "COMPLETED"),
            status_key.value_set(self._state.status.value if self._state else "RUNNING"),
            template_key.value_set(self._state.workflow_context.template_id if self._state else ""),
        ])

    def _build_result(self) -> dict:
        return {
            "status": self._state.status.value,
            "template_id": self._state.workflow_context.template_id,
            "transitions": [
                {"from": t.from_cp, "to": t.to_cp, "condition": t.condition}
                for t in self._state.transitions
            ],
        }

    @staticmethod
    def _maybe_timedelta(v) -> timedelta | None:
        """Convert a float/int seconds value back to timedelta, or pass through None."""
        if v is None:
            return None
        if isinstance(v, timedelta):
            return v
        return timedelta(seconds=float(v))

    @staticmethod
    def _dict_to_template(data: dict) -> WorkflowTemplate:
        from controlplane.domain.template import (
            ActivityBinding, BranchDefinition, ControlPointDefinition,
            MigrationStrategy, TransitionDefinition, TransitionType,
        )
        from controlplane.domain.human_gate import HumanGateDefinition
        from controlplane.domain.policy import ExecutionPolicy, RetryPolicy, TimeoutPolicy

        _td = TemplateWorkflow._maybe_timedelta

        control_points = []
        for cp_data in data.get("control_points", []):
            binding_data = cp_data.get("activity_binding")
            binding = ActivityBinding(**binding_data) if binding_data else None
            gate_data = cp_data.get("human_gate")
            gate = None
            if gate_data:
                gate = HumanGateDefinition(
                    timeout=_td(gate_data.get("timeout")),
                    redirect_target=gate_data.get("redirect_target"),
                )
            retry_data = cp_data.get("execution_policy", {}).get("retry_policy", {})
            timeout_data = cp_data.get("execution_policy", {}).get("timeout_policy", {})
            policy = ExecutionPolicy(
                retry_policy=RetryPolicy(
                    max_attempts=retry_data.get("max_attempts", 3),
                    initial_interval=_td(retry_data.get("initial_interval", 1.0)),
                    backoff_coefficient=retry_data.get("backoff_coefficient", 2.0),
                    maximum_interval=_td(retry_data.get("maximum_interval", 100.0)),
                    non_retryable_error_types=retry_data.get("non_retryable_error_types", []),
                ),
                timeout_policy=TimeoutPolicy(
                    schedule_to_close=_td(timeout_data.get("schedule_to_close")),
                    schedule_to_start=_td(timeout_data.get("schedule_to_start")),
                    start_to_close=_td(timeout_data.get("start_to_close")),
                    heartbeat=_td(timeout_data.get("heartbeat")),
                ),
            )
            control_points.append(ControlPointDefinition(
                id=cp_data["id"], name=cp_data["name"],
                activity_binding=binding, execution_policy=policy, human_gate=gate,
                params=cp_data.get("params"),
            ))

        transitions = []
        for td_data in data.get("transitions", []):
            branches = [
                BranchDefinition(condition=b["condition"], to_cp=b.get("to_cp"))
                for b in td_data.get("branches", [])
            ]
            transitions.append(TransitionDefinition(
                from_cp=td_data["from_cp"],
                transition_type=TransitionType(td_data.get("transition_type", "SEQUENTIAL")),
                branches=branches,
                parallel_targets=td_data.get("parallel_targets", []),
                join_condition=td_data.get("join_condition"),
            ))

        ms_data = data.get("migration_strategy")
        migration = MigrationStrategy(ms_data) if ms_data else None

        return WorkflowTemplate(
            id=data["id"], version=data["version"],
            control_points=control_points, transitions=transitions,
            entry_point=data["entry_point"], migration_strategy=migration,
        )
