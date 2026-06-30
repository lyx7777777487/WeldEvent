"""Conditional dual-write — PG always, vector store when available.

Source: 7-plane redesign spec §10 Dual-Write Persistence.
Modeled after Letta passage_manager.py:606-630.
Phase 1: PG + Redis stub. Phase 2: PG + Milvus.
"""

import logging
from typing import Any, Protocol

from cognitiveplane.shared.dto.memory import MemorySearchQuery, MemorySearchResult
from cognitiveplane.shared.types import MemoryId

logger = logging.getLogger(__name__)


class VectorStorePort(Protocol):
    """Protocol for vector store adapters."""

    def is_available(self) -> bool: ...

    async def upsert(self, id: str, vector: list[float], metadata: dict[str, Any]) -> None: ...

    async def search(self, vector: list[float], limit: int, filter_tags: list[str] | None = None) -> list[str]: ...


class DualWriteMemoryService:
    """Conditional dual-write: PG (always) + vector store (when available).

    Modeled after Letta passage_manager.py:606-630 — dual-write is gated by
    vector_db_provider config, not unconditional.
    """

    def __init__(self, pg_repo: Any, vector_store: VectorStorePort | None = None) -> None:
        self._pg = pg_repo
        self._vector = vector_store

    @property
    def _vector_available(self) -> bool:
        return self._vector is not None and self._vector.is_available()

    async def store(self, record: Any) -> str:
        """Write to PG first (source of truth), then vector store (best-effort).

        Consistent with Letta: PG first, vector second, same IDs across stores.
        """
        # 1. Write to PG first (source of truth)
        if hasattr(self._pg, 'save'):
            pg_id = await self._pg.save(record)
        else:
            pg_id = str(record.memory_id.value) if hasattr(record, 'memory_id') else "unknown"

        # 2. Write to vector store only if available AND record has feature vector
        if self._vector_available:
            feature_vector = getattr(record, 'content', None)
            if feature_vector:
                fv = getattr(feature_vector, 'feature_vector', None)
            else:
                fv = getattr(record, 'feature_vector', None)
            if fv:
                try:
                    await self._vector.upsert(
                        id=pg_id,
                        vector=fv,
                        metadata={
                            "case_id": str(getattr(record, 'case_id', '')),
                            "memory_type": str(getattr(getattr(record, 'memory_type', ''), 'value', '')),
                        },
                    )
                except Exception:
                    logger.warning("Vector store unavailable, PG write succeeded for %s", pg_id)

        return pg_id

    async def search(self, query: MemorySearchQuery) -> list[MemorySearchResult]:
        """Hybrid search when vector store available; PG-only fallback otherwise.

        RRF fusion only applies when both vector and PG results exist.
        Modeled after Letta tpuf_client.py:1489-1560 (k=60, weights=0.5/0.5).
        """
        if not self._vector_available or not query.feature_vector:
            if hasattr(self._pg, 'search'):
                return await self._pg.search(query)
            return []

        # Full hybrid: vector + PG + RRF
        vector_hits: list[str] = []
        try:
            vector_hits = await self._vector.search(
                vector=query.feature_vector,
                limit=query.max_results,
                filter_tags=None,
            )
        except Exception:
            logger.warning("Vector search failed, falling back to PG-only")

        if not vector_hits:
            if hasattr(self._pg, 'search'):
                return await self._pg.search(query)
            return []

        # RRF fusion
        if hasattr(self._pg, 'search'):
            pg_results = await self._pg.search(query)
        else:
            pg_results = []

        vector_results = [r for r in pg_results if str(r.memory_id.value) in vector_hits]
        return self._rrf_fuse(vector_results, pg_results, k=60, vector_weight=0.5, pg_weight=0.5)

    @staticmethod
    def _rrf_fuse(
        vector_results: list[MemorySearchResult],
        pg_results: list[MemorySearchResult],
        k: int = 60,
        vector_weight: float = 0.5,
        pg_weight: float = 0.5,
    ) -> list[MemorySearchResult]:
        """Reciprocal Rank Fusion.

        Parameters from Letta tpuf_client.py (k=60, Cormack et al. 2009).
        Letta uses equal weights (0.5/0.5).
        """
        scores: dict[str, float] = {}
        seen: dict[str, MemorySearchResult] = {}

        for rank, r in enumerate(vector_results):
            rid = str(r.memory_id.value)
            scores[rid] = scores.get(rid, 0.0) + vector_weight / (k + rank + 1)
            if rid not in seen:
                seen[rid] = r

        for rank, r in enumerate(pg_results):
            rid = str(r.memory_id.value)
            scores[rid] = scores.get(rid, 0.0) + pg_weight / (k + rank + 1)
            if rid not in seen:
                seen[rid] = r

        return sorted(seen.values(), key=lambda r: scores.get(str(r.memory_id.value), 0.0), reverse=True)
