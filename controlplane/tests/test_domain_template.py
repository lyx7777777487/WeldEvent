from datetime import timedelta

from controlplane.domain.template import (
    ActivityBinding,
    BranchDefinition,
    ControlPointDefinition,
    MigrationStrategy,
    TransitionDefinition,
    TransitionType,
    WorkflowTemplate,
)
from controlplane.domain.human_gate import HumanGateDefinition
from controlplane.domain.policy import ExecutionPolicy


def test_activity_binding_defaults():
    b = ActivityBinding(activity_name="mea_activity")
    assert b.activity_name == "mea_activity"
    assert b.activity_version == "latest"
    assert b.task_queue is None
    assert b.execution_target is None


def test_activity_binding_full():
    b = ActivityBinding(
        activity_name="mea_activity",
        activity_version="v2",
        task_queue="gpu-queue",
        execution_target="gpu",
    )
    assert b.activity_version == "v2"
    assert b.task_queue == "gpu-queue"
    assert b.execution_target == "gpu"


def test_control_point_with_binding():
    cp = ControlPointDefinition(
        id="CP0",
        name="Incoming Quality Assessment",
        activity_binding=ActivityBinding(activity_name="iqa_activity"),
        execution_policy=ExecutionPolicy(),
    )
    assert cp.id == "CP0"
    assert cp.activity_binding.activity_name == "iqa_activity"
    assert cp.human_gate is None


def test_control_point_without_binding():
    cp = ControlPointDefinition(
        id="CP_DECISION",
        name="Decision Point",
        activity_binding=None,
        execution_policy=ExecutionPolicy(),
    )
    assert cp.activity_binding is None


def test_control_point_with_human_gate():
    cp = ControlPointDefinition(
        id="CP4",
        name="Visual Inspection Review",
        activity_binding=ActivityBinding(activity_name="vda_activity"),
        execution_policy=ExecutionPolicy(),
        human_gate=HumanGateDefinition(timeout=timedelta(hours=1), redirect_target="CP5"),
    )
    assert cp.human_gate.timeout == timedelta(hours=1)
    assert cp.human_gate.redirect_target == "CP5"


def test_transition_type_enum():
    assert TransitionType.SEQUENTIAL.value == "SEQUENTIAL"
    assert TransitionType.PARALLEL.value == "PARALLEL"
    assert TransitionType.JOIN.value == "JOIN"


def test_transition_definition_sequential():
    td = TransitionDefinition(
        from_cp="CP0",
        transition_type=TransitionType.SEQUENTIAL,
        branches=[
            BranchDefinition(condition="OK", to_cp="CP1"),
            BranchDefinition(condition="default", to_cp="CP5"),
        ],
    )
    assert td.from_cp == "CP0"
    assert len(td.branches) == 2
    assert td.parallel_targets == []


def test_transition_definition_parallel():
    td = TransitionDefinition(
        from_cp="CP2",
        transition_type=TransitionType.PARALLEL,
        parallel_targets=["CP2A", "CP2B", "CP2C"],
    )
    assert td.transition_type == TransitionType.PARALLEL
    assert td.parallel_targets == ["CP2A", "CP2B", "CP2C"]


def test_workflow_template_creation():
    template = WorkflowTemplate(
        id="weld_inspection",
        version="1.0",
        control_points=[
            ControlPointDefinition(
                id="CP0",
                name="IQA",
                activity_binding=ActivityBinding(activity_name="iqa_activity"),
                execution_policy=ExecutionPolicy(),
            ),
        ],
        transitions=[
            TransitionDefinition(
                from_cp="CP0",
                branches=[BranchDefinition(condition="OK", to_cp=None)],
            ),
        ],
        entry_point="CP0",
    )
    assert template.id == "weld_inspection"
    assert template.version == "1.0"
    assert template.entry_point == "CP0"
    assert template.migration_strategy is None


def test_workflow_template_with_migration():
    template = WorkflowTemplate(
        id="weld_inspection",
        version="2.0",
        control_points=[],
        transitions=[],
        entry_point="CP0",
        migration_strategy=MigrationStrategy.ROLLING,
    )
    assert template.migration_strategy == MigrationStrategy.ROLLING


def test_branch_definition_end_workflow():
    b = BranchDefinition(condition="OK", to_cp=None)
    assert b.to_cp is None
