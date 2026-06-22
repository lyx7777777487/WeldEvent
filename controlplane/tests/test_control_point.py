from datetime import datetime, timezone

from controlplane.domain.activity import ActivityOutput, ActivityStatus
from controlplane.domain.execution import (
    ControlPointInstance,
    ControlPointStatus,
    WorkflowContext,
    WorkflowState,
    WorkflowStatus,
)
from controlplane.runtime.control_point import ControlPointExecutor


FIXED_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_state() -> WorkflowState:
    ctx = WorkflowContext(template_id="t", template_version="1.0", workflow_id="w")
    return WorkflowState(workflow_context=ctx)


def _make_executor() -> ControlPointExecutor:
    return ControlPointExecutor(now_fn=lambda: FIXED_NOW)


def test_enter_cp():
    state = _make_state()
    executor = _make_executor()
    executor.enter_cp(state, "CP0")
    assert "CP0" in state.cp_instances
    assert state.cp_instances["CP0"].status == ControlPointStatus.EXECUTING
    assert state.cp_instances["CP0"].started_at == FIXED_NOW
    assert state.current_cp == "CP0"


def test_complete_cp():
    state = _make_state()
    executor = _make_executor()
    executor.enter_cp(state, "CP0")
    result = ActivityOutput(status=ActivityStatus.OK, data={"val": 1})
    executor.complete_cp(state, "CP0", result)
    assert state.cp_instances["CP0"].status == ControlPointStatus.COMPLETED
    assert state.cp_instances["CP0"].completed_at == FIXED_NOW
    assert state.cp_instances["CP0"].activity_output == result


def test_fail_cp():
    state = _make_state()
    executor = _make_executor()
    executor.enter_cp(state, "CP0")
    result = ActivityOutput(status=ActivityStatus.ERROR, error="timeout")
    executor.fail_cp(state, "CP0", result)
    assert state.cp_instances["CP0"].status == ControlPointStatus.FAILED
    assert state.cp_instances["CP0"].activity_output == result


def test_record_transition():
    state = _make_state()
    executor = _make_executor()
    executor.record_transition(state, "CP0", "CP1", "OK")
    assert len(state.transitions) == 1
    assert state.transitions[0].from_cp == "CP0"
    assert state.transitions[0].to_cp == "CP1"
    assert state.transitions[0].condition == "OK"
    assert state.transitions[0].timestamp == FIXED_NOW


def test_set_waiting_gate():
    state = _make_state()
    executor = _make_executor()
    executor.enter_cp(state, "CP0")
    executor.set_waiting_gate(state, "CP0")
    assert state.cp_instances["CP0"].status == ControlPointStatus.WAITING_GATE


def test_resume_from_gate():
    state = _make_state()
    executor = _make_executor()
    executor.enter_cp(state, "CP0")
    executor.set_waiting_gate(state, "CP0")
    executor.resume_from_gate(state, "CP0")
    assert state.cp_instances["CP0"].status == ControlPointStatus.EXECUTING
