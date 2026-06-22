import pytest

from controlplane.domain.human_gate import GateAction, HumanGateDefinition
from controlplane.runtime.human_gate import HumanGateRuntime


def test_reset_state():
    runtime = HumanGateRuntime()
    runtime.set_action(GateAction.CONTINUE, None)
    assert runtime.pending_action is not None
    runtime.reset()
    assert runtime.pending_action is None
    assert runtime.redirect_target is None


def test_set_action_continue():
    runtime = HumanGateRuntime()
    runtime.reset()
    runtime.set_action(GateAction.CONTINUE, None)
    assert runtime.pending_action == GateAction.CONTINUE
    assert runtime.redirect_target is None


def test_set_action_redirect():
    runtime = HumanGateRuntime()
    runtime.reset()
    runtime.set_action(GateAction.REDIRECT, "CP5")
    assert runtime.pending_action == GateAction.REDIRECT
    assert runtime.redirect_target == "CP5"


def test_set_action_terminate():
    runtime = HumanGateRuntime()
    runtime.reset()
    runtime.set_action(GateAction.TERMINATE, None)
    assert runtime.pending_action == GateAction.TERMINATE


def test_is_awaiting():
    runtime = HumanGateRuntime()
    runtime.reset()
    assert runtime.is_awaiting is True
    runtime.set_action(GateAction.CONTINUE, None)
    assert runtime.is_awaiting is False


def test_validate_valid_actions():
    runtime = HumanGateRuntime()
    for action in (GateAction.CONTINUE, GateAction.REDIRECT, GateAction.TERMINATE):
        runtime.validate_action(action)  # should not raise


def test_validate_invalid_action_raises():
    runtime = HumanGateRuntime()
    with pytest.raises(ValueError, match="Invalid gate action"):
        runtime.validate_action("INVALID")


def test_resolve_redirect_target_from_update():
    runtime = HumanGateRuntime()
    runtime.set_action(GateAction.REDIRECT, "CP5")
    gate_def = HumanGateDefinition(redirect_target="CP3")
    target = runtime.resolve_redirect_target(gate_def)
    assert target == "CP5"  # update-provided target takes precedence


def test_resolve_redirect_target_from_definition():
    runtime = HumanGateRuntime()
    runtime.set_action(GateAction.REDIRECT, None)
    gate_def = HumanGateDefinition(redirect_target="CP3")
    target = runtime.resolve_redirect_target(gate_def)
    assert target == "CP3"  # falls back to definition target


def test_resolve_redirect_target_none():
    runtime = HumanGateRuntime()
    runtime.set_action(GateAction.REDIRECT, None)
    gate_def = HumanGateDefinition()
    target = runtime.resolve_redirect_target(gate_def)
    assert target is None
