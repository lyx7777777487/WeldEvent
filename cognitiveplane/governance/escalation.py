"""EscalationTracker -- tracks consecutive CRITICAL shadow results and
escalates the fallback mode accordingly.

Thresholds (per Phase 4 Section 9.6):
  - 3 consecutive CRITICAL  ->  COGNITIVE_FALLBACK
  - 5 consecutive CRITICAL  ->  HUMAN_INTERVENTION

P1-7 fix: Per-case isolation. Each case has its own escalation state
(dict[CaseId, _CaseEscalationState]), preventing cross-case pollution.
"""

from cognitiveplane.shared.enums import FallbackMode, ShadowStatus
from cognitiveplane.shared.ports.validation import (
    EscalationState,
    EscalationTrackerInput,
    EscalationTrackerOutput,
    EscalationTrackerPort,
)
from cognitiveplane.shared.types import CaseId


class _CaseEscalationState:
    """Per-case escalation tracking."""

    __slots__ = ("count", "mode")

    def __init__(self) -> None:
        self.count: int = 0
        self.mode: FallbackMode = FallbackMode.NONE


class EscalationTracker(EscalationTrackerPort):
    """In-memory escalation state machine with per-case isolation.

    P1-7 fix: uses dict[str, _CaseEscalationState] keyed by case_id.value
    instead of single _case_id, so multiple cases can be tracked independently.
    """

    def __init__(self) -> None:
        self._cases: dict[str, _CaseEscalationState] = {}
        self._current_case_key: str | None = None

    # ------------------------------------------------------------------
    # Port interface
    # ------------------------------------------------------------------

    async def update(self, input_data: EscalationTrackerInput) -> EscalationTrackerOutput:
        previous_state = self._state()

        case_id = getattr(input_data, "case_id", None)
        if case_id is not None:
            key = case_id.value if hasattr(case_id, 'value') else str(case_id)
            self._current_case_key = key
            if key not in self._cases:
                self._cases[key] = _CaseEscalationState()

        case_state = self._get_current_case_state()

        if input_data.shadow_result == ShadowStatus.CRITICAL:
            case_state.count += 1
        else:
            case_state.count = 0

        self._evaluate_thresholds(case_state)

        new_state = self._state()
        return EscalationTrackerOutput(
            previous_state=previous_state,
            new_state=new_state,
            mode_changed=previous_state.current_fallback_mode != new_state.current_fallback_mode,
        )

    async def get_state(self) -> EscalationState:
        return self._state()

    async def reset(self, reason: str) -> EscalationState:
        if self._current_case_key and self._current_case_key in self._cases:
            del self._cases[self._current_case_key]
        self._current_case_key = None
        return self._state()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_current_case_state(self) -> _CaseEscalationState:
        if self._current_case_key and self._current_case_key in self._cases:
            return self._cases[self._current_case_key]
        return _CaseEscalationState()

    def _state(self) -> EscalationState:
        case_state = self._get_current_case_state()
        return EscalationState(
            consecutive_critical_count=case_state.count,
            current_fallback_mode=case_state.mode,
        )

    @staticmethod
    def _evaluate_thresholds(case_state: _CaseEscalationState) -> None:
        if case_state.count >= 5:
            case_state.mode = FallbackMode.HUMAN_INTERVENTION
        elif case_state.count >= 3:
            case_state.mode = FallbackMode.COGNITIVE_FALLBACK
        else:
            case_state.mode = FallbackMode.NONE
