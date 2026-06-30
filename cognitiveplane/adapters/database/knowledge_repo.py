"""Knowledge Postgres repo — Phase 1 stub. Phase 2 swaps in pgvector + Milvus."""

from __future__ import annotations

from cognitiveplane.adapters.database.engine import AsyncDatabaseEngine
from cognitiveplane.shared.dto.knowledge import KnowledgeResult, RAGQuery
from cognitiveplane.shared.enums import KnowledgeType
from cognitiveplane.shared.ports.knowledge import KnowledgeRepository
from cognitiveplane.shared.types import KnowledgeId


class PostgresKnowledgeRepository(KnowledgeRepository):
    def __init__(self, engine: AsyncDatabaseEngine) -> None:
        self._engine = engine
        self._entries: dict[str, dict] = {}

    async def rag_query(self, query: RAGQuery) -> list[KnowledgeResult]:
        return []

    async def find_by_id(self, knowledge_id: KnowledgeId) -> dict | None:
        return self._entries.get(str(knowledge_id.value))

    async def find_by_type(self, knowledge_type: KnowledgeType) -> list[dict]:
        return [
            e
            for e in self._entries.values()
            if e.get("knowledge_type") == knowledge_type.value
        ]
