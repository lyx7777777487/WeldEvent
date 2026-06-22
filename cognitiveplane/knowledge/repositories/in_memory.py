"""In-memory implementation of KnowledgeRepository for testing and development.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 6.4).
"""

from cognitiveplane.shared.dto_knowledge import KnowledgeResult, RAGQuery
from cognitiveplane.shared.enums import KnowledgeType
from cognitiveplane.shared.ports.knowledge import KnowledgeRepository
from cognitiveplane.shared.types import KnowledgeId


class InMemoryKnowledgeRepository(KnowledgeRepository):
    """In-memory repository backed by plain dicts.

    Suitable for unit tests and local development.  Not thread-safe.
    """

    def __init__(self) -> None:
        self._store: dict[str, dict] = {}
        self._type_index: dict[KnowledgeType, list[str]] = {}

    # ------------------------------------------------------------------
    # Repository interface
    # ------------------------------------------------------------------

    async def rag_query(self, query: RAGQuery) -> list[KnowledgeResult]:
        """Naive RAG query -- returns first N stored items regardless of relevance."""
        results: list[KnowledgeResult] = []
        for entry in self._store.values():
            results.append(KnowledgeResult(**entry))
            if len(results) >= query.max_results:
                break
        return results

    async def find_by_id(self, knowledge_id: KnowledgeId) -> dict | None:
        key = str(knowledge_id.value)
        return self._store.get(key)

    async def find_by_type(
        self, knowledge_type: KnowledgeType
    ) -> list[dict]:
        keys = self._type_index.get(knowledge_type, [])
        return [self._store[k] for k in keys if k in self._store]

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Reset all internal storage. Required for test isolation."""
        self._store.clear()
        self._type_index.clear()
