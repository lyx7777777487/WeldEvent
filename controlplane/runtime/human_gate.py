from controlplane.domain.human_gate import GateAction, HumanGateDefinition


class HumanGateRuntime:
    def __init__(self) -> None:
        self._pending_action: GateAction | None = None
        self._redirect_target: str | None = None

    @property
    def pending_action(self) -> GateAction | None:
        return self._pending_action

    @property
    def redirect_target(self) -> str | None:
        return self._redirect_target

    @property
    def is_awaiting(self) -> bool:
        return self._pending_action is None

    def reset(self) -> None:
        self._pending_action = None
        self._redirect_target = None

    def set_action(self, action: GateAction, redirect_target: str | None) -> None:
        self._pending_action = action
        self._redirect_target = redirect_target

    def validate_action(self, action, redirect_target: str | None = None) -> None:
        if action not in (GateAction.CONTINUE, GateAction.REDIRECT, GateAction.TERMINATE):
            raise ValueError(f"Invalid gate action: {action}")
        if action == GateAction.REDIRECT and redirect_target is None:
            # redirect_target may come from HumanGateDefinition instead,
            # so this is only a hard reject if neither source provides it.
            # The workflow handles the final null-target check.
            pass

    def resolve_redirect_target(self, gate_def: HumanGateDefinition) -> str | None:
        if self._redirect_target is not None:
            return self._redirect_target
        return gate_def.redirect_target
