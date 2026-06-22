"""Tests for BrainStateMachine — stateless pure-function state machine.

Covers every transition in the ROUTINE, ADAPTIVE, and EXPLORATORY paths,
error recovery, abort, and invalid-transition rejection.
"""

import pytest

from cognitiveplane.shared.enums import BrainStateType, BrainTrigger
from cognitiveplane.control.exceptions import InvalidStateTransition


# ---------------------------------------------------------------------------
# Helper: resolve the class lazily so the import error is clear if missing
# ---------------------------------------------------------------------------

def _sm():
    from cognitiveplane.control.state_machine import BrainStateMachine
    return BrainStateMachine


# ===================================================================
# Single-transition tests
# ===================================================================


class TestSingleTransitions:
    """Test each individual valid transition."""

    def test_idle_to_observing(self):
        sm = _sm()
        result = sm.transition(BrainStateType.IDLE, BrainTrigger.EVENT_DEQUEUED)
        assert result == BrainStateType.OBSERVING

    def test_observing_to_memory_retrieval_routine(self):
        sm = _sm()
        result = sm.transition(
            BrainStateType.OBSERVING, BrainTrigger.CONTEXT_LOADED_ROUTINE
        )
        assert result == BrainStateType.MEMORY_RETRIEVAL

    def test_observing_to_understanding_adaptive(self):
        sm = _sm()
        result = sm.transition(
            BrainStateType.OBSERVING, BrainTrigger.CONTEXT_LOADED_ADAPTIVE
        )
        assert result == BrainStateType.UNDERSTANDING

    def test_observing_to_understanding_exploratory(self):
        sm = _sm()
        result = sm.transition(
            BrainStateType.OBSERVING, BrainTrigger.CONTEXT_LOADED_EXPLORATORY
        )
        assert result == BrainStateType.UNDERSTANDING


# ===================================================================
# Full-path tests
# ===================================================================


class TestRoutinePath:
    """Full ROUTINE path: IDLE -> OBSERVING -> MEMORY_RETRIEVAL -> MEMORY_MATCHING -> VALIDATION."""

    def test_full_routine_path(self):
        sm = _sm()
        state = BrainStateType.IDLE

        state = sm.transition(state, BrainTrigger.EVENT_DEQUEUED)
        assert state == BrainStateType.OBSERVING

        state = sm.transition(state, BrainTrigger.CONTEXT_LOADED_ROUTINE)
        assert state == BrainStateType.MEMORY_RETRIEVAL

        state = sm.transition(state, BrainTrigger.MEMORY_RECEIVED_ROUTINE)
        assert state == BrainStateType.MEMORY_MATCHING

        state = sm.transition(state, BrainTrigger.MATCH_PRODUCED)
        assert state == BrainStateType.VALIDATION


class TestAdaptivePath:
    """Full ADAPTIVE path:
    IDLE -> OBSERVING -> UNDERSTANDING -> KNOWLEDGE_RETRIEVAL ->
    MEMORY_RETRIEVAL -> REASONING -> DECISION_GENERATION -> VALIDATION.
    """

    def test_full_adaptive_path(self):
        sm = _sm()
        state = BrainStateType.IDLE

        state = sm.transition(state, BrainTrigger.EVENT_DEQUEUED)
        assert state == BrainStateType.OBSERVING

        state = sm.transition(state, BrainTrigger.CONTEXT_LOADED_ADAPTIVE)
        assert state == BrainStateType.UNDERSTANDING

        state = sm.transition(state, BrainTrigger.CONTEXT_UNDERSTOOD)
        assert state == BrainStateType.KNOWLEDGE_RETRIEVAL

        state = sm.transition(state, BrainTrigger.KNOWLEDGE_RECEIVED)
        assert state == BrainStateType.MEMORY_RETRIEVAL

        state = sm.transition(state, BrainTrigger.MEMORY_RECEIVED_ADAPTIVE)
        assert state == BrainStateType.REASONING

        state = sm.transition(state, BrainTrigger.REASONING_COMPLETED)
        assert state == BrainStateType.DECISION_GENERATION

        state = sm.transition(state, BrainTrigger.DECISION_GENERATED)
        assert state == BrainStateType.VALIDATION


# ===================================================================
# Validation -> Publication / IDLE
# ===================================================================


class TestValidationOutcomes:

    def test_validation_to_publication_approved(self):
        sm = _sm()
        result = sm.transition(BrainStateType.VALIDATION, BrainTrigger.APPROVED)
        assert result == BrainStateType.PUBLICATION

    def test_validation_to_idle_rejected(self):
        sm = _sm()
        result = sm.transition(BrainStateType.VALIDATION, BrainTrigger.REJECTED)
        assert result == BrainStateType.IDLE


# ===================================================================
# Publication -> IDLE / WAITING_FEEDBACK
# ===================================================================


