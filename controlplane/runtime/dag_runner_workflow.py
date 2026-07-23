"""RunWorkflowSpec — Generic DAG Runner Workflow.

boundary-pinning §6.2: Temporal workflow 是 generic DAG runner，按 nodes 顺序
调度 Activity，每个 Activity 按 capability 调 ToolPool。

本 workflow 接收 WorkflowSpec dict（从 cognitiveplane bridge 层传入），按
depends_on 拓扑排序推进 nodes，每个 node 调 execute_node activity。

与 TemplateWorkflow 的区别：
  - TemplateWorkflow: 基于预定义 WorkflowTemplate FSM（control_points + transitions）
  - RunWorkflowSpec:  基于任意 WorkflowSpec DAG（nodes + depends_on 拓扑排序）

Source: boundary-pinning §6.2
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from controlplane.domain.workflow_spec import (
    WorkflowSpec,
    topological_sort,
    topological_sort_layered,
    workflow_spec_from_dict,
    workflow_spec_to_dict,
)
# Op 27/29: Audit trail + execution record (event sourcing for compliance)
from cognitiveplane.audit.governance_event import GovernanceEventLog
from cognitiveplane.control.planner.versioning import PlanVersionRegistry, RevisionType
from cognitiveplane.audit.decision_record import AuditTrail, DecisionType, RevokeApprovalRecord, BatchHoldRecord, PauseScopeRecord, RelabelRecord, OverrideRecord, DelegateReviewRecord, StandardUpdateRecord, CaseCorrectionRecord
from cognitiveplane.audit.execution_record import (
    NodeExecutionRecord,
    StreamingUpdate,
    WorkflowExecutionRecord,
)
# Op 33: Self-Refine cycle for iterative quality improvement
from cognitiveplane.control.planner.advanced import SelfRefineCycle
# 节点状态机 (影子模式: 同步状态变化, 暴露与现有列表推断的偏差)
from controlplane.runtime.node_state_machine import (
    NodeStateMachine, NodeState, NodeEvent,
)


@workflow.defn(name="RunWorkflowSpec")
class RunWorkflowSpec:
    """Generic DAG Runner — 按 WorkflowSpec.nodes 拓扑排序推进。

    Phase 2 实现：
      - 串行执行（同层无依赖节点也串行，Phase 4+ 加并行）
      - on_failure=abort → 立即终止 workflow
      - on_failure=escalate → 标记 node 失败，继续（Phase 4+ 接 HumanGateSignal）
      - on_failure=continue/retry → 当前等同 escalate
    """

    def __init__(self) -> None:
        self._spec: WorkflowSpec | None = None
        self._node_results: dict[str, dict[str, Any]] = {}
        # P2-5 fix: 拆分语义：
        #   _completed_nodes: 成功完成的节点（status=OK/MARGINAL）— 消费者据此判断成功
        #   _failed_nodes:    失败的节点（status=NG/ERROR 或被 HumanGate 拒绝）
        #   _processed_nodes: 已处理的节点（成功+失败），用于依赖检查放行下游
        self._completed_nodes: list[str] = []
        self._failed_nodes: list[str] = []
        self._processed_nodes: set[str] = set()
        # P0-2 fix: mock 节点单独跟踪 — 静默 mock OK 在工业质检是安全风险
        #   _mocked_nodes: 走 mock fallback 的节点(data.mock=True),即便 status=MARGINAL
        #   消费者(L1/用户)必须能识别"哪些节点没真执行",_build_result / query_status 暴露
        self._mocked_nodes: list[str] = []
        self._status: str = "RUNNING"
        # Op 2: ReviewResult 5-way decision (approve/rework/modify_downstream/reject/escalate)
        # Replaces old bool human_gate. human_review signal writes here.
        self._review_results: dict[str, dict[str, Any]] = {}
        self._review_waiting: set[str] = set()
        # P1-4/P1-5: L2→L1 事件回传 + 智能暂停
        #   _event_meta: 从 spec.metadata 提取的 session_id/callback_url,供 emit 用
        #   _pause_signals: 用户通过 LLM pause_workflow 工具发来的暂停信号
        #     workflow_id → "paused" | "resumed" | "cancelled"
        self._event_meta: dict[str, str] = {}
        self._pause_state: str = "running"  # running | paused | cancelled
        # Op 4: inject_context signal 存储
        self._injected_context: dict[str, Any] = {}
        # Op 5: 节点执行次数跟踪 (用于幂等键)
        self._node_attempts: dict[str, int] = {}
        # Op 20: rework 队列 - 需要重跑的节点集合
        self._rework_nodes: set[str] = set()
        # Op 24: dead letter queue - 反复失败的节点
        self._dead_letter: dict[str, dict[str, Any]] = {}
        # Op 21: Saga compensation ledger - 每个节点的补偿信息
        # {node_id: {"compensate_action": str, "compensate_data": dict, "executed_at": str}}
        self._saga_compensations: dict[str, dict[str, Any]] = {}
        # Op 25: Token 预算跟踪
        self._token_budget_used: int = 0
        self._token_budget_limit: int = 100000  # 默认 10 万 token
        # Op 37: 批量信号队列
        self._pending_signals: list[dict[str, Any]] = []
        # 节点状态机 (影子模式): 每节点一个 NodeStateMachine.
        # 与 _completed/_failed/_processed 列表并行维护, build_result 时断言一致.
        # 不一致 -> 状态机漏设计或代码 bug, 两者都该查.
        self._node_sm: dict[str, NodeStateMachine] = {}
        self._sm_mismatches: list[dict[str, Any]] = []
        # Op-新: 批次冻结 - batch_id -> {node_ids, reason, evidence, resolution}
        self._held_batches: dict[str, dict[str, Any]] = {}
        # Op-新: pause_scope 分级暂停 - STATION/BATCH 级暂停集合
        #   _paused_nodes: set[node_id] - 暂停的工位(单节点)
        #   _paused_batches: set[case_id] - 暂停的批次(按 case_id)
        #   WORKFLOW scope 退化为现有 _pause_state (不加新集合)
        self._paused_nodes: set[str] = set()
        self._paused_batches: set[str] = set()
        # Op-新: delegate_review - reviewer 分配 (node_id -> reviewer_id)
        self._reviewer_assignments: dict[str, str] = {}
        # Op-新: heartbeat 流式 - 当前节点执行进度 (供 query_status 实时查)
        self._current_node: str = ""
        self._current_stage: str = ""
        self._current_progress: float = 0.0
        # 当前 checkpoint 名 (供 pause_scope 记录生效点)
        self._current_checkpoint: str = "init"
        # Op 16-19: Pending spec modification (mid-workflow change)
        self._pending_spec_change: dict[str, Any] | None = None
        # 阶段2: spec 变更类型 (modify_params=热更新 / add_node/remove_node/reorder=cancel+relaunch)
        self._pending_spec_revision_type: str = ""
        # 阶段3: Plan 版本注册表 (versioning.py 接通)
        self._plan_version_registry: PlanVersionRegistry | None = None
        self._current_plan_version: str = ""  # 当前生效的 plan_version_id
        # Op 27: Audit trail (event sourcing - immutable decision records)
        self._audit_trail: AuditTrail | None = None
        # 治理事件流 (阶段1 双写: 改字段同时追加事件, 投影只做 shadow 校验)
        self._governance_log: GovernanceEventLog | None = None
        # 阶段2: shadow 校验结果 (投影 vs 字段, 空=一致)
        self._gov_mismatches: list[dict[str, Any]] = []
        # Op 29: Execution record (complete execution history for replay/learning)
        self._execution_record: WorkflowExecutionRecord | None = None

    def _gov_append(self, event_type: str, scope: str, actor: str,
                    reason: str, node_id: str | None = None,
                    batch_id: str | None = None,
                    payload: dict | None = None,
                    supersedes: str | None = None) -> str:
        """阶段1 双写: 治理模块改字段后调此追加事件. 返回 event_id 供 supersedes."""
        if self._governance_log is None:
            return ""
        ev = self._governance_log.append(
            event_type, scope, actor, reason, workflow.now().timestamp(),
            node_id=node_id, batch_id=batch_id, payload=payload,
            supersedes=supersedes,
        )
        return ev.event_id

    @workflow.run
    async def run(self, workflow_spec_dict: dict[str, Any]) -> dict[str, Any]:
        """主入口 — 接收 WorkflowSpec dict，按拓扑排序执行 nodes。

        Args:
            workflow_spec_dict: WorkflowSpec 序列化 dict（来自 cognitiveplane
                bridge 层的 workflow_spec_to_dict()）

        Returns:
            workflow 执行结果，含 node_results / status / completed / failed
        """
        self._spec = workflow_spec_from_dict(workflow_spec_dict)

        # P1-4: 从 spec.metadata 提取 session_id + callback_url,供 emit_workflow_event 用
        # L1 launch_workflow 工具注入这两个字段,让 L2 能 HTTP POST 节点事件到 L1
        self._event_meta = {
            "session_id": str(self._spec.metadata.get("session_id", "unknown")),
            "callback_url": str(self._spec.metadata.get("callback_url", "")),
            "workflow_id": self._spec.workflow_id,
        }

        workflow.logger.info(
            "RunWorkflowSpec started: workflow_id=%s objective=%s nodes=%d",
            self._spec.workflow_id,
            self._spec.objective,
            len(self._spec.nodes),
        )

        # P1-4: emit workflow_started 事件到 L1
        await self._emit_event("workflow_started")

        # Op 27/29: Initialize audit trail + execution record
        wf_id_init = self._spec.workflow_id
        self._audit_trail = AuditTrail(workflow_id=wf_id_init)
        self._governance_log = GovernanceEventLog()
        # 阶段3: 初始化 Plan 版本注册表 + 冻结初始版本
        self._plan_version_registry = PlanVersionRegistry()
        _initial_spec_dict = workflow_spec_to_dict(self._spec)
        _now_ts = workflow.now().timestamp()
        _pv = self._plan_version_registry.create_version(_initial_spec_dict, timestamp=_now_ts)
        self._plan_version_registry.freeze_version(_pv.version_id, timestamp=_now_ts)
        self._current_plan_version = _pv.version_id
        # 记录初始 spec 版本 (workflow 启动时的 spec hash)
        self._record_spec_revision('workflow initial spec', 'system')
        self._execution_record = WorkflowExecutionRecord(
            workflow_id=wf_id_init,
            objective=self._spec.objective,
            spec_summary={
                "node_count": len(self._spec.nodes),
                "capabilities": list({n.capability for n in self._spec.nodes}),
            },
            # Op 16: workflow 内禁用 wall clock, 用 workflow.now() 的 epoch 秒
            started_at=workflow.now().timestamp(),
        )

        # 拓扑排序（若环或引用错误会抛 ValueError → workflow fail）
        # Op 23: topological sort layered - same-layer nodes can run in parallel
        try:
            layers = topological_sort_layered(self._spec.nodes)
        except ValueError as e:
            self._status = "FAILED"
            workflow.logger.error("Topological sort failed: %s", e)
            await self._emit_event("workflow_failed", error=str(e))
            return self._build_result(error=str(e))


        # Execute by layer - same-layer nodes in parallel, layers serial
        # Op 20: while-loop supports rework/retry (nodes marked for re-execution)
        import asyncio as _aio
        max_rounds = 10  # prevent infinite rework loops
        for _round in range(max_rounds):
            if self._status != "RUNNING":
                break
            had_work = False
            for layer in layers:
                if self._status != "RUNNING":
                    break
                # Only process nodes not yet done or marked for rework
                pending = [
                    n for n in layer
                    if n.node_id not in self._processed_nodes
                    or n.node_id in self._rework_nodes
                ]
                if not pending:
                    continue
                had_work = True
                if len(pending) == 1:
                    await self._process_single_node(pending[0])
                else:
                    workflow.logger.info(
                        "Layer with %d nodes parallel: %s",
                        len(pending), [n.node_id for n in pending],
                    )
                    await _aio.gather(*[self._process_single_node(n) for n in pending])
            if not had_work:
                break

        # Final checkpoint: drain pending signals (e.g. revoke_approval on
        # a committed node) queued during the last node's execution.
        self._apply_pending_signals()

        # Mark workflow complete
        if self._status == "RUNNING":
            self._status = "COMPLETED"

        workflow.logger.info(
            "RunWorkflowSpec finished: status=%s completed=%d failed=%d",
            self._status, len(self._completed_nodes), len(self._failed_nodes),
        )

        # P1-4: emit workflow_completed/failed event
        if self._status == "COMPLETED":
            await self._emit_event("workflow_completed")
        else:
            await self._emit_event("workflow_failed", error=f"status={self._status}")

        return self._build_result()

    async def _process_single_node(self, node) -> None:
        """Process a single node - extracted from main loop for parallel support."""
        if self._status != "RUNNING":
            return

        # Op 37: Apply batched signals at each checkpoint (before node)
        self._apply_pending_signals()

        # Op 16-19: Check if spec was modified mid-workflow
        if self._pending_spec_change is not None:
            self._status = "MODIFIED"
            workflow.logger.info("Workflow modified mid-execution - completing for relaunch")
            return

        # Op 25: Check token budget - warn if exceeded (degraded mode)
        if self._is_budget_exceeded():
            workflow.logger.warning(
                "Token budget exceeded (%d/%d) - node %s in degraded mode",
                self._token_budget_used, self._token_budget_limit, node.node_id,
            )

        # P1-5: 智能暂停 - 检查用户是否发了 pause signal
        if self._pause_state == "cancelled":
            self._status = "FAILED"
            await self._emit_event("workflow_failed", error="cancelled by user")
            return
        # 暂停状态:等待 resume signal(最多 30 分钟,超时自动取消)
        while self._pause_state == "paused":
            await self._emit_event("workflow_paused")
            timed_out = False
            try:
                await workflow.wait_condition(
                    lambda: self._pause_state != "paused",
                    timeout=timedelta(minutes=30),
                )
            except Exception:
                timed_out = True
            if timed_out:
                self._status = "FAILED"
                await self._emit_event("workflow_failed", error="pause timeout (30m)")
                return
            await self._emit_event("workflow_resumed")

        # Op 20: Rework check - if node is marked for rework, clear old result
        if node.node_id in self._rework_nodes:
            workflow.logger.info(
                "Node %s marked for rework - clearing old result and re-executing",
                node.node_id,
            )
            _rework_ev = self._find_last_active_event("REWORK_NODE", node.node_id)
            self._gov_append("REWORK_NODE_COMPLETED", "node", "system",
                             "rework completed", node_id=node.node_id,
                             supersedes=_rework_ev)
            self._refresh_governance_cache()
            self._rework_nodes.discard(node.node_id)
            self._node_results.pop(node.node_id, None)
            if node.node_id in self._completed_nodes:
                self._completed_nodes.remove(node.node_id)
            if node.node_id in self._failed_nodes:
                self._failed_nodes.remove(node.node_id)
            self._processed_nodes.discard(node.node_id)

        self._current_checkpoint = node.node_id  # 供 pause_scope 记录生效点

        workflow.logger.info(
            "Executing node: %s (type=%s capability=%s depends_on=%s)",
            node.node_id, node.type, node.capability, node.depends_on,
        )

        # Op-新: pause_scope 分级暂停 - 若该节点被 STATION/BATCH 级暂停, 阻塞等 resume.
        # (WORKFLOW scope 退化为上面的 _pause_state 整 Run 检查)
        await self._check_node_pause(node.node_id, node.caller_context.case_id)

        # P1-4: emit node_start event to L1
        await self._emit_event("node_start", node_id=node.node_id)

        # Check dependencies are all processed.
        # 若依赖在 _rework_nodes (被 relabel/rework 标记待重跑), 不判 FAILED -
        # 跳过本节点本轮, 等依赖重跑完下轮再处理 (拓扑顺序保证).
        missing_deps = [d for d in node.depends_on
                        if d not in self._processed_nodes and d not in self._rework_nodes]
        if missing_deps:
            self._status = "FAILED"
            err = f"Dependency '{missing_deps[0]}' not processed for node '{node.node_id}'"
            workflow.logger.error(err)
            return
        pending_deps = [d for d in node.depends_on if d in self._rework_nodes]
        if pending_deps:
            workflow.logger.info(
                "Node %s skipped this round (deps in rework: %s) - will retry next round",
                node.node_id, pending_deps,
            )
            return

        # Op 2: human_task waits for human_review signal (5-way decision)
        if node.type == "human_task":
            workflow.logger.info(
                "Node %s is human_task - waiting for human_review signal",
                node.node_id,
            )
            await self._emit_event(
                "node_review_request", node_id=node.node_id, node_status="HUMAN_TASK",
            )
            review = await self._await_review(node)
            if review is None:
                self._failed_nodes.append(node.node_id)
                self._status = "FAILED"
                err = f"human_review timeout for node '{node.node_id}' (waited 30m)"
                workflow.logger.error(err)
                return
            decision = review.get("decision", "approve")
            if decision == "reject":
                result = {
                    "status": "NG",
                    "data": {"node_id": node.node_id, "rejected_by": "human_review"},
                    "error": f"Review rejected: {review.get('feedback', '')}",
                }
                self._node_results[node.node_id] = result
                self._failed_nodes.append(node.node_id)
                self._processed_nodes.add(node.node_id)
                if not self._handle_on_failure(node, "NG", result):
                    return
                return
            if decision == "escalate":
                self._pause_state = "paused"
                return
            if decision == "modify_downstream":
                overrides = review.get("param_overrides", {})
                if overrides:
                    self._apply_downstream_overrides(node, overrides)
            workflow.logger.info(
                "Node %s approved via human_review (decision=%s) - proceeding to activity",
                node.node_id, decision,
            )

        # Build dependency results - use DLQ default for dead-letter nodes
        dep_results: dict[str, dict[str, Any]] = {}
        for dep_id in node.depends_on:
            dep_results[dep_id] = self._get_node_result_or_default(dep_id)

        # Op 22: 条件分支评估 (Argo when clause + Airflow BranchPythonOperator)
        if not self._evaluate_condition(node, dep_results):
            skip_result = {
                "status": "SKIPPED",
                "data": {"node_id": node.node_id, "reason": "condition_not_met"},
            }
            self._node_results[node.node_id] = skip_result
            self._processed_nodes.add(node.node_id)
            await self._emit_event(
                "node_end",
                node_id=node.node_id,
                node_status="SKIPPED",
                node_data={"reason": "condition_not_met"},
            )
            return

        # Op 7: Use _execute_node_v2 (Prepare/Execute/Validate/Commit)
        result = await self._execute_node_v2(node, dep_results)

        # Op 1: ReviewPolicy check for non-human_task nodes
        if node.type != "human_task":
            review_policy = getattr(node, "review_policy", "conditional")
            node_status = str(result.get("status", "ERROR")).upper()
            needs_review = False
            if review_policy == "required":
                needs_review = True
            elif review_policy == "conditional":
                needs_review = node_status not in ("OK",)
            if needs_review:
                await self._emit_event("node_review_request", node_id=node.node_id, node_status=node_status)
                review = await self._await_review(node)
                if review is None:
                    self._failed_nodes.append(node.node_id)
                    self._status = "FAILED"
                    return
                should_continue = self._handle_review_decision(node, review)
                if not should_continue:
                    return
                if self._pause_state == "paused":
                    return
                result = self._node_results.get(node.node_id, result)

        # Op 3: on_failure handling.
        # Op-新: ground_truth_override 的 NG 是质检员权威终态, 跳过 on_failure.
        if isinstance(result.get("data"), dict) and result["data"].get("overridden"):
            workflow.logger.info(
                "Node %s: overridden by inspector, skip on_failure handling",
                node.node_id,
            )
            return
        status = str(result.get("status", "ERROR")).upper()
        if status not in ("OK", "MARGINAL", "SKIPPED"):
            if not self._handle_on_failure(node, status, result):
                comp_results = await self._saga_compensate(node.node_id)
                return

    async def _emit_event(
        self,
        event_type: str,
        node_id: str | None = None,
        node_status: str | None = None,
        node_data: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """P1-4: 通过 emit_workflow_event activity 向 L1 推送事件。

        Temporal workflow 不能直接调 HTTP(sandbox 限制),必须通过 activity。
        用短超时 + 0 次重试,失败不阻断 workflow。
        callback_url 缺失时跳过(开发环境无 L1 回调时不报错)。
        """
        if not self._event_meta.get("callback_url"):
            return  # 无回调 URL — 静默跳过
        try:
            await workflow.execute_activity(
                "emit_workflow_event",
                {
                    **self._event_meta,
                    "event_type": event_type,
                    "node_id": node_id,
                    "node_status": node_status,
                    "node_data": node_data,
                    "error": error,
                },
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(
                    maximum_attempts=1,  # 事件回传不重试
                    initial_interval=timedelta(seconds=1),
                ),
            )
        except Exception as e:
            # 事件回传失败不阻断 workflow
            workflow.logger.warning(
                "emit_workflow_event failed (non-blocking): %s: %s",
                type(e).__name__, e,
            )

    @workflow.query
    def query_status(self) -> dict[str, Any]:
        """查询 workflow 当前状态。"""
        return {
            "status": self._status,
            "current_node": self._current_node,
            "current_stage": self._current_stage,
            "current_progress": self._current_progress,
            "workflow_id": self._spec.workflow_id if self._spec else None,
            "completed_nodes": list(self._completed_nodes),
            "failed_nodes": list(self._failed_nodes),
            # P0-2 fix: 暴露 mock 节点,让 L1/用户识别"哪些没真执行"
            "mocked_nodes": list(self._mocked_nodes),
            # Phase E: shadow 一致性结果暴露给测试侧硬断言 (空=状态机与列表一致)
            "sm_mismatches": list(self._sm_mismatches),
            "node_results": dict(self._node_results),
            # Op 16-19: Include modified spec for L1 relaunch
            "modified_spec": self._pending_spec_change,
            "plan_version": self._current_plan_version,
            "preserved_nodes": list(self._completed_nodes),
            "review_pending": sorted(self._review_waiting),
            "injected_context": dict(self._gov_injected_context()),
            "node_attempts": dict(self._node_attempts),
            "rework_nodes": sorted(self._rework_nodes),
            "held_batches": {k: dict(v) for k, v in self._gov_held_batches().items()},
            "paused_nodes": sorted(self._gov_paused_nodes()),
            "paused_batches": sorted(self._gov_paused_batches()),
            "reviewer_assignments": dict(self._reviewer_assignments),
            "dead_letter_queue": dict(self._dead_letter),
            "saga_compensations": dict(self._saga_compensations),
            "token_budget": {"used": self._token_budget_used, "limit": self._token_budget_limit},
            "pending_signals": len(self._pending_signals),
        }

    @workflow.signal
    def human_review(self, node_id: str, result: dict[str, Any]) -> None:
        """Op 2: Human review signal - replaces human_gate with 5-way decision.

        Args:
            node_id: The node being reviewed
            result: ReviewResult dict with keys:
                - decision: "approve" | "rework" | "modify_downstream" | "reject" | "escalate"
                - feedback: str (optional)
                - param_overrides: dict (optional, for modify_downstream)
        """
        if not isinstance(node_id, str) or not node_id:
            workflow.logger.error("human_review: invalid node_id=%s", node_id)
            return
        decision = result.get("decision", "approve") if isinstance(result, dict) else "approve"
        if decision not in ("approve", "rework", "modify_downstream", "reject", "escalate", "override"):
            workflow.logger.error("human_review: invalid decision=%s", decision)
            return
        stored = result if isinstance(result, dict) else {"decision": decision}
        self._gov_append("HUMAN_REVIEW", "node", "user", "review",
                         node_id=node_id, payload=stored)
        self._refresh_governance_cache()
        workflow.logger.info(
            "human_review signal: node=%s decision=%s",
            node_id, decision,
        )

    @workflow.signal
    def human_gate(self, node_id: str, approved: bool) -> None:
        """Backward-compatible alias for human_review (Op 2).

        Converts bool approved to ReviewResult dict and forwards to human_review.
        """
        decision = "approve" if approved else "reject"
        self.human_review(node_id=node_id, result={"decision": decision})

    @workflow.signal
    def revoke_approval(
        self, node_id: str, revoker_id: str = "unknown",
        reason: str = "", artifact_status: str = "draft",
    ) -> None:
        """Op-新: 撤回已批准决策 signal.

        Args:
            node_id: 要撤销的已 COMMIT 节点
            revoker_id: 撤销人 (责任认定)
            reason: 撤销原因
            artifact_status: "draft" | "formal" | "external"
                决定撤销复杂度 (见 _revoke_approval)

        signal 进入 _pending_signals, 在 checkpoint 批量应用.
        未 COMMIT 的节点会被拒绝 (用 rework_node 代替).
        """
        self._pending_signals.append({
            "type": "revoke_approval",
            "node_id": node_id,
            "revoker_id": revoker_id,
            "reason": reason,
            "artifact_status": artifact_status,
        })
        workflow.logger.info(
            "revoke_approval signal queued: node=%s revoker=%s status=%s",
            node_id, revoker_id, artifact_status,
        )

    @workflow.signal
    def batch_hold(
        self, batch_id: str, reason: str = "", evidence: str = "",
        node_ids: list[str] | None = None,
    ) -> None:
        """Op-新: 批次冻结 - 可疑批次扣留待查, 不阻塞其他批次.

        batch = 共享同一 case_id 的节点组 (焊检域 case_id 是批次标识).
        三档决议: release_hold / rework_batch / quarantine_batch.
        与 pause_scope 区别: batch_hold 隔离结果(fence)不停执行.
        """
        self._pending_signals.append({
            "type": "batch_hold", "batch_id": batch_id,
            "reason": reason, "evidence": evidence, "node_ids": node_ids,
        })
        workflow.logger.info("batch_hold signal queued: batch=%s", batch_id)

    @workflow.signal
    def release_hold(self, batch_id: str) -> None:
        """Op-新: 复查通过 -> HELD 转 COMMITTED, 重新并入 fan-in."""
        self._pending_signals.append({"type": "release_hold", "batch_id": batch_id})
        workflow.logger.info("release_hold signal queued: batch=%s", batch_id)

    @workflow.signal
    def rework_batch(self, batch_id: str) -> None:
        """Op-新: 复查需返工 -> HELD 转 PREPARING + 下游闭包重跑."""
        self._pending_signals.append({"type": "rework_batch", "batch_id": batch_id})
        workflow.logger.info("rework_batch signal queued: batch=%s", batch_id)

    @workflow.signal
    def quarantine_batch(self, batch_id: str) -> None:
        """Op-新: 永久隔离 -> HELD 转 FAILED, 下游通知."""
        self._pending_signals.append({"type": "quarantine_batch", "batch_id": batch_id})
        workflow.logger.info("quarantine_batch signal queued: batch=%s", batch_id)

    @workflow.signal
    def pause_scope(
        self, scope_type: str, scope_id: str,
        reason: str = "", initiator: str = "user",
    ) -> None:
        """Op-新: 分级暂停 - 只暂停指定工位/批次, 其余 workflow 继续.

        scope_type: "workflow" | "station" | "batch"
        scope_id: WORKFLOW=workflow_id; STATION=node_id; BATCH=case_id
        信号缓冲到下一个 checkpoint 批量应用 (不打断 mid-activity).
        与 batch_hold 区别: pause_scope 停止执行(halt); batch_hold 隔离结果(fence).
        """
        self._pending_signals.append({
            "type": "pause_scope", "scope_type": scope_type,
            "scope_id": scope_id, "reason": reason, "initiator": initiator,
        })
        workflow.logger.info(
            "pause_scope signal queued: scope=%s id=%s", scope_type, scope_id,
        )

    @workflow.signal
    def resume_scope(self, scope_type: str, scope_id: str) -> None:
        """Op-新: 恢复指定 scope 的调度 (对应 pause_scope)."""
        self._pending_signals.append({
            "type": "resume_scope", "scope_type": scope_type, "scope_id": scope_id,
        })
        workflow.logger.info(
            "resume_scope signal queued: scope=%s id=%s", scope_type, scope_id,
        )

    @workflow.signal
    def relabel_request(
        self, node_id: str, artifact_id: str,
        original_label: str, corrected_label: str, reason: str = "",
    ) -> None:
        """Op-新: 重新标注 - 标签错但结果对, 只回标注环节.

        是 rework_node 标签专项版: 只重跑 Annot 节点+依赖 label 的下游,
        复用执行结果(不重跑昂贵分析). 信号缓冲到 checkpoint 批量应用.
        """
        self._pending_signals.append({
            "type": "relabel_request", "node_id": node_id,
            "artifact_id": artifact_id, "original_label": original_label,
            "corrected_label": corrected_label, "reason": reason,
        })
        workflow.logger.info(
            "relabel_request signal queued: node=%s label %r->%r",
            node_id, original_label, corrected_label,
        )

    @workflow.signal
    def ground_truth_override(
        self, node_id: str, inspector_id: str,
        forced_verdict: str, reason: str = "", evidence: str = "",
    ) -> None:
        """Op-新: 人工强制覆盖 - 绕过 Evaluator-Optimizer, 质检员直接定 verdict.

        作为 human_review 的 override decision 处理: 写 _review_results,
        review-gate 把它当 override. 必须单独留痕做责任认定(人工对结果负责).
        machine_verdict(机器原裁决)仍保留供校准.
        """
        _override_payload = {
            "decision": "override",
            "forced_verdict": forced_verdict,
            "inspector_id": inspector_id,
            "reason": reason,
            "evidence": evidence,
        }
        self._gov_append("HUMAN_REVIEW", "node", inspector_id, "ground_truth_override",
                         node_id=node_id, payload=_override_payload)
        self._refresh_governance_cache()
        workflow.logger.info(
            "ground_truth_override signal: node=%s inspector=%s verdict=%s",
            node_id, inspector_id, forced_verdict,
        )

    @workflow.signal
    def delegate_review(
        self, target: str, new_reviewer_id: str,
        reason: str = "", is_escalation: bool = False,
    ) -> None:
        """Op-新: 审查权转移 - REQUIRED 分支"谁来 review"变更.

        target: node_id (具体节点) 或 node_type (该类型所有节点).
        更新 reviewer 分配, 重路由 pending review 给新 reviewer.
        已 COMPLETED 决策不可追溯改(不可变).

        立即生效(不排队): 因节点阻塞在 review 时无 checkpoint 触发,
        排队会延迟到 review 完成后才应用, 此时已离开 _review_waiting.
        """
        self._delegate_review(target, new_reviewer_id, reason, is_escalation)
        workflow.logger.info(
            "delegate_review applied: target=%s -> %s", target, new_reviewer_id,
        )

    @workflow.signal
    def standard_update(
        self, standard_id: str, old_version: str, new_version: str,
        effective_date: str = "", diff: str = "",
    ) -> None:
        """Op-新[M]: 标准库版本更新 -> 触发历史判定重审.

        [M]治理类: 扫描引用旧版本的历史DecisionRecord, 分类标记.
        立即生效(知识库变更通知, 无需等checkpoint).
        """
        self._standard_update(standard_id, old_version, new_version,
                              effective_date, diff)
        workflow.logger.info(
            "standard_update applied: %s %s->%s", standard_id, old_version, new_version,
        )

    @workflow.signal
    def case_library_correction(
        self, case_id: str, error_type: str, correction: str,
    ) -> None:
        """Op-新[M]: CBR案例纠错 -> 扫描引用该案例的决策.

        [M]治理类: 影响面>rework_node(跨workflow引用图).
        立即生效(知识库变更通知).
        """
        self._case_library_correction(case_id, error_type, correction)
        workflow.logger.info(
            "case_library_correction applied: case=%s", case_id,
        )

    @workflow.signal
    def pause(self) -> None:
        """P1-5: 暂停 workflow — 用户通过 LLM pause_workflow 工具发此 signal。

        workflow 在下一个节点执行前检查 _pause_state,若为 "paused" 则
        wait_condition 等待 resume signal。最多等 30 分钟,超时自动取消。
        """
        if self._pause_state == "running":
            self._pause_state = "paused"
            workflow.logger.info("Workflow paused by user signal")

    @workflow.signal
    def resume(self) -> None:
        """P1-5: 恢复暂停的 workflow。"""
        if self._pause_state == "paused":
            self._pause_state = "running"
            workflow.logger.info("Workflow resumed by user signal")

    @workflow.signal
    def cancel_by_user(self) -> None:
        """P1-5: 用户取消 workflow — 与 Temporal 内置 cancel 区分(那是系统级)。

        workflow 检测到 _pause_state="cancelled" 后立即终止,返回 FAILED。
        """
        self._pause_state = "cancelled"
        workflow.logger.info("Workflow cancelled by user signal")

    @workflow.signal
    def inject_context(self, key: str, value: Any) -> None:
        """Op 4: 中途注入上下文信息 - 只影响尚未执行的节点。

        用户在执行中补充的信息（如标准/参数/上下文）通过此 signal 注入。
        下一个节点执行前会合并到 node.input。
        """
        if not isinstance(key, str) or not key:
            workflow.logger.error("inject_context: invalid key=%s", key)
            return
        self._gov_append("INJECT_CONTEXT", "workflow", "user", "inject",
                         payload={"key": key, "value": value})
        self._refresh_governance_cache()
        workflow.logger.info(
            "inject_context: key=%s value=%s (affects pending nodes only)",
            key, str(value)[:100],
        )


    @workflow.signal
    def rework_node(self, node_id: str) -> None:
        """Op 20: 请求重跑指定节点及其所有下游节点。

        依赖污染分析 (Airflow downstream propagation):
        用 BFS 遍历 depends_on 图，找到所有直接或间接依赖 node_id 的节点。
        这些节点的结果已被"污染"，必须重跑。

        实现：标记到 _rework_nodes，主循环会在到达这些节点时
        清除旧结果并重新执行。
        """
        if not node_id or not self._spec:
            return

        # BFS: 找到所有下游依赖节点
        affected = self._find_downstream_nodes(node_id)
        affected.add(node_id)

        # 清除受影响节点的结果和完成状态
        for nid in affected:
            self._node_results.pop(nid, None)
            if nid in self._completed_nodes:
                self._completed_nodes.remove(nid)
            if nid in self._failed_nodes:
                self._failed_nodes.remove(nid)
            self._processed_nodes.discard(nid)
            # 增加执行次数 - 重跑用新的幂等键
            self._node_attempts[nid] = self._node_attempts.get(nid, 0) + 1

        self._rework_nodes.update(affected)
        for nid in affected:
            self._gov_append("REWORK_NODE", "node", "user", f"rework from {node_id}", node_id=nid)
        self._refresh_governance_cache()

        workflow.logger.info(
            "rework_node: node=%s affected_downstream=%s (total rework=%d)",
            node_id, sorted(affected - {node_id}), len(affected),
        )

    def _analyze_spec_change(self, new_spec_dict: dict[str, Any]) -> str:
        """分析 spec 变更类型: 拓扑变更 vs 参数变更.

        返回 RevisionType:
        - add_node / remove_node / reorder -> 需要 cancel + relaunch
        - modify_params / modify_node -> 可热更新 (只改未执行节点)

        判定逻辑:
        1. 对比节点 ID 集合: 新增/删除 -> 拓扑变更
        2. 对比依赖关系: 改变 -> reorder (拓扑变更)
        3. 其余: 只改了 input 参数 -> 参数变更
        """
        if self._spec is None:
            return "modify_params"

        old_nodes = {n.node_id: n for n in self._spec.nodes}
        new_nodes_list = new_spec_dict.get("nodes", [])
        new_node_ids = {n.get("node_id", "") for n in new_nodes_list if isinstance(n, dict)}

        old_ids = set(old_nodes.keys())
        new_ids = set(new_node_ids)

        # 节点增删 -> 拓扑变更
        if new_ids - old_ids:
            return "add_node"
        if old_ids - new_ids:
            return "remove_node"

        # 检查依赖关系是否改变 -> reorder
        for n in new_nodes_list:
            if not isinstance(n, dict):
                continue
            nid = n.get("node_id", "")
            old_node = old_nodes.get(nid)
            if old_node:
                old_deps = set(old_node.depends_on)
                new_deps = set(n.get("depends_on", []))
                if old_deps != new_deps:
                    return "reorder"

        # 其余: 只改了 input 参数
        return "modify_params"

    def _apply_param_hot_update(self, new_spec_dict: dict[str, Any]) -> None:
        """参数级热更新: 只改未执行节点的 input, 不 cancel 工作流.

        已完成/正在执行的节点不受影响 (结果已基于旧参数产出).
        未执行的节点在下次到达时读到新 input.
        """
        if self._spec is None:
            return
        new_nodes_list = new_spec_dict.get("nodes", [])
        new_node_map = {n.get("node_id", ""): n for n in new_nodes_list if isinstance(n, dict)}

        updated = 0
        new_nodes_list = []
        for node in self._spec.nodes:
            new_n = new_node_map.get(node.node_id)
            if new_n is None or (node.node_id in self._completed_nodes
                                 or node.node_id in self._processed_nodes):
                new_nodes_list.append(node)
                continue
            # frozen dataclass: 用 replace 创建新实例
            from dataclasses import replace as dc_replace
            replacements = {}
            new_input = new_n.get("input", {})
            if new_input != node.input:
                replacements["input"] = dict(new_input)
            new_on_fail = new_n.get("on_failure")
            if new_on_fail and new_on_fail != node.on_failure:
                replacements["on_failure"] = new_on_fail
            new_review = new_n.get("review_policy")
            if new_review and new_review != node.review_policy:
                replacements["review_policy"] = new_review
            if replacements:
                updated += len(replacements)
                new_nodes_list.append(dc_replace(node, **replacements))
            else:
                new_nodes_list.append(node)

        # 用新节点列表替换 spec 的 nodes
        from dataclasses import replace as dc_replace
        self._spec = dc_replace(self._spec, nodes=new_nodes_list)

        workflow.logger.info(
            "param_hot_update: %d fields updated on pending nodes (no cancel)",
            updated,
        )

    @workflow.signal
    def modify_spec(self, new_spec_dict: dict[str, Any]) -> None:
        """Op 16-19: Mid-workflow modification.

        Source: Airflow dynamic DAG + Temporal Update API (1.25+) + 调研报告 §7.2.

        参数级变更 (modify_params): 热更新未执行节点 input, 不 cancel.
        拓扑变更 (add/remove/reorder): cancel + relaunch (现有行为).
        """
        revision_type = self._analyze_spec_change(new_spec_dict)
        self._pending_spec_revision_type = revision_type

        if revision_type == "modify_params":
            # 参数级变更: 热更新, 不 cancel
            self._apply_param_hot_update(new_spec_dict)
            self._record_spec_revision(f'param hot update ({revision_type})', 'user')
            workflow.logger.info(
                "modify_spec: parameter-only change -> hot updated (no cancel)"
            )
            return

        # 拓扑变更: cancel + relaunch (现有行为)
        self._pending_spec_change = new_spec_dict
        # 阶段3: 走 PlanRevisionProposal (带影响分析) + 批准 + 创建新版本
        _rev_type = RevisionType(revision_type) if revision_type in (
            "add_node", "remove_node", "modify_node", "modify_params", "reorder"
        ) else RevisionType.MODIFY_PARAMS
        _proposal = self._plan_version_registry.propose_revision(
            revision_type=_rev_type,
            change_description=f"modify_spec: {revision_type}",
            impact_nodes=list(new_spec_dict.get("nodes", [])),
            new_spec_dict=new_spec_dict,
        )
        self._plan_version_registry.approve_proposal(_proposal.proposal_id, "user")
        if self._plan_version_registry.current_version:
            self._current_plan_version = self._plan_version_registry.current_version.version_id
        # 记录 spec 版本更迭到事件流
        self._record_spec_revision(f'topology change ({revision_type})', 'user')
        workflow.logger.info(
            "modify_spec: received new spec with %d nodes",
            len(new_spec_dict.get("nodes", [])),
        )

    def _find_downstream_nodes(self, node_id: str) -> set[str]:
        """Op 20: BFS 遍历 depends_on 图，找到所有依赖 node_id 的节点。

        返回包括直接依赖和间接依赖的所有下游节点。
        """
        if not self._spec:
            return set()

        affected: set[str] = set()
        queue: list[str] = [node_id]

        while queue:
            current = queue.pop(0)
            for node in self._spec.nodes:
                if current in node.depends_on and node.node_id not in affected:
                    affected.add(node.node_id)
                    queue.append(node.node_id)

        return affected


    async def _await_review(
        self, node: Any, timeout_min: int = 30
    ) -> dict[str, Any] | None:
        """等待 human_review signal, 返回 ReviewResult dict 或 None(超时)。"""
        workflow.logger.info(
            "Node %s waiting for human_review (timeout=%dm)",
            node.node_id, timeout_min,
        )
        self._review_waiting.add(node.node_id)
        # wait_condition 返回 None(成功) / 抛 TimeoutError(超时).
        # 不能用 "if not ok" 判断 (None 恒为 falsy -> 永远误判超时).
        timed_out = False
        try:
            await workflow.wait_condition(
                lambda: node.node_id in self._review_results,
                timeout=timedelta(minutes=timeout_min),
            )
        except Exception:
            timed_out = True
        self._review_waiting.discard(node.node_id)
        if timed_out:
            return None
        return self._review_results.get(node.node_id, {})

    def _handle_review_decision(
        self, node: Any, review: dict[str, Any]
    ) -> bool:
        """处理审查决策,返回 True=继续 False=应终止 workflow。"""
        decision = review.get("decision", "approve")
        feedback = review.get("feedback", "")

        # Op 27: Record review decision in audit trail
        if self._audit_trail is not None:
            wf_id = self._spec.workflow_id if self._spec else ""
            self._audit_trail.record(
                DecisionType.NODE_REVIEW, node.node_id,
                f"review={decision}", feedback[:200], "user",
                now_ts=workflow.now().timestamp(),
            )

        if decision == "approve":
            workflow.logger.info(
                "Node %s reviewed: APPROVE (%s)", node.node_id, feedback[:80],
            )
            return True

        if decision == "modify_downstream":
            # 通过但修改下游参数
            workflow.logger.info(
                "Node %s reviewed: MODIFY_DOWNSTREAM (%s)", node.node_id, feedback[:80],
            )
            return True

        if decision == "rework":
            # Op 20: 重跑本节点及所有下游受影响节点 (依赖污染 BFS)
            affected = self._find_downstream_nodes(node.node_id)
            affected.add(node.node_id)
            for nid in affected:
                self._node_results.pop(nid, None)
                if nid in self._completed_nodes:
                    self._completed_nodes.remove(nid)
                if nid in self._failed_nodes:
                    self._failed_nodes.remove(nid)
                self._processed_nodes.discard(nid)
                self._node_attempts[nid] = self._node_attempts.get(nid, 0) + 1
            self._rework_nodes.update(affected)
            for nid in affected:
                self._gov_append("REWORK_NODE", "node", "user",
                                 "review rework", node_id=nid)
            self._refresh_governance_cache()
            workflow.logger.info(
                "Node %s reviewed: REWORK -> affected nodes: %s",
                node.node_id, sorted(affected),
            )
            return True

        if decision == "reject":
            # 拒绝 -> 走 on_failure
            result = {
                "status": "NG",
                "data": {"node_id": node.node_id, "rejected_by": "human_review"},
                "error": f"Review rejected: {feedback}",
            }
            self._node_results[node.node_id] = result
            self._failed_nodes.append(node.node_id)
            self._processed_nodes.add(node.node_id)
            return self._handle_on_failure(node, "NG", result)

        if decision == "escalate":
            # 暂停整个 workflow
            workflow.logger.warning(
                "Node %s reviewed: ESCALATE - pausing workflow", node.node_id,
            )
            self._pause_state = "paused"
            return True

        if decision == "override":
            # Op-新: ground_truth_override - 绕过 evaluator, 质检员直接拍板.
            # machine_verdict 保留供校准, ground_truth_verdict 实际生效.
            # 单独留痕做责任认定: 人工对结果负责.
            forced = review.get("forced_verdict", "OK")
            inspector = review.get("inspector_id", "unknown")
            reason_ov = review.get("reason", feedback[:200])
            evidence_ov = review.get("evidence", "")
            now_ts = workflow.now().timestamp()

            # 保留 machine_verdict (当前 result 里的机器裁决)
            _cur = self._node_results.get(node.node_id, {})
            machine_verdict = str(_cur.get("status", "unknown"))
            if isinstance(_cur.get("data"), dict):
                qs = _cur["data"].get("quality_score")
                if qs is not None:
                    machine_verdict = f"{machine_verdict}(score={qs})"

            # 用 forced_verdict 覆盖结果 (实际生效).
            # NG 是质检员权威终态(不是失败要重试), 标 processed 跳过 on_failure.
            forced_status = forced.upper()
            self._node_results[node.node_id] = {
                **_cur,
                "status": forced_status,
                "data": {**(_cur.get("data") or {}),
                         "overridden": True,
                         "ground_truth_verdict": forced,
                         "override_by": inspector},
            }
            self._processed_nodes.add(node.node_id)
            if forced_status in ("OK", "MARGINAL"):
                if node.node_id not in self._completed_nodes:
                    self._completed_nodes.append(node.node_id)
            else:
                # 质检员判 NG -> 视为已处理终态(非失败重试)
                if node.node_id not in self._failed_nodes:
                    self._failed_nodes.append(node.node_id)

            # 状态机: OVERRIDE 迁移 (AWAITING_REVIEW -> COMMITTED)
            sm = self._sm(node.node_id)
            if sm.can_transition(NodeEvent.OVERRIDE):
                sm.transition(NodeEvent.OVERRIDE, inspector_id=inspector)
                self._gov_append("GROUND_TRUTH_OVERRIDE", "node", inspector, reason_ov,
                                 node_id=node_id,
                                 payload={"forced_verdict": forced, "machine_verdict": machine_verdict})

            rec = OverrideRecord(
                node_id=node.node_id, machine_verdict=machine_verdict,
                ground_truth_verdict=forced, override_by=inspector,
                responsibility_owner=inspector, reason=reason_ov,
                evidence=evidence_ov,
                calibration_pair_id=f"{node.node_id}:calib:{int(now_ts)}",
                timestamp=now_ts,
            )
            if self._execution_record is not None:
                self._execution_record.add_reflexion_note(f"OVERRIDE: {rec.to_dict()}")
            self._audit_trail.record(
                DecisionType.GROUND_TRUTH_OVERRIDE, node.node_id,
                decision=f"override machine={machine_verdict!r} -> ground_truth={forced!r}",
                rationale=reason_ov, actor=inspector, now_ts=now_ts,
                context={"override_record": rec.to_dict()},
            )
            workflow.logger.warning(
                "Node %s: GROUND_TRUTH_OVERRIDE by %s: machine=%s -> forced=%s",
                node.node_id, inspector, machine_verdict, forced,
            )
            return True

        return True

    def _handle_on_failure(
        self, node: Any, status: str, result: dict[str, Any]
    ) -> bool:
        """Op 3: on_failure 五种语义真实分流。返回 True=继续 False=终止。"""
        on_failure = getattr(node, "on_failure", "escalate")

        # Op 27: Record failure handling decision in audit trail
        if self._audit_trail is not None:
            wf_id = self._spec.workflow_id if self._spec else ""
            self._audit_trail.record(
                DecisionType.ON_FAILURE, node.node_id,
                f"on_failure={on_failure}, status={status}",
                result.get("error", "")[:200], "system",
                now_ts=workflow.now().timestamp(),
            )

        if on_failure == "abort":
            self._status = "FAILED"
            workflow.logger.error(
                "Node %s failed (status=%s), on_failure=abort -> workflow FAILED",
                node.node_id, status,
            )
            return False

        if on_failure == "escalate":
            # 标记失败 + 暂停等人工 (emit escalation 事件)
            workflow.logger.warning(
                "Node %s failed (status=%s), on_failure=escalate -> paused for human",
                node.node_id, status,
            )
            self._pause_state = "paused"
            return True

        if on_failure == "retry":
            # retry: Temporal RetryPolicy 已在 activity 层重试 3 次
            # 如果到了这里说明 activity 重试已耗尽 -> 检查是否该进死信队列
            attempt = self._node_attempts.get(node.node_id, 0)
            if attempt >= 3:
                # Op 24: retry 3 次仍失败 -> 死信队列隔离
                self._send_to_dlq(node.node_id, result, attempt)
                workflow.logger.error(
                    "Node %s failed (status=%s), on_failure=retry -> DLQ after %d attempts",
                    node.node_id, status, attempt,
                )
                # 标记已处理，下游用默认值继续
                self._processed_nodes.add(node.node_id)
                return True
            # 重新执行：增加 attempt 计数，主循环会再次到达此节点
            self._node_attempts[node.node_id] = attempt + 1
            self._rework_nodes.add(node.node_id)
            self._gov_append("REWORK_NODE", "node", "system",
                             f"on_failure=retry (attempt {attempt+1})",
                             node_id=node.node_id)
            self._refresh_governance_cache()
            self._processed_nodes.discard(node.node_id)
            if node.node_id in self._completed_nodes:
                self._completed_nodes.remove(node.node_id)
            workflow.logger.warning(
                "Node %s failed (status=%s), on_failure=retry -> attempt %d, will retry",
                node.node_id, status, attempt + 1,
            )
            return True

        if on_failure == "rework":
            # rework: 新 NodeAttempt + 改参数 -> 重跑本节点及下游
            workflow.logger.warning(
                "Node %s failed (status=%s), on_failure=rework -> rework node + downstream",
                node.node_id, status,
            )
            affected = self._find_downstream_nodes(node.node_id)
            affected.add(node.node_id)
            for nid in affected:
                self._node_results.pop(nid, None)
                if nid in self._completed_nodes:
                    self._completed_nodes.remove(nid)
                if nid in self._failed_nodes:
                    self._failed_nodes.remove(nid)
                self._processed_nodes.discard(nid)
                self._node_attempts[nid] = self._node_attempts.get(nid, 0) + 1
            self._rework_nodes.update(affected)
            for nid in affected:
                self._gov_append("REWORK_NODE", "node", "system",
                                 "on_failure=rework", node_id=nid)
            self._refresh_governance_cache()
            return True

        if on_failure == "fallback":
            # fallback: 换 capability (如 MLLM->CV)
            workflow.logger.warning(
                "Node %s failed (status=%s), on_failure=fallback -> switch capability",
                node.node_id, status,
            )
            # 重跑本节点，标记需要 fallback
            self._rework_nodes.add(node.node_id)
            self._gov_append("REWORK_NODE", "node", "system",
                             "on_failure=fallback", node_id=node.node_id)
            self._refresh_governance_cache()
            self._processed_nodes.discard(node.node_id)
            if node.node_id in self._completed_nodes:
                self._completed_nodes.remove(node.node_id)
            self._node_attempts[node.node_id] = self._node_attempts.get(node.node_id, 0) + 1
            # 在 node_input 里标记 fallback，让 activity 选择降级路径
            existing = self._node_results.get(node.node_id, {})
            existing["_fallback"] = True
            self._node_results[node.node_id] = existing
            return True

        if on_failure == "continue":
            # 标记失败,下游继续
            workflow.logger.warning(
                "Node %s failed (status=%s), on_failure=continue -> downstream continues",
                node.node_id, status,
            )
            return True

        # 默认: 同 continue
        workflow.logger.warning(
            "Node %s failed (status=%s), on_failure=%s -> default continue",
            node.node_id, status, on_failure,
        )
        return True

    def _apply_downstream_overrides(self, node: Any, overrides: dict[str, Any]) -> None:
        """将 modify_downstream 的 param_overrides 应用到下游节点。"""
        # overrides 是 {node_id: {field: value}} 或 {field: value}
        # 前者指定节点,后者应用到所有下游
        workflow.logger.info(
            "Applying downstream overrides from node %s: %s",
            node.node_id, list(overrides.keys()),
        )
        # 当前实现: 存入 _node_results 供下游 activity 读取
        # Phase 4 Round 2 实现完整的 inject 机制
        self._node_results.setdefault(node.node_id, {}).setdefault("_downstream_overrides", {}).update(overrides)


    # ── Op 21: Saga compensation ───────────────────────────────

    def _record_saga_compensation(
        self, node_id: str, result: dict[str, Any]
    ) -> None:
        """Op 21: 记录节点的补偿信息，供失败时逆序执行。

        Saga pattern (Garcia-Molina & Salem, SIGMOD 1987):
        每个正向操作有一个补偿操作。失败时逆序执行已完成节点的补偿。

        补偿信息从节点的 result.data 中提取（如果 L3 activity 返回了
        compensate_action 字段），或用默认补偿逻辑（如标记为 stale）。
        """
        data = result.get("data") or {}
        compensate_action = data.get("compensate_action", "")
        compensate_data = data.get("compensate_data", {})

        if not compensate_action:
            # 无显式补偿 -> 用默认：标记结果为 stale
            compensate_action = "mark_stale"
            compensate_data = {"reason": "saga_rollback"}

        self._saga_compensations[node_id] = {
            "compensate_action": compensate_action,
            "compensate_data": compensate_data,
            "node_status": result.get("status", ""),
        }

    async def _saga_compensate(
        self, failed_node_id: str
    ) -> list[dict[str, Any]]:
        """Op 21: 逆序执行已完成节点的补偿操作。

        遍历 _completed_nodes（逆序），对每个节点执行其补偿操作。
        返回补偿结果列表（供 audit / 日志使用）。

        补偿操作通过 execute_node activity 执行，用 compensate_action
        作为 capability，compensate_data 作为 input。
        """
        compensation_results: list[dict[str, Any]] = []

        # 逆序遍历已完成节点（在 failed_node 之前的）
        to_compensate = []
        for nid in reversed(self._completed_nodes):
            if nid == failed_node_id:
                break
            if nid in self._saga_compensations:
                to_compensate.append(nid)

        for nid in to_compensate:
            comp = self._saga_compensations[nid]
            workflow.logger.info(
                "Saga compensation: node=%s action=%s",
                nid, comp["compensate_action"],
            )
            try:
                comp_result = await workflow.execute_activity(
                    "execute_node",
                    {
                        "node_id": f"{nid}_compensate",
                        "type": "compensation",
                        "capability": comp["compensate_action"],
                        "input": comp["compensate_data"],
                        "workflow_id": self._spec.workflow_id if self._spec else "",
                        "idempotency_key": f"saga_compensate:{nid}",
                        "dependency_results": {},
                        "caller_context": {
                            "caller_type": "saga_compensator",
                            "case_id": None,
                            "node_id": nid,
                            "session_id": self._event_meta.get("session_id", ""),
                        },
                    },
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
                compensation_results.append({
                    "node_id": nid,
                    "compensate_action": comp["compensate_action"],
                    "result": comp_result,
                })
            except Exception as e:
                workflow.logger.error(
                    "Saga compensation FAILED for node %s: %s", nid, e,
                )
                compensation_results.append({
                    "node_id": nid,
                    "compensate_action": comp["compensate_action"],
                    "error": str(e),
                })

        return compensation_results

    # ── Op-新: revoke_approval 撤回已批准决策 ────────────────────────
    def _revoke_approval(
        self, node_id: str, revoker_id: str, reason: str,
        artifact_status: str = "draft",
    ) -> dict[str, Any]:
        """撤回已 COMMIT 的节点决策 (误判处理汇聚出口).

        对应状态机: COMMITTED -> REVOKED (单向门, 不可逆).
        与 rework_node(回退第一级)的区别: rework 只对未 COMMIT 节点;
        revoke 专处理已 COMMIT, artifact 可能已转正式/离系统.

        artifact_status 决定撤销复杂度 (回退层级):
          draft    -> 走 rework_node 下游闭包 (回退第二级, 较轻)
          formal    -> 记补偿 + 发更正版 v2 + 通知下游 (回退第三级)
          external  -> 已离系统不可 undo, 只发更正通知 (最重)

        下游处理: BFS 找已消费该节点 artifact 的下游, 标 AFFECTED_BY_REVOKE
                  (不自动重跑, 留人工决定 - 可扩展).
        """
        wf_id = self._spec.workflow_id if self._spec else ""
        now_ts = workflow.now().timestamp()

        # 守卫: 只能撤销已 COMMIT 的节点
        sm = self._sm(node_id)
        if sm.state != NodeState.COMMITTED:
            workflow.logger.error(
                "revoke_approval REJECTED: node=%s state=%s (not COMMITTED) - "
                "use rework_node for uncommitted nodes",
                node_id, sm.state.value,
            )
            self._audit_trail.record(
                DecisionType.REVOKE_APPROVAL, node_id,
                decision=f"revoke REJECTED (node not COMMITTED, state={sm.state.value})",
                rationale=reason, actor=revoker_id,
                context={"artifact_status": artifact_status},
                now_ts=now_ts,
            )
            return {"ok": False, "error": f"node not COMMITTED (state={sm.state.value})"}

        # BFS 找受影响下游 (depends_on 链)
        affected = self._find_affected_downstream(node_id)

        # 状态机迁移: COMMITTED -> REVOKED
        sm.transition(NodeEvent.REVOKE, revoker_id=revoker_id, reason=reason)
        self._gov_append("REVOKE_APPROVAL", "node", revoker_id, reason,
                         node_id=node_id,
                         payload={"artifact_status": artifact_status})

        # 追溯原批准 DecisionRecord (责任链: 原 approver -> 现 revoker)
        original_decision_id = None
        for _rec in reversed(self._audit_trail.query_by_node(node_id)):
            if _rec.decision_type == DecisionType.NODE_REVIEW and "approve" in _rec.decision:
                original_decision_id = _rec.record_id
                break

        # 从 completed 移到 failed (workflow 视角: 不再成功)
        if node_id in self._completed_nodes:
            self._completed_nodes.remove(node_id)
        self._failed_nodes.append(node_id)
        self._node_results[node_id] = {
            **self._node_results.get(node_id, {}),
            "status": "REVOKED",
            "data": {**(self._node_results.get(node_id, {}).get("data") or {}),
                     "revoked": True, "revoked_by": revoker_id, "revoke_reason": reason},
        }

        # 按 artifact_status 分支处理
        compensation_actions: list[dict[str, Any]] = []
        correction_version: str | None = None
        notified: list[str] = []

        if artifact_status == "draft":
            # 回退第二级: 标记下游 rework (复用现有 rework 机制)
            for dn in affected:
                self._rework_nodes.add(dn)
                self._gov_append("REWORK_NODE", "node", revoker_id,
                                 "revoke downstream", node_id=dn)
            self._refresh_governance_cache()
            workflow.logger.info(
                "revoke node=%s (draft): %d downstream marked for rework",
                node_id, len(affected),
            )

        elif artifact_status == "formal":
            # 回退第三级: 记补偿动作 + 发更正版 v2 + 通知
            correction_version = f"{node_id}_v2_revoked"
            for dn in affected:
                compensation_actions.append({
                    "node_id": dn,
                    "action": "notify_correction",
                    "correction_version": correction_version,
                })
                notified.append(dn)
            workflow.logger.warning(
                "revoke node=%s (formal): correction v2=%s, %d downstream notified",
                node_id, correction_version, len(notified),
            )

        elif artifact_status == "external":
            # 已离系统: 不可 undo, 只发更正通知
            correction_version = f"{node_id}_v2_external_revoked"
            for dn in affected:
                notified.append(dn)
            workflow.logger.error(
                "revoke node=%s (external): CANNOT undo - artifact left system. "
                "%d consumers notified for manual correction.",
                node_id, len(notified),
            )

        # 记 RevokeApprovalRecord (责任认定留痕)
        revoke_rec = RevokeApprovalRecord(
            revoked_node_id=node_id,
            revoker_id=revoker_id,
            reason=reason,
            artifact_status=artifact_status,
            affected_downstream=affected,
            compensation_actions=compensation_actions,
            correction_artifact_version=correction_version,
            notified_consumers=notified,
            original_decision_id=original_decision_id,
            timestamp=now_ts,
        )
        # 存到 execution_record (供审计导出)
        if self._execution_record is not None:
            self._execution_record.add_reflexion_note(
                f"REVOKE: {revoke_rec.to_dict()}"
            )

        # 记 DecisionRecord (不可变审计链)
        self._audit_trail.record(
            DecisionType.REVOKE_APPROVAL, node_id,
            decision=f"revoke approved (artifact={artifact_status}, "
                     f"{len(affected)} downstream affected)",
            rationale=reason, actor=revoker_id,
            context={"revoke_record": revoke_rec.to_dict()},
            now_ts=now_ts,
        )

        workflow.logger.info(
            "revoke_approval complete: node=%s revoker=%s status=%s affected=%d",
            node_id, revoker_id, artifact_status, len(affected),
        )
        return {"ok": True, "revoke_record": revoke_rec.to_dict()}

    def _find_affected_downstream(self, node_id: str) -> list[str]:
        """BFS 遍历 depends_on 图, 找所有直接/间接消费该节点 artifact 的下游.

        Op 20 依赖污染分析 (Airflow downstream propagation).
        返回拓扑序的受影响节点列表 (不含 node_id 本身).
        """
        if self._spec is None:
            return []
        affected: list[str] = []
        seen: set[str] = {node_id}
        queue: list[str] = [node_id]
        while queue:
            current = queue.pop(0)
            for n in self._spec.nodes:
                if current in n.depends_on and n.node_id not in seen:
                    seen.add(n.node_id)
                    affected.append(n.node_id)
                    queue.append(n.node_id)
        return affected

    # ── Op-新: batch_hold 批次冻结 ────────────────────────────────
    def _nodes_in_batch(self, batch_id: str, explicit: list[str] | None = None) -> list[str]:
        """找出属于指定 batch 的节点.

        batch = 共享同一 case_id 的节点组 (焊检域 case_id 天然是批次标识).
        若显式传入 explicit node_ids 则直接用 (测试/精确控制用).
        """
        if explicit:
            return [nid for nid in explicit if self._spec
                    and any(n.node_id == nid for n in self._spec.nodes)]
        if not self._spec:
            return []
        return [n.node_id for n in self._spec.nodes
                if n.caller_context.case_id == batch_id]

    def _batch_hold(
        self, batch_id: str, reason: str, evidence: str = "",
        node_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """扣留可疑批次 (质量可疑隔离, 非失败).

        对应状态机: COMMITTED/AWAITING_REVIEW -> HELD (HOLD 事件).
        与 pause_scope 区别: batch_hold 隔离结果(artifact 冻结), 不停执行;
        与 DLQ 区别: DLQ 是失败隔离(fan-in 默认值); batch_hold 是质量可疑隔离(路由复查).
        其他批次不受影响 (只动本 batch_id 节点).
        """
        if self._spec is None:
            return {"ok": False, "error": "no spec"}
        now_ts = workflow.now().timestamp()
        held_ids = self._nodes_in_batch(batch_id, node_ids)
        if not held_ids:
            workflow.logger.warning(
                "batch_hold: no nodes found for batch_id=%s", batch_id,
            )
            self._audit_trail.record(
                DecisionType.BATCH_HOLD, batch_id,
                decision="batch_hold REJECTED (no nodes in batch)",
                rationale=reason, actor="user", now_ts=now_ts,
                context={"evidence": evidence},
            )
            return {"ok": False, "error": f"no nodes in batch {batch_id}"}

        held_artifacts: list[str] = []
        for nid in held_ids:
            sm = self._sm(nid)
            # 只能 hold 已提交或待审的节点 (PENDING/EXECUTING 不该 hold, 让它先跑完)
            if sm.state in (NodeState.COMMITTED, NodeState.AWAITING_REVIEW):
                sm.transition(NodeEvent.HOLD, batch_id=batch_id, reason=reason)
                # 从 completed 移除 (HELD 不算成功)
                if nid in self._completed_nodes:
                    self._completed_nodes.remove(nid)
                _nr = self._node_results.get(nid, {})
                _art = f"{self._spec.workflow_id}:{nid}:v1"
                held_artifacts.append(_art)
                self._node_results[nid] = {
                    **_nr,
                    "status": "HELD",
                    "data": {**(_nr.get("data") or {}),
                             "held": True, "batch_id": batch_id, "hold_reason": reason},
                }
            elif sm.state == NodeState.HELD:
                workflow.logger.info("batch_hold: node=%s already HELD, skip", nid)
            else:
                workflow.logger.info(
                    "batch_hold: node=%s state=%s (not holdable yet), skip",
                    nid, sm.state.value,
                )

        self._gov_append("BATCH_HOLD", "batch", "user", reason, batch_id=batch_id,
                         payload={"reason": reason, "evidence": evidence,
                                  "node_ids": held_ids, "held_artifacts": held_artifacts})
        self._refresh_governance_cache()
        rec = BatchHoldRecord(
            batch_id=batch_id, held_node_ids=held_ids, reason=reason,
            evidence=evidence, held_artifact_ids=held_artifacts, timestamp=now_ts,
        )
        if self._execution_record is not None:
            self._execution_record.add_reflexion_note(f"BATCH_HOLD: {rec.to_dict()}")
        self._audit_trail.record(
            DecisionType.BATCH_HOLD, batch_id,
            decision=f"batch held ({len(held_ids)} nodes, artifact=fence)",
            rationale=reason, actor="user", now_ts=now_ts,
            context={"batch_hold_record": rec.to_dict()},
        )
        workflow.logger.warning(
            "batch_hold complete: batch=%s held=%d artifacts=%d",
            batch_id, len(held_ids), len(held_artifacts),
        )
        return {"ok": True, "batch_hold_record": rec.to_dict()}

    def _release_hold(self, batch_id: str) -> dict[str, Any]:
        """复查通过: HELD -> COMMITTED, 重新并入 fan-in."""
        if batch_id not in self._held_batches:
            return {"ok": False, "error": f"batch {batch_id} not held"}
        now_ts = workflow.now().timestamp()
        info = self._held_batches[batch_id]
        released = 0
        for nid in info["node_ids"]:
            sm = self._sm(nid)
            if sm.state == NodeState.HELD:
                sm.transition(NodeEvent.RELEASE, batch_id=batch_id)
                _nr = self._node_results.get(nid, {})
                _status = _nr.get("data", {}).get("held_prev_status", "MARGINAL")
                self._node_results[nid] = {**_nr, "status": _status,
                                           "data": {k: v for k, v in (_nr.get("data") or {}).items()
                                                    if k not in ("held", "hold_reason")}}
                if nid not in self._completed_nodes:
                    self._completed_nodes.append(nid)
                released += 1
        info["resolution"] = "released"
        # 找对应 BATCH_HOLD 事件作 supersedes
        _hold_ev = None
        if self._governance_log is not None:
            for ev in reversed(self._governance_log.events):
                if ev.event_type == "BATCH_HOLD" and ev.batch_id == batch_id:
                    _hold_ev = ev.event_id; break
        self._gov_append("RELEASE_HOLD", "batch", "user", "release", batch_id=batch_id,
                         supersedes=_hold_ev)
        self._refresh_governance_cache()
        self._audit_trail.record(
            DecisionType.BATCH_HOLD, batch_id,
            decision=f"batch released ({released} nodes HELD->COMMITTED)",
            rationale="review passed", actor="user", now_ts=now_ts,
        )
        workflow.logger.info("release_hold: batch=%s released=%d", batch_id, released)
        return {"ok": True, "released": released}

    def _rework_batch(self, batch_id: str) -> dict[str, Any]:
        """复查需返工: HELD -> PREPARING, 走 rework_node 下游闭包."""
        if batch_id not in self._held_batches:
            return {"ok": False, "error": f"batch {batch_id} not held"}
        now_ts = workflow.now().timestamp()
        info = self._held_batches[batch_id]
        affected: set[str] = set()
        for nid in info["node_ids"]:
            sm = self._sm(nid)
            if sm.state == NodeState.HELD:
                sm.transition(NodeEvent.REWORK, batch_id=batch_id)
                affected.add(nid)
        # 下游闭包 (复用 rework BFS)
        for nid in list(affected):
            affected |= self._find_downstream_nodes(nid)
        for nid in affected:
            self._node_results.pop(nid, None)
            if nid in self._completed_nodes:
                self._completed_nodes.remove(nid)
            if nid in self._failed_nodes:
                self._failed_nodes.remove(nid)
            self._processed_nodes.discard(nid)
            self._node_attempts[nid] = self._node_attempts.get(nid, 0) + 1
        self._rework_nodes.update(affected)
        for nid in affected:
            self._gov_append("REWORK_NODE", "node", "user",
                             "rework_batch", node_id=nid)
        self._refresh_governance_cache()
        info["resolution"] = "reworked"
        _hold_ev = None
        if self._governance_log is not None:
            for ev in reversed(self._governance_log.events):
                if ev.event_type == "BATCH_HOLD" and ev.batch_id == batch_id:
                    _hold_ev = ev.event_id; break
        self._gov_append("REWORK_BATCH", "batch", "user", "rework batch", batch_id=batch_id,
                         supersedes=_hold_ev)
        self._refresh_governance_cache()
        self._audit_trail.record(
            DecisionType.BATCH_HOLD, batch_id,
            decision=f"batch rework ({len(affected)} nodes HELD->PREPARING+downstream)",
            rationale="review needs rework", actor="user", now_ts=now_ts,
        )
        workflow.logger.info("rework_batch: batch=%s affected=%s", batch_id, sorted(affected))
        return {"ok": True, "rework_nodes": sorted(affected)}

    def _quarantine_batch(self, batch_id: str) -> dict[str, Any]:
        """永久隔离: HELD -> FAILED, 下游通知."""
        if batch_id not in self._held_batches:
            return {"ok": False, "error": f"batch {batch_id} not held"}
        now_ts = workflow.now().timestamp()
        info = self._held_batches[batch_id]
        quarantined: list[str] = []
        notified: list[str] = []
        for nid in info["node_ids"]:
            sm = self._sm(nid)
            if sm.state == NodeState.HELD:
                sm.transition(NodeEvent.QUARANTINE, batch_id=batch_id)
                self._node_results[nid] = {**self._node_results.get(nid, {}),
                                           "status": "QUARANTINED"}
                if nid not in self._failed_nodes:
                    self._failed_nodes.append(nid)
                quarantined.append(nid)
                notified.extend(self._find_affected_downstream(nid))
        info["resolution"] = "quarantined"
        _hold_ev = None
        if self._governance_log is not None:
            for ev in reversed(self._governance_log.events):
                if ev.event_type == "BATCH_HOLD" and ev.batch_id == batch_id:
                    _hold_ev = ev.event_id; break
        self._gov_append("QUARANTINE_BATCH", "batch", "user", "quarantine", batch_id=batch_id,
                         supersedes=_hold_ev)
        self._refresh_governance_cache()
        self._audit_trail.record(
            DecisionType.BATCH_HOLD, batch_id,
            decision=f"batch quarantined ({len(quarantined)} HELD->FAILED, "
                     f"{len(notified)} downstream notified)",
            rationale="permanent quarantine", actor="user", now_ts=now_ts,
        )
        workflow.logger.error("quarantine_batch: batch=%s quarantined=%s notified=%s",
                              batch_id, quarantined, notified)
        return {"ok": True, "quarantined": quarantined, "notified": notified}

    # ── Op-新: pause_scope 分级暂停 ────────────────────────────────
    def _pause_scope(
        self, scope_type: str, scope_id: str, reason: str, initiator: str = "user",
    ) -> dict[str, Any]:
        """分级暂停 - 只挂起指定 scope 的调度, 其余 workflow 继续.

        scope_type:
          workflow -> 退化为现有整 Run pause (写 _pause_state)
          station  -> 暂停单节点(工位): 加 _paused_nodes, 节点执行前阻塞等 resume
          batch    -> 暂停整批(按 case_id): 加 _paused_batches, 该批所有节点阻塞

        不变量: pause 从不删历史, 只挂起调度. resume 从 checkpoint 续跑.
        """
        if self._spec is None:
            return {"ok": False, "error": "no spec"}
        now_ts = workflow.now().timestamp()
        checkpoint = self._current_checkpoint or "init"
        rec = PauseScopeRecord(
            scope_type=scope_type, scope_id=scope_id, reason=reason,
            initiator=initiator, paused_at_checkpoint=checkpoint, timestamp=now_ts,
        )
        affected: list[str] = []
        if scope_type == "workflow":
            # 退化为整 Run pause (复用现有 _pause_state)
            if self._pause_state == "running":
                self._pause_state = "paused"
            workflow.logger.info("pause_scope WORKFLOW: paused entire run")
            affected = [n.node_id for n in self._spec.nodes]
        elif scope_type == "station":
            # 单节点暂停
            if not any(n.node_id == scope_id for n in self._spec.nodes):
                self._audit_trail.record(
                    DecisionType.PAUSE_SCOPE, scope_id,
                    decision="pause_scope STATION REJECTED (node not found)",
                    rationale=reason, actor=initiator, now_ts=now_ts,
                )
                return {"ok": False, "error": f"node {scope_id} not found"}
            self._gov_append("PAUSE_SCOPE", "station", initiator, reason, node_id=scope_id)
            self._refresh_governance_cache()
            affected = [scope_id]
            workflow.logger.info("pause_scope STATION: paused node=%s", scope_id)
        elif scope_type == "batch":
            # 整批暂停 (按 case_id)
            affected = self._nodes_in_batch(scope_id)
            if not affected:
                self._audit_trail.record(
                    DecisionType.PAUSE_SCOPE, scope_id,
                    decision="pause_scope BATCH REJECTED (no nodes in batch)",
                    rationale=reason, actor=initiator, now_ts=now_ts,
                )
                return {"ok": False, "error": f"no nodes in batch {scope_id}"}
            self._gov_append("PAUSE_SCOPE", "batch", initiator, reason, batch_id=scope_id)
            self._refresh_governance_cache()
            workflow.logger.info("pause_scope BATCH: paused batch=%s nodes=%s",
                                 scope_id, affected)
        else:
            return {"ok": False, "error": f"unknown scope_type {scope_type}"}

        # 状态机: 对已启动的节点做 PAUSE 迁移 (影子同步)
        for nid in affected:
            sm = self._sm(nid)
            if sm.state in (NodeState.EXECUTING, NodeState.AWAITING_REVIEW,
                            NodeState.PREPARING):
                if sm.can_transition(NodeEvent.PAUSE):
                    sm.transition(NodeEvent.PAUSE, reason=reason)

        if self._execution_record is not None:
            self._execution_record.add_reflexion_note(f"PAUSE_SCOPE: {rec.to_dict()}")
        self._audit_trail.record(
            DecisionType.PAUSE_SCOPE, scope_id,
            decision=f"pause scope={scope_type} id={scope_id} ({len(affected)} nodes)",
            rationale=reason, actor=initiator, now_ts=now_ts,
            context={"pause_scope_record": rec.to_dict(), "affected": affected},
        )
        return {"ok": True, "pause_scope_record": rec.to_dict(), "affected": affected}

    def _resume_scope(
        self, scope_type: str, scope_id: str,
    ) -> dict[str, Any]:
        """恢复指定 scope 的调度."""
        now_ts = workflow.now().timestamp()
        resumed: list[str] = []
        if scope_type == "workflow":
            if self._pause_state == "paused":
                self._pause_state = "running"
            resumed = [n.node_id for n in self._spec.nodes] if self._spec else []
            workflow.logger.info("resume_scope WORKFLOW: resumed entire run")
        elif scope_type == "station":
            if scope_id in self._paused_nodes:
                # 找本节点 active 的 PAUSE_SCOPE 事件作 supersedes
                _pause_ev = None
                if self._governance_log is not None:
                    for ev in reversed(self._governance_log.events):
                        if ev.event_type == "PAUSE_SCOPE" and ev.node_id == scope_id:
                            _pause_ev = ev.event_id; break
                self._gov_append("RESUME_SCOPE", "station", "system", "resume",
                                 node_id=scope_id, supersedes=_pause_ev)
                self._refresh_governance_cache()
                resumed = [scope_id]
            sm = self._sm(scope_id)
            if sm.state == NodeState.PAUSED and sm.can_transition(NodeEvent.RESUME):
                sm.transition(NodeEvent.RESUME)
            workflow.logger.info("resume_scope STATION: resumed node=%s", scope_id)
        elif scope_type == "batch":
            if scope_id in self._paused_batches:
                _pause_ev = None
                if self._governance_log is not None:
                    for ev in reversed(self._governance_log.events):
                        if ev.event_type == "PAUSE_SCOPE" and ev.batch_id == scope_id:
                            _pause_ev = ev.event_id; break
                self._gov_append("RESUME_SCOPE", "batch", "system", "resume",
                                 batch_id=scope_id, supersedes=_pause_ev)
                self._refresh_governance_cache()
                resumed = self._nodes_in_batch(scope_id)
            for nid in resumed:
                sm = self._sm(nid)
                if sm.state == NodeState.PAUSED and sm.can_transition(NodeEvent.RESUME):
                    sm.transition(NodeEvent.RESUME)
            workflow.logger.info("resume_scope BATCH: resumed batch=%s", scope_id)
        else:
            return {"ok": False, "error": f"unknown scope_type {scope_type}"}

        self._audit_trail.record(
            DecisionType.PAUSE_SCOPE, scope_id,
            decision=f"resume scope={scope_type} id={scope_id} ({len(resumed)} nodes)",
            rationale="resume by user", actor="user", now_ts=now_ts,
        )
        return {"ok": True, "resumed": resumed}

    def _is_node_paused_gov(self, node_id: str, case_id: str | None) -> bool:
        """阶段2: 从治理投影读暂停状态 (替代 _paused_nodes/_paused_batches 直读).

        投影从事件流 reduce, supersedes 链自动处理 pause->resume.
        字段仍双写 (shadow 校验一致性), 投影是执行依据.
        _governance_log 为 None 时 (未初始化) 回退读字段, 保证安全.
        """
        if self._governance_log is None:
            return (node_id in self._paused_nodes
                    or (case_id is not None and case_id in self._paused_batches))
        proj = self._governance_log.project()
        return (node_id in proj.paused_nodes
                or (case_id is not None and case_id in proj.paused_batches))

    def _gov_paused_nodes(self) -> set[str]:
        """阶段2: 从投影读暂停节点集合 (供 query_status/_build_result 暴露)."""
        if self._governance_log is None:
            return self._paused_nodes
        return self._governance_log.project().paused_nodes

    def _gov_paused_batches(self) -> set[str]:
        """阶段2: 从投影读暂停批次集合 (供 query_status/_build_result 暴露)."""
        if self._governance_log is None:
            return self._paused_batches
        return self._governance_log.project().paused_batches

    def _gov_held_batches(self) -> dict[str, dict[str, Any]]:
        """阶段3: 从投影读扣留批次字典 (供 query_status/_build_result 暴露)."""
        if self._governance_log is None:
            return self._held_batches
        return self._governance_log.project().held_batches

    def _gov_injected_context(self) -> dict[str, Any]:
        """阶段3: 从投影读注入上下文 (供 query_status 暴露)."""
        if self._governance_log is None:
            return self._injected_context
        return self._governance_log.project().injected_context

    def _gov_review_results(self) -> dict[str, dict[str, Any]]:
        """阶段3: 从投影读审核结果 (供 query_status 暴露)."""
        if self._governance_log is None:
            return self._review_results
        return self._governance_log.project().review_results

    def _find_last_active_event(self, event_type: str, node_id: str) -> str | None:
        """找节点最近一个未被 supersede 的指定类型事件 ID (供 supersedes 引用)."""
        if self._governance_log is None:
            return None
        superseded = {ev.supersedes for ev in self._governance_log.events
                      if ev.supersedes is not None}
        for ev in reversed(self._governance_log.events):
            if (ev.event_type == event_type and ev.node_id == node_id
                    and ev.event_id not in superseded):
                return ev.event_id
        return None

    def _compute_spec_hash(self) -> str:
        """计算当前 WorkflowSpec 的 hash (用于版本追踪)."""
        if self._spec is None:
            return ""
        import hashlib, json
        spec_dict = workflow_spec_to_dict(self._spec)
        spec_str = json.dumps(spec_dict, sort_keys=True, default=str)
        return hashlib.sha256(spec_str.encode()).hexdigest()[:16]

    def _record_spec_revision(self, reason: str, actor: str = "user") -> None:
        """记录工作流 spec 版本更迭到治理事件流."""
        spec_hash = self._compute_spec_hash()
        self._gov_append("SPEC_REVISION", "workflow", actor, reason,
                         payload={"spec_hash": spec_hash, "reason": reason})
        self._refresh_governance_cache()

    def _refresh_governance_cache(self) -> None:
        """阶段3: 从投影刷新治理缓存字段.

        治理模块不再直接 add/discard 字段, 只 append 事件, 然后调此方法
        让字段从投影同步. 字段降级为投影的缓存 (双写 -> 投影驱动).
        paused_nodes/paused_batches/held_batches 已切换; rework_nodes 等
        仍双写 (执行引擎临时修改, 语义不同).
        """
        if self._governance_log is None:
            return
        proj = self._governance_log.project()
        self._paused_nodes = proj.paused_nodes
        self._paused_batches = proj.paused_batches
        self._held_batches = proj.held_batches
        self._injected_context = proj.injected_context
        self._review_results = proj.review_results
        self._rework_nodes = proj.rework_nodes

    async def _check_node_pause(self, node_id: str, case_id: str | None) -> None:
        """节点执行前检查: 若被 STATION 或 BATCH 级暂停, 阻塞等 resume.

        与整 Run 暂停(_pause_state)的区别: 这里只阻塞本节点, 同 Run 其它
        节点继续推进. pause 从不删历史, resume 后从 checkpoint 续跑.

        阶段2: 读路径切到治理投影 (原读 _paused_nodes/_paused_batches).
        """
        while self._is_node_paused_gov(node_id, case_id):
            _scope = ("station" if node_id in self._gov_paused_nodes()
                      else f"batch={case_id}")
            workflow.logger.info(
                "Node %s paused (scope: %s), waiting for resume_scope",
                node_id, _scope,
            )
            await self._emit_event(
                "node_paused", node_id=node_id, node_status="PAUSED_SCOPE",
            )
            timed_out = False
            try:
                await workflow.wait_condition(
                    lambda: not self._is_node_paused_gov(node_id, case_id),
                    timeout=timedelta(minutes=30),
                )
            except Exception:
                timed_out = True
            if timed_out:
                workflow.logger.error(
                    "Node %s pause_scope timeout (30m) -> skipping node",
                    node_id,
                )
                return
            await self._emit_event("node_resumed", node_id=node_id)

    # ── Op-新: relabel_request 重新标注 ────────────────────────────
    def _relabel_request(
        self, node_id: str, artifact_id: str,
        original_label: str, corrected_label: str, reason: str,
    ) -> dict[str, Any]:
        """重新标注 - 标签错但执行结果对, 只回标注环节, 不重跑分析.

        是 rework_node 的标签专项版. 与普通 rework 区别:
          - 只重跑产出 label 的 Annot 节点(及依赖 label 的下游)
          - 复用既有执行结果(reused_result_artifact_id), 不重跑昂贵分析(IQA/PPA)
          - 新 label = 新 ArtifactVersion, provenance 保留

        选择性下游: 系统无 label-vs-result 精细依赖, 保守按 capability 区分 -
          capability="annotation" 的节点是 label 产出方, 其它下游默认可能依赖 label.
          若节点 metadata 标注 depends_on_label_only=False 则跳过(仅依赖 result).
        """
        if self._spec is None:
            return {"ok": False, "error": "no spec"}
        now_ts = workflow.now().timestamp()

        # 校验目标节点存在
        target = next((n for n in self._spec.nodes if n.node_id == node_id), None)
        if target is None:
            self._audit_trail.record(
                DecisionType.RELABEL_REQUEST, node_id,
                decision="relabel REJECTED (node not found)",
                rationale=reason, actor="user", now_ts=now_ts,
            )
            return {"ok": False, "error": f"node {node_id} not found"}

        # 前置条件: 标签错但结果对 (由调用方保证, 这里记录假定)
        reused_result = f"{self._spec.workflow_id}:{node_id}:result_v1"

        # 选择性下游: BFS 找下游闭包, 跳过标注了"仅依赖 result"的节点
        all_downstream = self._find_downstream_nodes(node_id)
        affected: list[str] = []
        skipped: list[str] = []
        for nid in sorted(all_downstream):
            dn = next((n for n in self._spec.nodes if n.node_id == nid), None)
            if dn is None:
                continue
            # metadata.depends_on_label_only=False -> 仅依赖 result, 标签变不影响, 跳过
            meta = dn.input.get("metadata", {}) if isinstance(dn.input, dict) else {}
            if isinstance(meta, dict) and meta.get("depends_on_label_only") is False:
                skipped.append(nid)
            else:
                affected.append(nid)

        # 定向 rework: Annot 节点本身 + 受影响下游 (复用现有 rework 机制)
        rework_set = {node_id} | set(affected)
        for nid in rework_set:
            self._node_results.pop(nid, None)
            if nid in self._completed_nodes:
                self._completed_nodes.remove(nid)
            if nid in self._failed_nodes:
                self._failed_nodes.remove(nid)
            self._processed_nodes.discard(nid)
            self._node_attempts[nid] = self._node_attempts.get(nid, 0) + 1
        self._rework_nodes.update(rework_set)
        for nid in rework_set:
            self._gov_append("REWORK_NODE", "node", "user",
                             f"relabel from {node_id}", node_id=nid)
        self._refresh_governance_cache()

        # 状态机: 标记 Annot 节点走 REWORK 迁移 (若当前态允许)
        sm = self._sm(node_id)
        if sm.can_transition(NodeEvent.REWORK):
            sm.transition(NodeEvent.REWORK, reason=reason)

        rec = RelabelRecord(
            node_id=node_id, artifact_id=artifact_id,
            original_label=original_label, corrected_label=corrected_label,
            reused_result_artifact_id=reused_result,
            affected_downstream=affected, reason=reason, timestamp=now_ts,
        )
        if self._execution_record is not None:
            self._execution_record.add_reflexion_note(f"RELABEL: {rec.to_dict()}")
        self._audit_trail.record(
            DecisionType.RELABEL_REQUEST, node_id,
            decision=f"relabel (label: {original_label!r}->{corrected_label!r}, "
                    f"{len(affected)} downstream rework, {len(skipped)} reused result)",
            rationale=reason, actor="user", now_ts=now_ts,
            context={"relabel_record": rec.to_dict(), "skipped_result_only": skipped},
        )
        workflow.logger.info(
            "relabel_request: node=%s label %r->%r, rework=%s reused=%s",
            node_id, original_label, corrected_label, sorted(rework_set), skipped,
        )
        self._gov_append("RELABEL_REQUEST", "node", "user", reason, node_id=node_id,
                         payload={"original_label": original_label, "corrected_label": corrected_label})

        return {"ok": True, "relabel_record": rec.to_dict(),
                "rework_nodes": sorted(rework_set), "skipped_result_only": skipped}

    # ── Op-新: delegate_review 审查权转移 ────────────────────────────
    def _delegate_review(
        self, target: str, new_reviewer_id: str, reason: str,
        is_escalation: bool = False,
    ) -> dict[str, Any]:
        """审查权转移 - REQUIRED 分支"谁来 review"变更.

        管 human_review 的"who"维度. 更新 target 的 reviewer 分配,
        重路由 pending review 给新 reviewer. 已 COMPLETED 决策不可追溯改(不可变).

        target: node_id (具体节点) 或 node_type (该类型所有节点)
        """
        if self._spec is None:
            return {"ok": False, "error": "no spec"}
        now_ts = workflow.now().timestamp()

        # 解析 target -> 受影响的 node_id 列表
        affected_nodes: list[str] = []
        if any(n.node_id == target for n in self._spec.nodes):
            affected_nodes = [target]
        else:
            # target 当 node_type 处理
            affected_nodes = [n.node_id for n in self._spec.nodes if n.type == target]
        if not affected_nodes:
            self._audit_trail.record(
                DecisionType.DELEGATE_REVIEW, target,
                decision="delegate REJECTED (no matching nodes)",
                rationale=reason, actor="user", now_ts=now_ts,
            )
            return {"ok": False, "error": f"no nodes match target {target}"}

        # 收集 pending review (处于 _review_waiting 的)
        rerouted: list[str] = [nid for nid in affected_nodes if nid in self._review_waiting]
        old_reviewer = ""
        for nid in affected_nodes:
            old = self._reviewer_assignments.get(nid, "default_reviewer")
            if nid == affected_nodes[0]:
                old_reviewer = old
            self._reviewer_assignments[nid] = new_reviewer_id

        rec = DelegateReviewRecord(
            target=target, old_reviewer_id=old_reviewer,
            new_reviewer_id=new_reviewer_id,
            rerouted_pending_review_ids=rerouted, reason=reason,
            is_escalation=is_escalation, timestamp=now_ts,
        )
        if self._execution_record is not None:
            self._execution_record.add_reflexion_note(f"DELEGATE: {rec.to_dict()}")
        self._audit_trail.record(
            DecisionType.DELEGATE_REVIEW, target,
            decision=f"delegate {old_reviewer!r}->{new_reviewer_id!r} "
                    f"({len(affected_nodes)} nodes, {len(rerouted)} rerouted)",
            rationale=reason, actor="user", now_ts=now_ts,
            context={"delegate_record": rec.to_dict(), "affected_nodes": affected_nodes},
        )
        workflow.logger.info(
            "delegate_review: target=%s %s->%s (rerouted=%d)",
            target, old_reviewer, new_reviewer_id, len(rerouted),
        )
        for _nid in affected_nodes:
            self._gov_append("DELEGATE_REVIEW", "node", "user", reason, node_id=_nid)

        return {"ok": True, "delegate_record": rec.to_dict(),
                "affected_nodes": affected_nodes}

    # ── Op-新[M]: standard_update 标准库更新 ──────────────────────────
    def _standard_update(
        self, standard_id: str, old_version: str, new_version: str,
        effective_date: str = "", diff: str = "",
    ) -> dict[str, Any]:
        """标准库版本更新 -> 扫描受影响历史判定, 分类标记.

        [M]治理类: 核心影响扫描跨workflow(离线). 本方法做当前workflow范围内
        的扫描: 扫audit_trail里context.standard_version==old_version的
        DecisionRecord(由commit阶段记入), 分类(STABLE/AT_RISK/OBSOLETE).

        跨workflow历史扫描(影响所有曾用旧版本的历史workflow): 需L4 knowledge
        层的离线replay聚合所有workflow的execution_record/audit_trail.
        当前系统audit_trail是in-memory单workflow实例, 无持久化聚合层 -> 留TODO.
        出口: AT_RISK决策路由人工复查(delegate_review)或revoke_approval.
        """
        now_ts = workflow.now().timestamp()
        affected: list[dict[str, Any]] = []
        for rec in self._audit_trail._records:
            ctx = rec.context or {}
            sv = ctx.get("standard_version", "")
            if sv == old_version:
                # 简化分类: 有 diff 标 AT_RISK(条款变), 无 diff 标 STABLE
                cls = "AT_RISK" if diff else "STABLE"
                affected.append({"record_id": rec.record_id,
                                 "node_id": rec.node_id, "classification": cls})

        rec_out = StandardUpdateRecord(
            standard_id=standard_id, old_version=old_version,
            new_version=new_version, effective_date=effective_date,
            diff=diff, affected_decisions=affected, timestamp=now_ts,
        )
        if self._execution_record is not None:
            self._execution_record.add_reflexion_note(f"STANDARD_UPDATE: {rec_out.to_dict()}")
        self._audit_trail.record(
            DecisionType.STANDARD_UPDATE, standard_id,
            decision=f"standard {old_version}->{new_version} "
                    f"({len(affected)} affected decisions)",
            rationale=diff[:200] or "version upgrade", actor="user", now_ts=now_ts,
            context={"standard_update_record": rec_out.to_dict()},
        )
        workflow.logger.warning(
            "standard_update: %s %s->%s, %d affected",
            standard_id, old_version, new_version, len(affected),
        )
        self._gov_append("STANDARD_UPDATE", "workflow", "user", f"{old_version}->{new_version}")

        return {"ok": True, "standard_update_record": rec_out.to_dict()}

    # ── Op-新[M]: case_library_correction 案例库纠错 ───────────────────
    def _case_library_correction(
        self, case_id: str, error_type: str, correction: str,
    ) -> dict[str, Any]:
        """CBR案例纠错 -> 扫描引用该案例的决策, 标记复查.

        [M]治理类: 影响面>rework_node(跨workflow引用图, 跨时间). 本方法做
        当前workflow范围扫描: 扫audit_trail里context.case_id==case_id的
        DecisionRecord(由commit阶段记入caller_context.case_id).

        跨workflow引用图扫描(影响所有曾检索该案例的workflow): 需L4 knowledge
        层的Memory Store聚合所有workflow的引用记录. 当前系统无此聚合层 -> 留TODO.
        影响图: case -> 引用workflow -> 其决策 -> 其artifact (远宽于单DAG).
        """
        now_ts = workflow.now().timestamp()
        affected: list[dict[str, Any]] = []
        for rec in self._audit_trail._records:
            ctx = rec.context or {}
            if ctx.get("case_id") == case_id or case_id in str(ctx.get("cases", [])):
                affected.append({"record_id": rec.record_id, "node_id": rec.node_id})

        rec_out = CaseCorrectionRecord(
            case_id=case_id, error_type=error_type, correction=correction,
            affected_workflows=[self._spec.workflow_id] if self._spec else [],
            affected_decisions=affected, timestamp=now_ts,
        )
        if self._execution_record is not None:
            self._execution_record.add_reflexion_note(f"CASE_CORRECTION: {rec_out.to_dict()}")
        self._audit_trail.record(
            DecisionType.CASE_LIBRARY_CORRECTION, case_id,
            decision=f"case corrected ({error_type}->{correction}, "
                    f"{len(affected)} affected decisions)",
            rationale=error_type, actor="user", now_ts=now_ts,
            context={"case_correction_record": rec_out.to_dict()},
        )
        workflow.logger.warning(
            "case_library_correction: case=%s %s->%s, %d affected",
            case_id, error_type, correction, len(affected),
        )
        self._gov_append("CASE_LIBRARY_CORRECTION", "workflow", "user", error_type)

        return {"ok": True, "case_correction_record": rec_out.to_dict()}

    # ── Op 25: Token budget tracking ───────────────────────────

    def _emit_progress(
        self, node_id: str, progress: float, stage: str,
        partial: dict[str, Any] | None = None,
    ) -> None:
        """Op-新: heartbeat 流式 - 节点执行各阶段进度上报.

        节点级流式 (非 Activity 内 token 级): _execute_node_v2 四阶段边界
        (prepare/execute/validate/commit) 各报一次进度, 供 query_status 实时查,
        前端可显示"n1: validate 阶段 85%". 复用 StreamingUpdate 基建.
        """
        self._current_node = node_id
        self._current_stage = stage
        self._current_progress = progress
        if self._execution_record is not None:
            self._execution_record.add_streaming_update(StreamingUpdate(
                node_id=node_id, progress=progress, stage=stage,
                partial_result=partial, timestamp=workflow.now().timestamp(),
            ))
        workflow.logger.info(
            "heartbeat: %s %s %.0f%%", node_id, stage, progress * 100,
        )

    def _track_token_usage(self, node_id: str, result: dict[str, Any]) -> None:
        """Op 25: 跟踪节点执行的 token 用量。

        Source: Claude Code token budget + Anthropic prompt caching cost control.

        从节点结果中提取 token 用量，累加到 _token_budget_used。
        超过预算 80% 时发出警告，超过 100% 时标记降级。
        """
        data = result.get("data") or {}
        if isinstance(data, dict):
            tokens = data.get("tokens_used", 0)
            if isinstance(tokens, (int, float)) and tokens > 0:
                self._token_budget_used += int(tokens)

        ratio = self._token_budget_used / self._token_budget_limit if self._token_budget_limit > 0 else 0
        if ratio >= 1.0:
            workflow.logger.warning(
                "Token budget EXCEEDED: %d/%d (%.0f%%) - downstream nodes may degrade",
                self._token_budget_used, self._token_budget_limit, ratio * 100,
            )
        elif ratio >= 0.8:
            workflow.logger.info(
                "Token budget approaching limit: %d/%d (%.0f%%)",
                self._token_budget_used, self._token_budget_limit, ratio * 100,
            )

    def _is_budget_exceeded(self) -> bool:
        """Op 25: 检查 token 预算是否超限。"""
        return self._token_budget_used >= self._token_budget_limit

    # ── Op 37: Batch signal processing ─────────────────────────

    @workflow.signal
    def batch_signals(self, signals: list[dict[str, Any]]) -> None:
        """Op 37: 批量应用信号 - 在下一个 checkpoint 一起处理。

        Source: 批量信号处理 - 在 checkpoint 处批量应用信号。

        每个 signal 是 {"type": str, "args": [...]}
        type 可以是: pause, resume, cancel, inject_context, rework_node, human_review
        """
        for sig in signals:
            if not isinstance(sig, dict):
                continue
            self._pending_signals.append(sig)

    def _apply_pending_signals(self) -> None:
        """Op 37: 在 checkpoint 处批量应用累积的信号。

        在每个节点执行前后调用，确保用户的批量操作
        能在合适的时机被处理。
        """
        if not self._pending_signals:
            return

        signals = self._pending_signals[:]
        self._pending_signals.clear()

        for sig in signals:
            sig_type = sig.get("type", "")
            args = sig.get("args", [])

            if sig_type == "pause" and self._pause_state == "running":
                self._pause_state = "paused"
                workflow.logger.info("Batch signal: pause")
            elif sig_type == "resume" and self._pause_state == "paused":
                self._pause_state = "running"
                workflow.logger.info("Batch signal: resume")
            elif sig_type == "cancel":
                self._pause_state = "cancelled"
                workflow.logger.info("Batch signal: cancel")
            elif sig_type == "inject_context" and len(args) >= 2:
                key, value = args[0], args[1]
                if isinstance(key, str) and key:
                    self._gov_append("INJECT_CONTEXT", "workflow", "user", "inject",
                                     payload={"key": key, "value": value})
                    self._refresh_governance_cache()
                    workflow.logger.info("Batch signal: inject_context key=%s", key)
            elif sig_type == "rework_node" and len(args) >= 1:
                node_id = args[0]
                # 调用 rework_node 的逻辑（同步部分）
                affected = self._find_downstream_nodes(node_id)
                affected.add(node_id)
                for nid in affected:
                    self._node_results.pop(nid, None)
                    if nid in self._completed_nodes:
                        self._completed_nodes.remove(nid)
                    if nid in self._failed_nodes:
                        self._failed_nodes.remove(nid)
                    self._processed_nodes.discard(nid)
                    self._node_attempts[nid] = self._node_attempts.get(nid, 0) + 1
                self._rework_nodes.update(affected)
                for nid in affected:
                    self._gov_append("REWORK_NODE", "node", "user",
                                     f"rework from {node_id}", node_id=nid)
                self._refresh_governance_cache()
                workflow.logger.info("Batch signal: rework_node=%s affected=%s", node_id, sorted(affected))
            elif sig_type == "human_review" and len(args) >= 2:
                node_id, result = args[0], args[1]
                _stored = result if isinstance(result, dict) else {"decision": "approve"}
                self._gov_append("HUMAN_REVIEW", "node", "user", "review",
                                 node_id=node_id, payload=_stored)
                self._refresh_governance_cache()
                workflow.logger.info("Batch signal: human_review node=%s", node_id)
            elif sig_type == "modify_spec" and len(args) >= 1:
                self.modify_spec(args[0])
                workflow.logger.info("Batch signal: modify_spec")
            elif sig_type == "revoke_approval":
                node_id = sig.get("node_id") or (args[0] if args else "")
                if node_id:
                    self._revoke_approval(
                        node_id,
                        revoker_id=sig.get("revoker_id", "unknown"),
                        reason=sig.get("reason", ""),
                        artifact_status=sig.get("artifact_status", "draft"),
                    )
                    workflow.logger.info(
                        "Batch signal: revoke_approval node=%s", node_id,
                    )
            elif sig_type == "batch_hold":
                self._batch_hold(
                    sig.get("batch_id", ""),
                    reason=sig.get("reason", ""),
                    evidence=sig.get("evidence", ""),
                    node_ids=sig.get("node_ids"),
                )
                workflow.logger.info("Batch signal: batch_hold=%s", sig.get("batch_id"))
            elif sig_type == "release_hold":
                self._release_hold(sig.get("batch_id", ""))
                workflow.logger.info("Batch signal: release_hold=%s", sig.get("batch_id"))
            elif sig_type == "rework_batch":
                self._rework_batch(sig.get("batch_id", ""))
                workflow.logger.info("Batch signal: rework_batch=%s", sig.get("batch_id"))
            elif sig_type == "quarantine_batch":
                self._quarantine_batch(sig.get("batch_id", ""))
                workflow.logger.info("Batch signal: quarantine_batch=%s", sig.get("batch_id"))
            elif sig_type == "pause_scope":
                self._pause_scope(
                    sig.get("scope_type", "station"),
                    sig.get("scope_id", ""),
                    sig.get("reason", ""), sig.get("initiator", "user"),
                )
                workflow.logger.info("Batch signal: pause_scope %s/%s",
                                     sig.get("scope_type"), sig.get("scope_id"))
            elif sig_type == "resume_scope":
                self._resume_scope(
                    sig.get("scope_type", "station"), sig.get("scope_id", ""),
                )
                workflow.logger.info("Batch signal: resume_scope %s/%s",
                                     sig.get("scope_type"), sig.get("scope_id"))
            elif sig_type == "relabel_request":
                self._relabel_request(
                    sig.get("node_id", ""), sig.get("artifact_id", ""),
                    sig.get("original_label", ""), sig.get("corrected_label", ""),
                    sig.get("reason", ""),
                )
                workflow.logger.info("Batch signal: relabel_request node=%s",
                                     sig.get("node_id"))
            elif sig_type == "delegate_review":
                self._delegate_review(
                    sig.get("target", ""), sig.get("new_reviewer_id", ""),
                    sig.get("reason", ""), sig.get("is_escalation", False),
                )
                workflow.logger.info("Batch signal: delegate_review target=%s",
                                     sig.get("target"))
            elif sig_type == "standard_update":
                self._standard_update(
                    sig.get("standard_id", ""), sig.get("old_version", ""),
                    sig.get("new_version", ""), sig.get("effective_date", ""),
                    sig.get("diff", ""),
                )
                workflow.logger.info("Batch signal: standard_update %s",
                                     sig.get("standard_id"))
            elif sig_type == "case_library_correction":
                self._case_library_correction(
                    sig.get("case_id", ""), sig.get("error_type", ""),
                    sig.get("correction", ""),
                )
                workflow.logger.info("Batch signal: case_library_correction %s",
                                     sig.get("case_id"))

        workflow.logger.info("Batch signals applied: %d signals", len(signals))


    # ── Op 7: NodeAttempt 四阶段 + Op 34: LLM 评估器 ──────────
    # Source: 数据库事务两阶段提交 (draft->commit) + Temporal Activity 生命周期
    # Op 34: Anthropic "Building Effective Agents" evaluator-optimizer
    #
    # 审计建议: 先加 _execute_node_v2 并行实现, 验证后切换.
    # 当前主循环仍用原逻辑; 此方法供未来切换.

    # ── 节点状态机影子同步 (zero-risk: 只同步不决策) ────────────────
    def _sm(self, node_id: str) -> NodeStateMachine:
        """获取/创建节点的状态机实例."""
        if node_id not in self._node_sm:
            self._node_sm[node_id] = NodeStateMachine(node_id)
        return self._node_sm[node_id]

    def _sm_transition(self, node_id: str, event: NodeEvent, **ctx: Any) -> NodeState:
        """影子同步: 推进状态机并记审计. 不影响现有决策逻辑."""
        return self._sm(node_id).transition(event, **ctx)

    def _assert_sm_consistency(self) -> list[dict[str, Any]]:
        """build_result 末尾断言: 状态机态与列表推断一致.

        不一致只记 warning (不抛, 避免阻断 workflow) - 偏差是 TODO 信号.
        收集 mismatches 到 self._sm_mismatches, 供 _build_result 暴露给测试侧硬断言.
        Returns: mismatch 列表 (空 = 一致).
        """
        mismatches: list[dict[str, Any]] = []
        for node_id, sm in self._node_sm.items():
            list_state = self._infer_state_from_lists(node_id)
            if list_state != sm.state:
                workflow.logger.warning(
                    "STATE MISMATCH: node=%s list_inferred=%s state_machine=%s "
                    "(shadow mode - investigate)",
                    node_id, list_state.value, sm.state.value,
                )
                mismatches.append({
                    "node_id": node_id,
                    "list_inferred": list_state.value,
                    "state_machine": sm.state.value,
                })
        self._sm_mismatches = mismatches
        return mismatches

    def _infer_state_from_lists(self, node_id: str) -> NodeState:
        """从现有列表推断节点状态 (影子对照基准)."""
        if node_id in self._rework_nodes:
            return NodeState.PREPARING
        if self._pause_state == "paused":
            return NodeState.PAUSED
        # Op-新: STATION/BATCH 级 pause_scope (只该节点 PAUSED)
        if node_id in self._paused_nodes:
            return NodeState.PAUSED
        if self._spec:
            _cid = next((n.caller_context.case_id for n in self._spec.nodes
                         if n.node_id == node_id), None)
            if _cid and _cid in self._paused_batches:
                return NodeState.PAUSED
        if node_id in self._completed_nodes:
            return NodeState.COMMITTED
        _nr = self._node_results.get(node_id, {})
        # HELD / REVOKED 都可能被移入 _failed_nodes, 先按 status 精确判 (避免误判 FAILED)
        if isinstance(_nr, dict):
            _st = str(_nr.get("status", "")).upper()
            if _st == "HELD":
                return NodeState.HELD
            if _st == "REVOKED":
                return NodeState.REVOKED
        if node_id in self._failed_nodes:
            return NodeState.FAILED
        if node_id in self._processed_nodes:
            return NodeState.AWAITING_REVIEW
        if node_id in self._node_results:
            return NodeState.AWAITING_REVIEW
        return NodeState.PENDING

    async def _execute_node_v2(
        self, node: Any, dep_results: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        """Op 7: Four-phase node execution (Prepare/Execute/Validate/Commit).

        Prepare:  build input, check deps, generate idempotency key
        Execute:  call execute_node activity (Temporal RetryPolicy retries 3x)
        Validate: schema check + Op 34 LLM quality evaluation
        Commit:   store result, update tracking, emit events
        """
        wf_id = self._spec.workflow_id if self._spec else ""

        # ── Phase 1: Prepare ──
        # 幂等守卫: rework 重跑时 SM 已在 PREPARING (上轮残留), START 会非法迁移
        # 被拒+记噪声 -> 仅 PENDING 态才发 START (视为首次 prepare)
        if self._sm(node.node_id).state == NodeState.PENDING:
            self._sm_transition(node.node_id, NodeEvent.START, phase="prepare")
        attempt_num = self._node_attempts.get(node.node_id, 0)
        idempotency_key = f"{wf_id}:{node.node_id}:{attempt_num}"

        node_input = {
            "node_id": node.node_id,
            "type": node.type,
            "capability": node.capability,
            "input": node.input,
            "workflow_id": wf_id,
            "idempotency_key": idempotency_key,
            "dependency_results": dep_results,
            "caller_context": {
                "caller_type": node.caller_context.caller_type,
                "case_id": node.caller_context.case_id,
                "node_id": node.caller_context.node_id,
                "session_id": node.caller_context.session_id,
            },
            "phase": "prepare",
        }
        if node.type == "human_task" and node.node_id in self._review_results:
            node_input["review_result"] = self._review_results.get(node.node_id, {})
        if self._injected_context:
            node_input["input"] = {**node_input.get("input", {}), **self._injected_context}

        self._emit_progress(node.node_id, 0.2, "prepare_done")
        # ── Phase 2: Execute ──
        self._sm_transition(node.node_id, NodeEvent.READY, phase="execute")
        node_input["phase"] = "execute"
        try:
            result = await workflow.execute_activity(
                "execute_node", node_input,
                schedule_to_start_timeout=timedelta(seconds=30),
                start_to_close_timeout=timedelta(seconds=60),
                schedule_to_close_timeout=timedelta(minutes=3),
                retry_policy=RetryPolicy(
                    maximum_attempts=3,
                    initial_interval=timedelta(seconds=1),
                    backoff_coefficient=2.0,
                ),
            )
        except Exception as e:
            result = {"status": "ERROR", "data": {}, "error": f"Activity failed: {e}"}

        self._emit_progress(node.node_id, 0.6, "execute_done",
                             partial=None)
        # ── Phase 3: Validate ──
        # Op 34: LLM quality evaluation (beyond schema check)
        # Only evaluate if node succeeded and is not a human_task
        # 影子同步: execute 完 -> AWAITING_REVIEW (即便后面 rework, 也会记 EXEC_DONE)
        exec_status = str(result.get("status", "ERROR")).upper()
        if exec_status in ("ERROR",) :
            self._sm_transition(node.node_id, NodeEvent.FAIL, phase="validate", status=exec_status)
        else:
            self._sm_transition(node.node_id, NodeEvent.EXEC_DONE, phase="validate", status=exec_status)
        status = str(result.get("status", "ERROR")).upper()
        if status in ("OK", "MARGINAL") and node.type != "human_task":
            validation = await self._validate_node_result(node, result)
            if validation.get("quality_score") is not None:
                result.setdefault("data", {})["quality_score"] = validation["quality_score"]
                if validation.get("quality_note"):
                    result["data"]["quality_note"] = validation["quality_note"]
                # If quality too low, downgrade MARGINAL to needs-review
                if validation["quality_score"] < 0.5 and status == "OK":
                    result["status"] = "MARGINAL"
                    workflow.logger.info(
                        "Node %s: quality_score=%.2f < 0.5 -> downgraded to MARGINAL",
                        node.node_id, validation["quality_score"],
                    )

        self._emit_progress(node.node_id, 0.85, "validate_done")
        # ── Phase 4: Commit ──
        final_for_commit = str(result.get("status", "ERROR")).upper()
        if final_for_commit in ("OK", "MARGINAL"):
            self._sm_transition(node.node_id, NodeEvent.APPROVE, phase="commit", status=final_for_commit)
        else:
            self._sm_transition(node.node_id, NodeEvent.FAIL, phase="commit", status=final_for_commit)

        # Op-新: commit 阶段记 audit, 含 standard_version + case_id (provenance).
        # 闭环 standard_update/case_library_correction 的历史影响扫描 (现机制就位, 有数据喂).
        _meta = node.input.get("metadata", {}) if isinstance(node.input, dict) else {}
        _sv = _meta.get("standard_version", "") if isinstance(_meta, dict) else ""
        _cid = node.caller_context.case_id or ""
        self._audit_trail.record(
            DecisionType.NODE_REVIEW, node.node_id,
            decision=f"commit status={final_for_commit}",
            rationale=f"phase4 commit (attempt={attempt_num})",
            actor="system", now_ts=workflow.now().timestamp(),
            context={"standard_version": _sv, "case_id": _cid,
                     "capability": node.capability, "commit_status": final_for_commit},
        )
        self._node_results[node.node_id] = result
        self._record_saga_compensation(node.node_id, result)
        self._track_token_usage(node.node_id, result)

        # Emit node_end event
        emit_data = {}
        if isinstance(result.get("data"), dict):
            emit_data = {
                k: v for k, v in result["data"].items()
                if k not in ("image_b64", "image_paths", "raw_image")
                and not str(v).startswith("data:")
            }
        await self._emit_event(
            "node_end",
            node_id=node.node_id,
            node_status=result.get("status", "ERROR"),
            node_data=emit_data,
            error=result.get("error"),
        )

        # Track mock nodes
        result_data = result.get("data") or {}
        if isinstance(result_data, dict) and result_data.get("mock") is True:
            self._mocked_nodes.append(node.node_id)

        # Update completion tracking
        final_status = str(result.get("status", "ERROR")).upper()

        # Op 28: Record streaming update for SSE delivery
        if self._execution_record is not None:
            self._execution_record.add_streaming_update(StreamingUpdate(
                node_id=node.node_id,
                progress=1.0 if final_status in ("OK", "MARGINAL") else 0.0,
                stage="dispatch_complete",
                partial_result=emit_data,
                timestamp=workflow.now().timestamp(),
            ))

        if final_status in ("OK", "MARGINAL"):
            self._completed_nodes.append(node.node_id)
        else:
            self._failed_nodes.append(node.node_id)
        self._processed_nodes.add(node.node_id)

        # Op 29: Record node execution for audit/learning
        if self._execution_record is not None:
            quality = 0.0
            if isinstance(result.get("data"), dict):
                quality = result["data"].get("quality_score", 0.0)
            self._execution_record.add_node_record(NodeExecutionRecord(
                node_id=node.node_id,
                capability=node.capability,
                status=final_status,
                attempt=self._node_attempts.get(node.node_id, 0),
                quality_score=quality,
                result_data=emit_data,
                error=result.get("error"),
                timestamp=workflow.now().timestamp(),
            ))

        return result

    async def _validate_node_result(
        self, node: Any, result: dict[str, Any]
    ) -> dict[str, Any]:
        """Op 34: Validate node result - schema check + LLM quality evaluation.

        Source: Anthropic "Building Effective Agents" (2024) evaluator-optimizer.
        LLM generates -> LLM evaluates -> optimize if needed.

        两阶段:
          1. 结构校验 (快速 pre-check): 明显缺字段直接返回低分, 不浪费 LLM
          2. LLM 语义评估 (Op 34): 调 evaluate_node_quality activity 拿真分
             - activity 内部失败/无 LLM 配置 -> 回退启发式
        """
        validation: dict[str, Any] = {}

        # Structural validation
        has_status = "status" in result
        has_data = "data" in result
        status = str(result.get("status", "")).upper()

        if not has_status:
            validation["quality_score"] = 0.0
            validation["quality_note"] = "missing status field"
            return validation

        if not has_data:
            validation["quality_score"] = 0.3
            validation["quality_note"] = "missing data field"
            return validation

        # Heuristic quality score based on result completeness
        data = result.get("data") or {}
        if not isinstance(data, dict):
            validation["quality_score"] = 0.4
            validation["quality_note"] = "data is not a dict"
            return validation

        # 启发式兜底分 (activity 内部 LLM 失败时用这个)
        score = 0.3  # base (lower to penalize empty/thin data)
        if status:
            score += 0.1
        if len(data) >= 2:
            score += 0.2  # rich data
        elif len(data) == 1:
            score += 0.1  # minimal data
        if not result.get("error"):
            score += 0.1
        if data.get("mock"):
            score -= 0.2  # mock penalty
        score = max(0.0, min(1.0, score))

        # ── Op 34: 调 evaluate_node_quality activity 拿真 LLM 语义评估 ──
        # workflow 里不能直接调 LLM (determinism), 走 activity.
        # activity 失败/无 LLM 配置时回退上面的启发式 score.
        objective = self._spec.objective if self._spec else ""
        try:
            eval_result = await workflow.execute_activity(
                "evaluate_node_quality",
                {
                    "node_id": node.node_id,
                    "capability": node.capability or "",
                    "status": status,
                    "result_data": data,
                    "objective": objective,
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(
                    maximum_attempts=1,  # 评估失败不重试, 用启发式兜底
                    initial_interval=timedelta(seconds=1),
                ),
            )
            validation["quality_score"] = float(eval_result.get("quality_score", score))
            note = eval_result.get("quality_note", "")
            by = eval_result.get("evaluated_by", "unknown")
            validation["quality_note"] = f"{note} [{by}]" if note else f"[{by}]"
            validation["evaluated_by"] = by
            score = validation["quality_score"]  # self-refine 用真分追踪
        except Exception as e:
            workflow.logger.warning(
                "evaluate_node_quality activity failed (node=%s): %s - heuristic fallback",
                node.node_id, e,
            )
            validation["quality_score"] = score
            validation["quality_note"] = f"heuristic fallback (score={score:.1f}, activity err: {type(e).__name__})"
            validation["evaluated_by"] = "heuristic_activity_err"

        # Op 33: Self-Refine cycle - track quality trends across re-executions
        # If this node has been re-executed, check if quality is improving
        attempt = self._node_attempts.get(node.node_id, 0)
        if attempt > 0:
            refine = SelfRefineCycle(
                draft="draft", max_iterations=3,
            )
            refine.record_iteration(score)
            if refine.should_continue():
                validation["quality_note"] += f" (self-refine: attempt {attempt+1}, improving)"
            else:
                validation["quality_note"] += f" (self-refine: converged after {attempt} attempts)"

        return validation

    def _evaluate_condition(
        self, node: Any, dep_results: dict[str, dict[str, Any]]
    ) -> bool:
        """Op 22: 评估节点的条件表达式。

        Source: Argo Workflows `when` clause + Airflow BranchPythonOperator.

        支持的语法（简化版，不引入新依赖）：
          - "dep_id.status == 'OK'" -> 检查上游节点状态
          - "dep_id.status != 'NG'" -> 排除特定状态
          - "dep_id.data.field == value" -> 检查上游结果字段
          - None / "" -> 无条件，总是执行

        条件不满足时跳过节点（标记为 SKIPPED，不放行下游依赖）。
        """
        condition = getattr(node, "condition", None)
        if not condition:
            return True  # 无条件 -> 执行

        # 简化解析：支持 "dep_id.field == value" 和 "dep_id.field != value"
        # 不引入 AST 解析器，用正则 + 字符串比较
        import re

        # 匹配模式: <dep_id>.<field_path> == <value> 或 != <value>
        pattern = r"(\w+)\.(\w+(?:\.\w+)*)\s*(==|!=)\s*(.+)"
        m = re.match(pattern, condition.strip())
        if not m:
            # 无法解析的条件 -> 默认执行（安全 fallback）
            workflow.logger.warning(
                "Node %s: unparseable condition '%s' -> defaulting to execute",
                node.node_id, condition,
            )
            return True

        dep_id = m.group(1)
        field_path = m.group(2)
        operator = m.group(3)
        expected = m.group(4).strip().strip(chr(39) + chr(34))

        # 从 dep_results 中取值
        dep_result = dep_results.get(dep_id, {})
        actual = self._get_nested(dep_result, field_path)

        if operator == "==":
            should_execute = str(actual) == expected
        else:  # !=
            should_execute = str(actual) != expected

        if not should_execute:
            workflow.logger.info(
                "Node %s: condition '%s' NOT met (actual=%s) -> SKIPPED",
                node.node_id, condition, actual,
            )

        return should_execute

    @staticmethod
    def _get_nested(data: dict, path: str) -> Any:
        """从嵌套 dict 中按 dot-path 取值。"""
        current: Any = data
        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current




    def _send_to_dlq(
        self, node_id: str, result: dict[str, Any], attempts: int
    ) -> None:
        """Op 24: 将反复失败的节点送入死信队列。

        Source: Kafka DLQ + MapReduce backup tasks (Dean & Ghemawat, OSDI 2004).

        节点超过 MAX_FAILURES 次失败后，送入死信队列隔离。
        下游 fan-in 时用默认值替代死信队列中的节点结果，
        不阻塞整个 workflow。
        """
        self._dead_letter[node_id] = {
            "node_id": node_id,
            "attempts": attempts,
            "last_error": result.get("error", ""),
            "last_status": result.get("status", "ERROR"),
            "sent_at": workflow.info().current_time.isoformat() if hasattr(workflow, 'info') else "",
        }
        workflow.logger.error(
            "Node %s sent to DLQ after %d failed attempts (last error: %s)",
            node_id, attempts, result.get("error", "")[:200],
        )

    def _get_node_result_or_default(
        self, node_id: str
    ) -> dict[str, Any]:
        """Op 24: 获取节点结果，死信队列中的用默认值替代。

        fan-in 时调用：如果节点在 DLQ 中，返回默认空结果，
        不阻塞下游节点执行。
        """
        # Op-新: HELD 批次 fan-in 排除 (质量可疑隔离, 非 DLQ 失败)
        _nr = self._node_results.get(node_id, {})
        if isinstance(_nr, dict) and str(_nr.get("status", "")).upper() == "HELD":
            return {
                "status": "HELD_FENCED",
                "data": {},
                "error": "upstream node held in batch_hold (fenced from fan-in)",
            }
        if node_id in self._dead_letter:
            return {
                "status": "DLQ_DEFAULT",
                "data": {},
                "error": "node was sent to dead letter queue",
            }
        return _nr

    def _assert_sm_consistency_safe(self) -> list[dict[str, Any]]:
        """build_result 调用的安全版: 异常不阻断 workflow. 返回 mismatches."""
        try:
            return self._assert_sm_consistency()
        except Exception as e:
            workflow.logger.warning("sm consistency check errored (non-blocking): %s", e)
            return []

    def _assert_governance_consistency(self) -> list[dict[str, Any]]:
        """阶段2 shadow 校验: 比对事件投影 vs 可变字段.

        非阻断 (mismatch 只记 warning + 收集). 返回 mismatch 列表 (空=一致).
        治理模块仍双写字段+事件, 此方法验证投影与字段一致后才切读路径.

        paused_nodes / paused_batches: CRITICAL - 阶段2 要切的读路径, 必须一致.
        held_batches keys: 应一致 (所有路径已双写).
        injected_context: 应一致 (signal 已双写; batch-signal 路径已知 gap, 后续修).
        rework_nodes: CHECK - 阶段3 已事件化 (REWORK_NODE + REWORK_NODE_COMPLETED).
        """
        mismatches: list[dict[str, Any]] = []
        if self._governance_log is None:
            return mismatches
        proj = self._governance_log.project()

        # CRITICAL: paused_nodes (阶段2 读路径切换目标)
        if proj.paused_nodes != self._paused_nodes:
            workflow.logger.warning(
                "GOV MISMATCH [paused_nodes]: field=%s projection=%s",
                sorted(self._paused_nodes), sorted(proj.paused_nodes),
            )
            mismatches.append({
                "field": "paused_nodes",
                "field_val": sorted(self._paused_nodes),
                "projection_val": sorted(proj.paused_nodes),
                "severity": "critical",
            })

        # CRITICAL: paused_batches (阶段2 读路径切换目标)
        if proj.paused_batches != self._paused_batches:
            workflow.logger.warning(
                "GOV MISMATCH [paused_batches]: field=%s projection=%s",
                sorted(self._paused_batches), sorted(proj.paused_batches),
            )
            mismatches.append({
                "field": "paused_batches",
                "field_val": sorted(self._paused_batches),
                "projection_val": sorted(proj.paused_batches),
                "severity": "critical",
            })

        # held_batches keys (应一致)
        proj_held_keys = set(proj.held_batches.keys())
        field_held_keys = set(self._held_batches.keys())
        if proj_held_keys != field_held_keys:
            workflow.logger.warning(
                "GOV MISMATCH [held_batches]: field_keys=%s projection_keys=%s",
                sorted(field_held_keys), sorted(proj_held_keys),
            )
            mismatches.append({
                "field": "held_batches",
                "field_val": sorted(field_held_keys),
                "projection_val": sorted(proj_held_keys),
                "severity": "check",
            })

        # injected_context (应一致, batch-signal 路径可能 gap)
        if proj.injected_context != self._injected_context:
            workflow.logger.warning(
                "GOV MISMATCH [injected_context]: field_keys=%s projection_keys=%s",
                sorted(self._injected_context.keys()),
                sorted(proj.injected_context.keys()),
            )
            mismatches.append({
                "field": "injected_context",
                "field_val": sorted(self._injected_context.keys()),
                "projection_val": sorted(proj.injected_context.keys()),
                "severity": "check",
            })

        # rework_nodes: 阶段3 已事件化 (REWORK_NODE + REWORK_NODE_COMPLETED)
        if proj.rework_nodes != self._rework_nodes:
            workflow.logger.warning(
                "GOV MISMATCH [rework_nodes]: field=%s projection=%s",
                sorted(self._rework_nodes), sorted(proj.rework_nodes),
            )
            mismatches.append({
                "field": "rework_nodes",
                "field_val": sorted(self._rework_nodes),
                "projection_val": sorted(proj.rework_nodes),
                "severity": "check",
            })

        self._gov_mismatches = mismatches
        return mismatches

    def _assert_governance_consistency_safe(self) -> list[dict[str, Any]]:
        """build_result 调用的安全版: 异常不阻断 workflow. 返回 mismatches."""
        try:
            return self._assert_governance_consistency()
        except Exception as e:
            workflow.logger.warning(
                "governance consistency check errored (non-blocking): %s", e)
            return []

    def _build_result(self, error: str | None = None) -> dict[str, Any]:
        """构建 workflow 返回值。"""
        sm_mismatches = self._assert_sm_consistency_safe()
        gov_mismatches = self._assert_governance_consistency_safe()
        # Op 29: Finalize execution record
        if self._execution_record is not None:
            self._execution_record.finalize(self._status, now_ts=workflow.now().timestamp())
            self._execution_record.total_tokens = self._token_budget_used

        result: dict[str, Any] = {
            "workflow_id": self._spec.workflow_id if self._spec else None,
            "objective": self._spec.objective if self._spec else None,
            "status": self._status,
            "completed_nodes": list(self._completed_nodes),
            "failed_nodes": list(self._failed_nodes),
            # P0-2 fix: 暴露 mock 节点列表 — 静默 mock 在工业质检是安全风险
            # 消费者(L1 launch_workflow / 用户)必须能识别"这些节点没真执行"
            "mocked_nodes": list(self._mocked_nodes),
            # Phase E: shadow 一致性结果暴露给测试侧硬断言 (空=状态机与列表一致)
            "sm_mismatches": sm_mismatches,
            # 阶段2: 治理事件投影 vs 字段 shadow 校验 (空=投影与字段一致)
            "gov_mismatches": gov_mismatches,
            # 工作流版本更迭: modify_params=热更新 / add_node等=cancel+relaunch
            "spec_revision_type": self._pending_spec_revision_type,
            "modified_spec": self._pending_spec_change,
            "plan_version": self._current_plan_version,
            "plan_version_history": (
                self._plan_version_registry.to_dict()["versions"]
                if self._plan_version_registry else []
            ),
            "held_batches": {k: dict(v) for k, v in self._gov_held_batches().items()},
            "paused_nodes": sorted(self._gov_paused_nodes()),
            "paused_batches": sorted(self._gov_paused_batches()),
            "reviewer_assignments": dict(self._reviewer_assignments),
            "node_results": dict(self._node_results),
            "node_attempts": dict(self._node_attempts),
        # Op 27: Include audit trail (immutable decision records)
        "audit_trail": [r.to_dict() for r in self._audit_trail._records]
            if self._audit_trail else [],
        # Op 29: Include execution record (complete history for replay/learning)
        "execution_record": self._execution_record.to_dict()
            if self._execution_record else None,
        }
        if error:
            result["error"] = error
        return result
