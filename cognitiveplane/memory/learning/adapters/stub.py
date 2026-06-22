"""Stub learning adapter that returns seeded or empty responses.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 6.6).
Implements LearningEventRepository ABC so it can be wired through the
port system without AttributeError.
"""

from datetime import datetime, timezone

from cognitiveplane.shared.dto_learning import LearningContent, LearningEvent
from cognitiveplane.shared.enums import LearningEventType, ProcessingStatus
from cognitiveplane.shared.ports.learning import LearningEventRepository
from cognitiveplane.shared.types import DecisionId, LearningEventId


class StubLearningAdapter(LearningEventRepository):
    """Stub adapter implementing LearningEventRepository.

    Stores events in an in-memory dict.  Accepts optional ``seed_responses``
    dict for Phase 5B harness testability.
    """

    def __init__(
        self, seed_responses: dict[str, list] | None = None
    ) -> None:
        self._seed = seed_responses or {}
        self._store: dict[LearningEventId, LearningEvent] = {}

    def clear(self) -> None:
        """Reset seed responses and store. Required for test isolation."""
        self._seed = {}
        self._store.clear()

    # ------------------------------------------------------------------
    # LearningEventRepository ABC
    # ------------------------------------------------------------------

    async def save(self, event: LearningEvent) -> None:
        self._store[event.learning_event_id] = event

    async def find_by_id(
        self, learning_event_id: LearningEventId
    ) -> LearningEvent | None:
        return self._store.get(learning_event_id)

    async def find_pending(self) -> list[LearningEvent]:
        return [
            e for e in self._store.values()
            if e.processing_status == ProcessingStatus.PENDING
        ]

    async def find_by_decision_id(
        self, decision_id: DecisionId
    ) -> list[LearningEvent]:
        return [
            e for e in self._store.values()
            if e.source_decision_id == decision_id
        ]

    # ------------------------------------------------------------------
    # Legacy seed-based query methods (for backward compat with tests)
    # ------------------------------------------------------------------

    async def query_learning_events(self) -> list:
        return self._seed.get("query_learning_events", [])

    async def query_pending_learning(self) -> list:
        return self._seed.get("query_pending_learning", [])

    async def query_promotion_candidates(self) -> list:
        return self._seed.get("query_promotion_candidates", [])