from datetime import datetime, timezone
from typing import Callable

from controlplane.domain.activity import ActivityOutput
from controlplane.domain.execution import (
    ControlPointInstance,
    ControlPointStatus,
    StateTransition,
    WorkflowState,
)


class ControlPointExecutor:
    def __init__(self, now_fn: Callable[[], datetime] | None = None):
        self._now = now_fn or (lambda: datetime.now(timezone.utc))

    def enter_cp(self, state: WorkflowState, cp_id: str) -> None:
        state.current_cp = cp_id
        state.cp_instances[cp_id] = ControlPointInstance(
            cp_id=cp_id,
            status=ControlPointStatus.EXECUTING,
            started_at=self._now(),
        )

    def set_waiting_gate(self, state: WorkflowState, cp_id: str) -> None:
        instance = state.cp_instances.get(cp_id)
        if instance:
            instance.status = ControlPointStatus.WAITING_GATE

    def resume_from_gate(self, state: WorkflowState, cp_id: str) -> None:
        instance = state.cp_instances.get(cp_id)
        if instance:
            instance.status = ControlPointStatus.EXECUTING

    def complete_cp(self, state: WorkflowState, cp_id: str, result: ActivityOutput) -> None:
        instance = state.cp_instances.get(cp_id)
        if instance:
            instance.status = ControlPointStatus.COMPLETED
            instance.completed_at = self._now()
            instance.activity_output = result

    def fail_cp(self, state: WorkflowState, cp_id: str, result: ActivityOutput | None) -> None:
        instance = state.cp_instances.get(cp_id)
        if instance:
            instance.status = ControlPointStatus.FAILED
            instance.completed_at = self._now()
            instance.activity_output = result

    def record_transition(
        self, state: WorkflowState, from_cp: str, to_cp: str | None, condition: str
    ) -> None:
        state.transitions.append(
            StateTransition(
                from_cp=from_cp,
                to_cp=to_cp,
                condition=condition,
                timestamp=self._now(),
            )
        )
