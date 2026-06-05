"""In-memory implementation of MemoryRepository for testing and development.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 6.3).
"""

from datetime import datetime

from src.shared.dto_memory import MemoryRecord, MemorySearchQuery, MemorySearchResult
from src.shared.enums import PromotionStatus
from src.shared.ports.memory import MemoryRepository
from src.shared.types import DecisionId, MemoryId


class InMemoryMemoryRepository(MemoryRepository):
    """In-memory repository backed by plain dicts.

    Suitable for unit tests and local development.  Not thread-safe.
    """

    def __init__(self) -> None:
        self._store: dict[str, MemoryRecord] = {}
        self._decision_index: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # Repository interface
    # ------------------------------------------------------------------

    async def store(self, record: MemoryRecord) -> MemoryId:
        key = str(record.memory_id.value)
        self._store[key] = record

        # Maintain the decision-id reverse index
        dec_key = str(record.source_decision_id.value)
        self._decision_index.setdefault(dec_key, []).append(key)

        return record.memory_id

    async def search(self, query: MemorySearchQuery) -> list[MemorySearchResult]:
        """Naive search -- filters by *status_filter* only, returns first N matches.

        No real vector similarity is computed; every matching record receives a
        similarity_score of 1.0.
        """
        results: list[MemorySearchResult] = []
        for record in self._store.values():
            if record.promotion_status not in query.status_filter:
                continue
            results.append(
                MemorySearchResult(
                    memory_id=record.memory_id,
                    content=record.content,
                    similarity_score=1.0,
                    promotion_status=record.promotion_status,
                    created_at=record.created_at,
                )
            )
            if len(results) >= query.max_results:
                break
        return results

    async def find_by_id(self, memory_id: MemoryId) -> MemoryRecord | None:
        return self._store.get(str(memory_id.value))

    async def find_by_source_decision(
        self, decision_id: DecisionId
    ) -> list[MemoryRecord]:
        keys = self._decision_index.get(str(decision_id.value), [])
        return [self._store[k] for k in keys if k in self._store]

    async def update_promotion_status(
        self, memory_id: MemoryId, status: PromotionStatus
    ) -> None:
        key = str(memory_id.value)
        record = self._store.get(key)
        if record is None:
            return
        self._store[key] = record.model_copy(
            update={"promotion_status": status}
        )

    async def archive(self, memory_id: MemoryId, reason: str) -> None:
        key = str(memory_id.value)
        record = self._store.get(key)
        if record is None:
            return
        now = datetime.utcnow()
        self._store[key] = record.model_copy(
            update={
                "promotion_status": PromotionStatus.ARCHIVED,
                "archived_at": now,
            }
        )

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Reset all internal storage. Required for test isolation."""
        self._store.clear()
        self._decision_index.clear()
