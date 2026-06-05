"""Memory confidence domain service.

Stub implementation -- returns a fixed confidence value.  A real
implementation would compute similarity against stored memories.
"""

from src.shared.dto_context import ContextSnapshot


class MemoryConfidenceService:
    """Stub -- returns fixed confidence. Real implementation would compute similarity."""

    async def compute(self, context: ContextSnapshot) -> float:
        return 0.5
