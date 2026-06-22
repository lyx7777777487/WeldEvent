import pytest

from controlplane.domain.activity import ActivityOutput, ActivityStatus
from controlplane.domain.template import (
    ActivityBinding,
    BranchDefinition,
    ControlPointDefinition,
    TransitionDefinition,
    TransitionType,
    WorkflowTemplate,
)
from controlplane.domain.policy import ExecutionPolicy
from controlplane.runtime.transition import TransitionResolver


def _make_template(transitions, entry="CP0"):
    return WorkflowTemplate(
        id="test", version="1.0", control_points=[], transitions=transitions, entry_point=entry,
    )


def test_resolve_sequential_ok():
    template = _make_template([
        TransitionDefinition(
            from_cp="CP0",
            branches=[
                BranchDefinition(condition="OK", to_cp="CP1"),
                BranchDefinition(condition="NG", to_cp="CP5"),
            ],
        ),
    ])
    resolver = TransitionResolver(template)
    assert resolver.resolve_next("CP0", "OK") == "CP1"


def test_resolve_sequential_ng():
    template = _make_template([
        TransitionDefinition(
            from_cp="CP0",
            branches=[
                BranchDefinition(condition="OK", to_cp="CP1"),
                BranchDefinition(condition="NG", to_cp="CP5"),
            ],
        ),
    ])
    resolver = TransitionResolver(template)
    assert resolver.resolve_next("CP0", "NG") == "CP5"


def test_resolve_default_branch():
    template = _make_template([
        TransitionDefinition(
            from_cp="CP4",
            branches=[
                BranchDefinition(condition="OK", to_cp="CP6"),
                BranchDefinition(condition="default", to_cp="CP5"),
            ],
        ),
    ])
    resolver = TransitionResolver(template)
    assert resolver.resolve_next("CP4", "MARGINAL") == "CP5"
    assert resolver.resolve_next("CP4", "NG") == "CP5"


def test_resolve_no_transition_returns_none():
    template = _make_template([])
    resolver = TransitionResolver(template)
    assert resolver.resolve_next("CP99", "OK") is None


def test_resolve_no_matching_branch_returns_none():
    template = _make_template([
        TransitionDefinition(
            from_cp="CP0",
            branches=[BranchDefinition(condition="OK", to_cp="CP1")],
        ),
    ])
    resolver = TransitionResolver(template)
    assert resolver.resolve_next("CP0", "NG") is None


def test_resolve_end_workflow():
    template = _make_template([
        TransitionDefinition(
            from_cp="CP6",
            branches=[BranchDefinition(condition="OK", to_cp=None)],
        ),
    ])
    resolver = TransitionResolver(template)
    assert resolver.resolve_next("CP6", "OK") is None


def test_resolve_parallel_raises_not_implemented():
    template = _make_template([
        TransitionDefinition(
            from_cp="CP2",
            transition_type=TransitionType.PARALLEL,
            parallel_targets=["CP2A", "CP2B"],
        ),
    ])
    resolver = TransitionResolver(template)
    with pytest.raises(NotImplementedError, match="Parallel transition"):
        resolver.resolve_next("CP2", "OK")


def test_result_to_condition_ok():
    resolver = TransitionResolver(_make_template([]))
    result = ActivityOutput(status=ActivityStatus.OK)
    assert resolver.result_to_condition(result) == "OK"


def test_result_to_condition_marginal():
    resolver = TransitionResolver(_make_template([]))
    result = ActivityOutput(status=ActivityStatus.MARGINAL)
    assert resolver.result_to_condition(result) == "MARGINAL"


def test_result_to_condition_none():
    resolver = TransitionResolver(_make_template([]))
    assert resolver.result_to_condition(None) == "default"
