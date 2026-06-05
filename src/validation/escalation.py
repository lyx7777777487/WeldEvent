"""EscalationTracker -- tracks consecutive CRITICAL shadow results and
escalates the fallback mode accordingly.

Thresholds (per Phase 4 Section 9.6):
  - 3 consecutive CRITICAL  ->  COGNITIVE_FALLBACK
  - 5 consecutive CRITICAL  ->  HUMAN_INTERVENTION
"""

from src.shared.enums import FallbackMode, ShadowStatus
from src.shared.ports.validation import (
    EscalationState,
    EscalationTrackerInput,
    EscalationTrackerOutput,
    EscalationTrackerPort,
)


class EscalationTracker(EscalationTrackerPort):
    """In-memory escalation state machine."""

    def __init__(self) -> None:
        self._count: int = 0
        self._mode: FallbackMode = FallbackMode.NONE

    # ------------------------------------------------------------------
    # Port interface
    # ------------------------------------------------------------------

    async def update(self, input_data: EscalationTrackerInput) -> EscalationTrackerOutput:
        previous_state = self._state()

        if input_data.shadow_result == ShadowStatus.CRITICAL:
            self._count += 1
        else:
            self._count = 0

        self._evaluate_thresholds()

        new_state = self._state()
        return EscalationTrackerOutput(
            previous_state=previous_state,
            new_state=new_state,
            mode_changed=previous_state.current_fallback_mode != new_state.current_fallback_mode,
        )

    async def get_state(self) -> EscalationState:
        return self._state()

    async def reset(self, reason: str) -> EscalationState:
        self._count = 0
        self._mode = FallbackMode.NONE
        return self._state()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _state(self) -> EscalationState:
        return EscalationState(
            consecutive_critical_count=self._count,
            current_fallback_mode=self._mode,
        )

    def _evaluate_thresholds(self) -> None:
        if self._count >= 5:
            self._mode = FallbackMode.HUMAN_INTERVENTION
        elif self._count >= 3:
            self._mode = FallbackMode.COGNITIVE_FALLBACK
        else:
            self._mode = FallbackMode.NONE
