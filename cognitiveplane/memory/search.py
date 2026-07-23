"""Memory plane — hybrid search (spec §骨架 line 954, §10 RRF fusion).

Combines vector similarity and full-text search via Reciprocal Rank
Fusion (RRF). Adapter wiring (Milvus + PG FTS) lands in Phase 2e/2g;
this module is the algorithmic core that callers use today against any
`MemorySearchPort` implementation.

RRF formula (Letta-inspired, k=60):
    score(d) = sum_i  weight_i / (k + rank_i(d))

Where each `i` is a retrieval channel (vector / FTS) and `rank_i(d)` is
the 1-based rank of document `d` in channel `i` (∞ if not present).
"""

from __future__ import annotations

from dataclasses import dataclass

from cognitiveplane.shared.dto.memory import (
    MemorySearchQuery,
    MemorySearchResult,
)
from cognitiveplane.shared.ports.memory import (
    MemorySearchInput,
    MemorySearchPort,
)

DEFAULT_RRF_K = 60
DEFAULT_VECTOR_WEIGHT = 0.5
DEFAULT_FTS_WEIGHT = 0.5


@dataclass(frozen=True)
class HybridSearchConfig:
    rrf_k: int = DEFAULT_RRF_K
    vector_weight: float = DEFAULT_VECTOR_WEIGHT
    fts_weight: float = DEFAULT_FTS_WEIGHT
    top_k: int = 20


class HybridMemorySearch:
    """Vector + FTS retrieval fused via RRF.

    Phase 1 wires both channels to the same `MemorySearchPort` (the
    in-memory adapter only does keyword matching today). Phase 2g
    splits them into a true vector channel + true FTS channel; the RRF
    math here stays unchanged.
    """

    def __init__(
        self,
        vector_port: MemorySearchPort,
        fts_port: MemorySearchPort,
        config: HybridSearchConfig | None = None,
    ) -> None:
        self._vector = vector_port
        self._fts = fts_port
        self._config = config or HybridSearchConfig()

    async def search(self, query: MemorySearchQuery) -> list[MemorySearchResult]:
        vec = await self._vector.search(MemorySearchInput(query=query))
        fts = await self._fts.search(MemorySearchInput(query=query))
        merged = self._fuse(vec.results, fts.results)
        # Op 8.1: Filter by memory_type if specified
        if query.memory_type is not None:
            merged = [
                r for r in merged
                if getattr(r.content, "memory_type", None) == query.memory_type
                or getattr(r, "memory_type", None) == query.memory_type
            ]
        return merged[: self._config.top_k]

    def _fuse(
        self,
        vector_hits: list[MemorySearchResult],
        fts_hits: list[MemorySearchResult],
    ) -> list[MemorySearchResult]:
        cfg = self._config
        scores: dict[str, float] = {}
        index: dict[str, MemorySearchResult] = {}

        for rank, hit in enumerate(vector_hits, start=1):
            key = str(hit.memory_id)
            index.setdefault(key, hit)
            scores[key] = scores.get(key, 0.0) + cfg.vector_weight / (cfg.rrf_k + rank)

        for rank, hit in enumerate(fts_hits, start=1):
            key = str(hit.memory_id)
            index.setdefault(key, hit)
            scores[key] = scores.get(key, 0.0) + cfg.fts_weight / (cfg.rrf_k + rank)

        ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return [index[k] for k, _ in ordered]


__all__ = [
    "DEFAULT_FTS_WEIGHT",
    "DEFAULT_RRF_K",
    "DEFAULT_VECTOR_WEIGHT",
    "HybridMemorySearch",
    "HybridSearchConfig",
]
