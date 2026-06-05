"""Tests for EscalationTracker."""

import pytest

from src.shared.enums import FallbackMode, ShadowStatus
from src.shared.ports.validation import EscalationTrackerInput
from src.validation.escalation import EscalationTracker


@pytest.fixture
def tracker() -> EscalationTracker:
    return EscalationTracker()


# -- 1. Initial state: count=0, mode=NONE -----------------------------------


async def test_initial_state(tracker: EscalationTracker) -> None:
    state = await tracker.get_state()
    assert state.consecutive_critical_count == 0
    assert state.current_fallback_mode == FallbackMode.NONE


# -- 2. 3 consecutive CRITICAL -> COGNITIVE_FALLBACK ------------------------


async def test_three_consecutive_critical_triggers_cognitive_fallback(
    tracker: EscalationTracker,
) -> None:
    for _ in range(3):
        await tracker.update(EscalationTrackerInput(shadow_result=ShadowStatus.CRITICAL))

    state = await tracker.get_state()
    assert state.consecutive_critical_count == 3
    assert state.current_fallback_mode == FallbackMode.COGNITIVE_FALLBACK


# -- 3. 5 consecutive CRITICAL -> HUMAN_INTERVENTION ------------------------


async def test_five_consecutive_critical_triggers_human_intervention(
    tracker: EscalationTracker,
) -> None:
    for _ in range(5):
        await tracker.update(EscalationTrackerInput(shadow_result=ShadowStatus.CRITICAL))

    state = await tracker.get_state()
    assert state.consecutive_critical_count == 5
    assert state.current_fallback_mode == FallbackMode.HUMAN_INTERVENTION


# -- 4. Non-CRITICAL resets counter to 0 ------------------------------------


async def test_non_critical_resets_counter(tracker: EscalationTracker) -> None:
    # Build up some CRITICAL count
    for _ in range(2):
        await tracker.update(EscalationTrackerInput(shadow_result=ShadowStatus.CRITICAL))

    # Feed a non-CRITICAL result
    await tracker.update(EscalationTrackerInput(shadow_result=ShadowStatus.CONCUR))

    state = await tracker.get_state()
    assert state.consecutive_critical_count == 0
    assert state.current_fallback_mode == FallbackMode.NONE


# -- 5. reset() returns to count=0, mode=NONE -------------------------------


async def test_reset_returns_to_initial_state(tracker: EscalationTracker) -> None:
    # Escalate to COGNITIVE_FALLBACK
    for _ in range(3):
        await tracker.update(EscalationTrackerInput(shadow_result=ShadowStatus.CRITICAL))

    state = await tracker.reset(reason="test reset")
    assert state.consecutive_critical_count == 0
    assert state.current_fallback_mode == FallbackMode.NONE
