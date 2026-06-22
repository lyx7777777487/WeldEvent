"""Memory Postgres repo — Phase 1 stub.

Phase 1: in-memory dict, satisfies `MemoryRepository`.
Phase 2: switch to PG (L2-L5) with Redis (L0-L1) cache via `redis_client`.
"""

from __future__ import annotations

from cognitiveplane.adapters.database.engine import AsyncDatabaseEngine
from cognitiveplane.shared.dto_memory import (
    MemoryRecord,
    MemorySearchQuery,
    MemorySearchResult,
)
from cognitiveplane.shared.enums import PromotionStatus
from cognitiveplane.shared.ports.memory import MemoryRepository
from cognitiveplane.shared.types import DecisionId, MemoryId


class PostgresMemoryRepository(MemoryRepository):
    def __init__(self, engine: AsyncDatabaseEngine) -> None:
        self._engine = engine
        self._records: dict[str, MemoryRecord] = {}

    async def store(self, record: MemoryRecord) -> MemoryId:
        async with self._engine.session():
            self._records[str(record.memory_id.value)] = record
        return record.memory_id

    async def search(
        self, query: MemorySearchQuery
    ) -> list[MemorySearchResult]:
        # Phase 1: return all records matching status filter, no scoring
        out: list[MemorySearchResult] = []
        for r in self._records.values():
            if query.status_filter and r.promotion_status not in query.status_filter:
                continue
            if query.memory_type is not None and r.memory_type != query.memory_type:
                continue
            out.append(
                MemorySearchResult(
                    memory_id=r.memory_id,
                    content=r.content,
                    similarity_score=0.5,
                    promotion_status=r.promotion_status,
                    created_at=r.created_at,
                )
            )
            if len(out) >= query.max_results:
                break
        return out

    async def find_by_id(self, memory_id: MemoryId) -> MemoryRecord | None:
        return self._records.get(str(memory_id.value))

    async def find_by_source_decision(
        self, decision_id: DecisionId
    ) -> list[MemoryRecord]:
        return [
            r
            for r in self._records.values()
            if r.source_decision_id == decision_id
        ]

    async def update_promotion_status(
        self, memory_id: MemoryId, status: PromotionStatus
    ) -> None:
        record = self._records.get(str(memory_id.value))
        if record is None:
            return
        self._records[str(memory_id.value)] = record.model_copy(
            update={"promotion_status": status}
        )

    async def archive(self, memory_id: MemoryId, reason: str) -> None:
        record = self._records.get(str(memory_id.value))
        if record is None:
            return
        from datetime import datetime, timezone

        self._records[str(memory_id.value)] = record.model_copy(
            update={"archived_at": datetime.now(timezone.utc)}
        )
