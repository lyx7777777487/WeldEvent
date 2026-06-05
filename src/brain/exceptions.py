"""L1 Cognitive Plane — Brain-specific exceptions.

Source: L1_Port_and_Contract_Design.md (Phase 4, Brain state machine).
"""

from src.shared.enums import BrainStateType, BrainTrigger


class InvalidStateTransition(Exception):
    """Raised when a trigger cannot be applied from the current Brain state."""

    def __init__(self, from_state: BrainStateType, trigger: BrainTrigger) -> None:
        self.from_state = from_state
        self.trigger = trigger
        super().__init__(
            f"Invalid transition: cannot apply '{trigger.value}' from state {from_state.value}"
        )
