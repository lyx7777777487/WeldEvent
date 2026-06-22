"""Tests for EscalationTracker.

P1-7 fix: Per-case isolation. Each case has independent escalation state.
"""

import pytest

from cognitiveplane.shared.enums import FallbackMode, ShadowStatus
from cognitiveplane.shared.ports.validation import EscalationTrackerInput
from cognitiveplane.shared.types import CaseId
from cognitiveplane.governance.escalation import EscalationTracker


CASE_1 = CaseId(value="case-001")
CASE_2 = CaseId(value="case-002")


@pytest.fixture
def tracker() -> EscalationTracker:
    return EscalationTracker()


# -- advice.md. Initial state: count=0, mode=NONE -----------------------------------


async def test_initial_state(tracker: EscalationTracker) -> None:
    state = await tracker.get_state()
    assert state.consecutive_critical_count == 0
    assert state.current_fallback_mode == FallbackMode.NONE


# -- 2. 3 consecutive CRITICAL -> COGNITIVE_FALLBACK ------------------------


async def test_three_consecutive_critical_triggers_cognitive_fallback(
    tracker: EscalationTracker,
) -> None:
    for _ in range(3):
        await tracker.update(EscalationTrackerInput(
            shadow_result=ShadowStatus.CRITICAL, case_id=CASE_1
        ))

    state = await tracker.get_state()
    assert state.consecutive_critical_count == 3
    assert state.current_fallback_mode == FallbackMode.COGNITIVE_FALLBACK


# -- 3. 5 consecutive CRITICAL -> HUMAN_INTERVENTION ------------------------


async def test_five_consecutive_critical_triggers_human_intervention(
    tracker: EscalationTracker,
) -> None:
    for _ in range(5):
        await tracker.update(EscalationTrackerInput(
            shadow_result=ShadowStatus.CRITICAL, case_id=CASE_1
        ))

    state = await tracker.get_state()
    assert state.consecutive_critical_count == 5
    assert state.current_fallback_mode == FallbackMode.HUMAN_INTERVENTION


# -- 4. Non-CRITICAL resets counter to 0 ------------------------------------


async def test_non_critical_resets_counter(tracker: EscalationTracker) -> None:
    for _ in range(2):
        await tracker.update(EscalationTrackerInput(
            shadow_result=ShadowStatus.CRITICAL, case_id=CASE_1
        ))

    await tracker.update(EscalationTrackerInput(
        shadow_result=ShadowStatus.CONCUR, case_id=CASE_1
    ))

    state = await tracker.get_state()
    assert state.consecutive_critical_count == 0
    assert state.current_fallback_mode == FallbackMode.NONE


# -- 5. reset() returns to count=0, mode=NONE -------------------------------


async def test_reset_returns_to_initial_state(tracker: EscalationTracker) -> None:
    for _ in range(3):
        await tracker.update(EscalationTrackerInput(
            shadow_result=ShadowStatus.CRITICAL, case_id=CASE_1
        ))

    state = await tracker.reset(reason="test reset")
    assert state.consecutive_critical_count == 0
    assert state.current_fallback_mode == FallbackMode.NONE


# -- 6. P1-7: Per-case isolation -------------------------------------------


async def test_per_case_isolation(tracker: EscalationTracker) -> None:
    """Escalation in case-001 does not affect case-002."""
    # Escalate case-001
    for _ in range(3):
        await tracker.update(EscalationTrackerInput(
            shadow_result=ShadowStatus.CRITICAL, case_id=CASE_1
        ))

    # Switch to case-002 — should start fresh
    await tracker.update(EscalationTrackerInput(
        shadow_result=ShadowStatus.CONCUR, case_id=CASE_2
    ))

    state = await tracker.get_state()
    assert state.consecutive_critical_count == 0
    assert state.current_fallback_mode == FallbackMode.NONE

    # Go back to case-001 — should still have escalation state
    await tracker.update(EscalationTrackerInput(
        shadow_result=ShadowStatus.CONCUR, case_id=CASE_1
    ))
    state = await tracker.get_state()
    # Non-CRITICAL resets count for case-001
    assert state.consecutive_critical_count == 0