class TestPublicationOutcomes:

    def test_publication_to_idle_routine_approved(self):
        sm = _sm()
        result = sm.transition(
            BrainStateType.PUBLICATION, BrainTrigger.PUBLISHED_ROUTINE_APPROVED
        )
        assert result == BrainStateType.IDLE

    def test_publication_to_idle_adaptive_approved(self):
        sm = _sm()
        result = sm.transition(
            BrainStateType.PUBLICATION, BrainTrigger.PUBLISHED_ADAPTIVE_APPROVED
        )
        assert result == BrainStateType.IDLE

    def test_publication_to_waiting_feedback_exploratory(self):
        sm = _sm()
        result = sm.transition(
            BrainStateType.PUBLICATION, BrainTrigger.PUBLISHED_EXPLORATORY
        )
        assert result == BrainStateType.WAITING_FEEDBACK

    def test_publication_to_waiting_feedback_requires_review(self):
        sm = _sm()
        result = sm.transition(
            BrainStateType.PUBLICATION, BrainTrigger.PUBLISHED_REQUIRES_REVIEW
        )
        assert result == BrainStateType.WAITING_FEEDBACK

    def test_publication_to_idle_escalation(self):
        sm = _sm()
        result = sm.transition(
            BrainStateType.PUBLICATION, BrainTrigger.ESCALATION_PUBLISHED
        )
        assert result == BrainStateType.IDLE


# ===================================================================
# Waiting feedback -> Observing
# ===================================================================


class TestWaitingFeedback:

    def test_waiting_feedback_to_observing(self):
        sm = _sm()
        result = sm.transition(
            BrainStateType.WAITING_FEEDBACK, BrainTrigger.FEEDBACK_RECEIVED
        )
        assert result == BrainStateType.OBSERVING


# ===================================================================
# Invalid transitions
# ===================================================================


class TestInvalidTransitions:

    def test_invalid_transition_raises(self):
        sm = _sm()
        with pytest.raises(InvalidStateTransition):
            sm.transition(BrainStateType.IDLE, BrainTrigger.APPROVED)

    def test_invalid_transition_exception_attributes(self):
        sm = _sm()
        with pytest.raises(InvalidStateTransition) as exc_info:
            sm.transition(BrainStateType.IDLE, BrainTrigger.APPROVED)
        assert exc_info.value.from_state == BrainStateType.IDLE
        assert exc_info.value.trigger == BrainTrigger.APPROVED


# ===================================================================
# can_transition
# ===================================================================


class TestCanTransition:

    def test_can_transition_valid(self):
        sm = _sm()
        assert sm.can_transition(BrainStateType.IDLE, BrainTrigger.EVENT_DEQUEUED) is True

    def test_can_transition_invalid(self):
        sm = _sm()
        assert sm.can_transition(BrainStateType.IDLE, BrainTrigger.APPROVED) is False

    def test_can_transition_any_source_trigger(self):
        sm = _sm()
        # RECOVERABLE_ERROR is an any-source trigger; valid from non-ABORT states
        assert sm.can_transition(BrainStateType.OBSERVING, BrainTrigger.RECOVERABLE_ERROR) is True
        assert sm.can_transition(BrainStateType.VALIDATION, BrainTrigger.RECOVERABLE_ERROR) is True


# ===================================================================
# Error recovery
# ===================================================================


class TestErrorRecovery:

    def test_recoverable_error_from_any_state(self):
        sm = _sm()
        for state in BrainStateType:
            if state == BrainStateType.ABORT:
                continue  # ABORT blocks recoverable_error per can_transition logic
            result = sm.transition(state, BrainTrigger.RECOVERABLE_ERROR)
            assert result == BrainStateType.ERROR, f"From {state}"

    def test_error_recovered_to_idle(self):
        sm = _sm()
        result = sm.transition(BrainStateType.ERROR, BrainTrigger.ERROR_RECOVERED)
        assert result == BrainStateType.IDLE


# ===================================================================
# Abort
# ===================================================================


class TestAbort:

    def test_abort_from_any_state(self):
        sm = _sm()
        for state in BrainStateType:
            result = sm.transition(state, BrainTrigger.ABORT)
            assert result == BrainStateType.ABORT, f"From {state}"

    def test_abort_is_terminal_for_all_triggers(self):
        """P2-10 fix: ABORT is a true terminal state — no trigger can leave it."""
        sm = _sm()
        for trigger in BrainTrigger:
            assert sm.can_transition(BrainStateType.ABORT, trigger) is False, (
                f"ABORT should be terminal but can_transition(ABORT, {trigger}) == True"
            )

    def test_unrecoverable_error_from_abort_is_blocked(self):
        """P2-10 fix: even UNRECOVERABLE_ERROR cannot leave ABORT."""
        sm = _sm()
        assert sm.can_transition(BrainStateType.ABORT, BrainTrigger.UNRECOVERABLE_ERROR) is False

    def test_recoverable_error_blocked_from_abort(self):
        """P2-10 fix: ABORT is terminal — no triggers can leave it."""
        sm = _sm()
        assert sm.can_transition(BrainStateType.ABORT, BrainTrigger.RECOVERABLE_ERROR) is False


# ===================================================================
# Unrecoverable error from any state
# ===================================================================


class TestUnrecoverableError:
    """P2-10 fix: UNRECOVERABLE_ERROR goes to ABORT (not IDLE)."""

    def test_unrecoverable_error_goes_to_abort_from_any_state(self):
        sm = _sm()
        for state in BrainStateType:
            if state == BrainStateType.ABORT:
                # ABORT is terminal — transition raises InvalidStateTransition
                continue
            result = sm.transition(state, BrainTrigger.UNRECOVERABLE_ERROR)
            assert result == BrainStateType.ABORT, f"From {state}"
