from datetime import datetime, timezone

from controlplane.domain.execution import (
    ControlPointInstance,
    ControlPointStatus,
    StateTransition,
    WorkflowContext,
    WorkflowState,
    WorkflowStatus,
)


def test_workflow_status_values():
    assert WorkflowStatus.RUNNING.value == "RUNNING"
    assert WorkflowStatus.COMPLETED.value == "COMPLETED"
    assert WorkflowStatus.FAILED.value == "FAILED"
    assert WorkflowStatus.TERMINATED.value == "TERMINATED"


def test_control_point_status_values():
    assert ControlPointStatus.PENDING.value == "PENDING"
    assert ControlPointStatus.EXECUTING.value == "EXECUTING"
    assert ControlPointStatus.WAITING_GATE.value == "WAITING_GATE"
    assert ControlPointStatus.COMPLETED.value == "COMPLETED"


def test_workflow_context():
    ctx = WorkflowContext(
        template_id="weld_inspection",
        template_version="1.0",
        workflow_id="wf-001",
    )
    assert ctx.template_id == "weld_inspection"
    assert ctx.metadata == {}


def test_state_transition():
    t = StateTransition(
        from_cp="CP0",
        to_cp="CP1",
        condition="OK",
        timestamp=datetime.now(timezone.utc),
    )
    assert t.from_cp == "CP0"
    assert t.to_cp == "CP1"
    assert t.activity_output is None


def test_control_point_instance():
    cpi = ControlPointInstance(cp_id="CP0", status=ControlPointStatus.EXECUTING)
    assert cpi.started_at is None
    assert cpi.activity_output is None


def test_workflow_state():
    ctx = WorkflowContext(
        template_id="weld_inspection",
        template_version="1.0",
        workflow_id="wf-001",
    )
    state = WorkflowState(workflow_context=ctx)
    assert state.current_cp is None
    assert state.status == WorkflowStatus.RUNNING
    assert state.cp_instances == {}
    assert state.transitions == []
