"""PersonaSelector — choose Persona based on ContextSnapshot.

Spec §5: extracted from BrainOrchestrator._select_persona.
"""

from cognitiveplane.shared.dto_context import ContextSnapshot
from cognitiveplane.shared.enums import NoveltyLevel, PersonaType


class PersonaSelector:
    """Pure function selector for Persona.

    Rules:
    - UNKNOWN novelty → CAA (cognitive autonomous agent — exploration)
    - any validation_critical_count > 0 → CAA (safety override)
    - PARTIAL novelty → PLANNER
    - else (KNOWN) → COPILOT
    """

    @staticmethod
    def select(context: ContextSnapshot) -> PersonaType:
        if context.event_novelty == NoveltyLevel.UNKNOWN:
            return PersonaType.CAA
        if context.validation_critical_count > 0:
            return PersonaType.CAA
        if context.event_novelty == NoveltyLevel.PARTIAL:
            return PersonaType.PLANNER
        return PersonaType.COPILOT
