"""ReasoningModeSelector — choose ReasoningMode based on ContextSnapshot.

Spec §5: extracted from legacy BrainOrchestrator._select_reasoning_mode
(orchestrator.py deleted 2026-06-26 as DEAD CODE; ReActEngine is now the
single L1 executor).
"""

from cognitiveplane.shared.dto.context import ContextSnapshot
from cognitiveplane.shared.enums import NoveltyLevel, ReasoningMode


class ReasoningModeSelector:
    """Pure function selector for ReasoningMode."""

    _MAP = {
        NoveltyLevel.KNOWN: ReasoningMode.ROUTINE,
        NoveltyLevel.PARTIAL: ReasoningMode.ADAPTIVE,
        NoveltyLevel.UNKNOWN: ReasoningMode.EXPLORATORY,
    }

    @classmethod
    def select(cls, context: ContextSnapshot) -> ReasoningMode:
        return cls._MAP.get(context.event_novelty, ReasoningMode.ADAPTIVE)
