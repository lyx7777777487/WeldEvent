"""L1 Cognitive Plane — Brain state machine.

Stateless pure-function state machine.  No internal mutable state --
callers own the state per case, enabling multi-case concurrency in Phase 5B.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 4).
"""

from src.shared.enums import BrainStateType, BrainTrigger
from src.brain.exceptions import InvalidStateTransition


# ---------------------------------------------------------------------------
# Transition table: (current_state, trigger) -> next_state
# ---------------------------------------------------------------------------

_TRANSITIONS: dict[tuple[BrainStateType, BrainTrigger], BrainStateType] = {
    # Entry
    (BrainStateType.IDLE, BrainTrigger.EVENT_DEQUEUED): BrainStateType.OBSERVING,

    # Context loading (branching by reasoning mode)
    (BrainStateType.OBSERVING, BrainTrigger.CONTEXT_LOADED_ROUTINE): BrainStateType.MEMORY_RETRIEVAL,
    (BrainStateType.OBSERVING, BrainTrigger.CONTEXT_LOADED_ADAPTIVE): BrainStateType.UNDERSTANDING,
    (BrainStateType.OBSERVING, BrainTrigger.CONTEXT_LOADED_EXPLORATORY): BrainStateType.UNDERSTANDING,

    # Understanding -> Knowledge -> Memory (adaptive / exploratory)
    (BrainStateType.UNDERSTANDING, BrainTrigger.CONTEXT_UNDERSTOOD): BrainStateType.KNOWLEDGE_RETRIEVAL,
    (BrainStateType.KNOWLEDGE_RETRIEVAL, BrainTrigger.KNOWLEDGE_RECEIVED): BrainStateType.MEMORY_RETRIEVAL,

    # Memory retrieval (branching by reasoning mode)
    (BrainStateType.MEMORY_RETRIEVAL, BrainTrigger.MEMORY_RECEIVED_ROUTINE): BrainStateType.MEMORY_MATCHING,
    (BrainStateType.MEMORY_RETRIEVAL, BrainTrigger.MEMORY_RECEIVED_ADAPTIVE): BrainStateType.REASONING,
    (BrainStateType.MEMORY_RETRIEVAL, BrainTrigger.MEMORY_RECEIVED_EXPLORATORY): BrainStateType.REASONING,

    # Routine shortcut
    (BrainStateType.MEMORY_MATCHING, BrainTrigger.MATCH_PRODUCED): BrainStateType.VALIDATION,

    # Adaptive / Exploratory deep path
    (BrainStateType.REASONING, BrainTrigger.REASONING_COMPLETED): BrainStateType.DECISION_GENERATION,
    (BrainStateType.DECISION_GENERATION, BrainTrigger.DECISION_GENERATED): BrainStateType.VALIDATION,

    # Validation outcomes
    (BrainStateType.VALIDATION, BrainTrigger.APPROVED): BrainStateType.PUBLICATION,
    (BrainStateType.VALIDATION, BrainTrigger.REQUIRES_REVIEW): BrainStateType.PUBLICATION,
    (BrainStateType.VALIDATION, BrainTrigger.REJECTED): BrainStateType.IDLE,
    (BrainStateType.VALIDATION, BrainTrigger.ESCALATED): BrainStateType.PUBLICATION,

    # Publication outcomes
    (BrainStateType.PUBLICATION, BrainTrigger.PUBLISHED_ROUTINE_APPROVED): BrainStateType.IDLE,
    (BrainStateType.PUBLICATION, BrainTrigger.PUBLISHED_ADAPTIVE_APPROVED): BrainStateType.IDLE,
    (BrainStateType.PUBLICATION, BrainTrigger.PUBLISHED_EXPLORATORY): BrainStateType.WAITING_FEEDBACK,
    (BrainStateType.PUBLICATION, BrainTrigger.PUBLISHED_REQUIRES_REVIEW): BrainStateType.WAITING_FEEDBACK,
    (BrainStateType.PUBLICATION, BrainTrigger.ESCALATION_PUBLISHED): BrainStateType.IDLE,

    # Feedback loop
    (BrainStateType.WAITING_FEEDBACK, BrainTrigger.FEEDBACK_RECEIVED): BrainStateType.OBSERVING,

    # Error recovery
    (BrainStateType.ERROR, BrainTrigger.ERROR_RECOVERED): BrainStateType.IDLE,
}

# ---------------------------------------------------------------------------
# Any-source triggers: valid from *any* current state (except ABORT for
# RECOVERABLE_ERROR, handled in can_transition).
# ---------------------------------------------------------------------------

_ANY_SOURCE_TRIGGERS: dict[BrainTrigger, BrainStateType] = {
    BrainTrigger.UNRECOVERABLE_ERROR: BrainStateType.IDLE,
    BrainTrigger.RECOVERABLE_ERROR: BrainStateType.ERROR,
    BrainTrigger.ABORT: BrainStateType.ABORT,
}


class BrainStateMachine:
    """Stateless pure-function state machine.

    No __init__, no self._state.  Callers pass current_state explicitly to
    transition() and can_transition().  This enables multi-case concurrency
    in Phase 5B.
    """

    @staticmethod
    def transition(current_state: BrainStateType, trigger: BrainTrigger) -> BrainStateType:
        """Apply *trigger* from *current_state* and return the new state.

        Raises InvalidStateTransition if the trigger is not valid from the
        current state.  Any-source triggers (ABORT, RECOVERABLE_ERROR,
        UNRECOVERABLE_ERROR) bypass the normal table.
        """
        if trigger in _ANY_SOURCE_TRIGGERS:
            return _ANY_SOURCE_TRIGGERS[trigger]

        key = (current_state, trigger)
        if key not in _TRANSITIONS:
            raise InvalidStateTransition(current_state, trigger)
        return _TRANSITIONS[key]

    @staticmethod
    def can_transition(current_state: BrainStateType, trigger: BrainTrigger) -> bool:
        """Return True if *trigger* can be applied from *current_state*.

        ABORT is treated as terminal: only UNRECOVERABLE_ERROR can leave it.
        """
        if trigger in _ANY_SOURCE_TRIGGERS:
            return current_state != BrainStateType.ABORT or trigger == BrainTrigger.UNRECOVERABLE_ERROR
        return (current_state, trigger) in _TRANSITIONS
