"""In-memory implementation of LearningEventRepository for testing and development.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 6.6).
"""

from cognitiveplane.shared.dto_learning import LearningEvent
from cognitiveplane.shared.enums import ProcessingStatus
from cognitiveplane.shared.ports.learning import LearningEventRepository
from cognitiveplane.shared.types import DecisionId, LearningEventId


class InMemoryLearningEventRepository(LearningEventRepository):
    """In-memory repository backed by plain dicts.

    Suitable for unit tests and local development.  Not thread-safe.
    """

    def __init__(self) -> None:
        self._store: dict[str, LearningEvent] = {}
        self._decision_index: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # Repository interface
    # ------------------------------------------------------------------

    async def save(self, event: LearningEvent) -> None:
        key = str(event.learning_event_id.value)
        self._store[key] = event

        # Maintain the decision-id reverse index
        if event.source_decision_id is not None:
            dec_key = str(event.source_decision_id.value)
            self._decision_index.setdefault(dec_key, []).append(key)

    async def find_by_id(
        self, learning_event_id: LearningEventId
    ) -> LearningEvent | None:
        key = str(learning_event_id.value)
        return self._store.get(key)

    async def find_pending(self) -> list[LearningEvent]:
        return [
            event
            for event in self._store.values()
            if event.processing_status == ProcessingStatus.PENDING
        ]

    async def find_by_decision_id(
        self, decision_id: DecisionId
    ) -> list[LearningEvent]:
        dec_key = str(decision_id.value)
        keys = self._decision_index.get(dec_key, [])
        return [self._store[k] for k in keys if k in self._store]

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Reset all internal storage. Required for test isolation."""
        self._store.clear()
        self._decision_index.clear()
