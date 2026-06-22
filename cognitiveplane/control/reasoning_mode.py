"""ReasoningModeSelector — choose ReasoningMode based on ContextSnapshot.

Spec §5: extracted from BrainOrchestrator._select_reasoning_mode.
"""

from cognitiveplane.shared.dto_context import ContextSnapshot
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
