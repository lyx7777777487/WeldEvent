"""memory/search.py — HybridMemorySearch + RRF tests (spec §骨架 line 954, §10).

The RRF formula is the only piece of math we ship in Phase 1; getting
it wrong silently degrades Phase 2g's hybrid retrieval. These tests
pin:

1. Vector-only and FTS-only paths return their respective channels.
2. Documents present in both channels score higher than documents in
   only one channel (sum-of-reciprocals property).
3. Document order respects the RRF score (lower combined rank wins).
4. `top_k` truncates the merged list.
5. Custom weights bias the fusion correctly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from cognitiveplane.memory.search import (
    DEFAULT_RRF_K,
    HybridMemorySearch,
    HybridSearchConfig,
)
from cognitiveplane.shared.dto_memory import (
    MemorySearchQuery,
    MemorySearchResult,
)
from cognitiveplane.shared.enums import PromotionStatus
from cognitiveplane.shared.ports.memory import (
    MemorySearchInput,
    MemorySearchOutput,
    MemorySearchPort,
)
from cognitiveplane.shared.types import MemoryId


def _result(uuid_seed: int, summary: str = "x") -> MemorySearchResult:
    """Construct a `MemorySearchResult` with a stable, predictable id."""
    return MemorySearchResult(
        memory_id=MemoryId(value=UUID(int=uuid_seed)),
        content={
            "summary": summary,
            "details": {},
            "feature_vector": [],
        },
        similarity_score=0.5,
        promotion_status=PromotionStatus.VALIDATED,
        created_at=datetime.now(timezone.utc),
    )


class _StubPort(MemorySearchPort):
    def __init__(self, results: list[MemorySearchResult]) -> None:
        self._results = results

    async def search(
        self, input_data: MemorySearchInput
    ) -> MemorySearchOutput:
        return MemorySearchOutput(results=list(self._results))


def _query() -> MemorySearchQuery:
    return MemorySearchQuery(case_features={"x": 1}, feature_vector=[0.0])


@pytest.mark.asyncio
async def test_returns_empty_when_both_channels_empty():
    hybrid = HybridMemorySearch(_StubPort([]), _StubPort([]))
    out = await hybrid.search(_query())
    assert out == []


@pytest.mark.asyncio
async def test_vector_only_passes_through():
    a, b = _result(1), _result(2)
    hybrid = HybridMemorySearch(_StubPort([a, b]), _StubPort([]))
    out = await hybrid.search(_query())
    assert [r.memory_id for r in out] == [a.memory_id, b.memory_id]


@pytest.mark.asyncio
async def test_fts_only_passes_through():
    a, b = _result(1), _result(2)
    hybrid = HybridMemorySearch(_StubPort([]), _StubPort([a, b]))
    out = await hybrid.search(_query())
    assert [r.memory_id for r in out] == [a.memory_id, b.memory_id]


@pytest.mark.asyncio
async def test_doc_in_both_channels_outranks_doc_in_one():
    """RRF property: A appears in both → score higher than B in only one."""
    a, b = _result(1), _result(2)
    # A is rank 2 in vector and rank 1 in FTS.
    # B is rank 1 in vector and absent from FTS.
    hybrid = HybridMemorySearch(_StubPort([b, a]), _StubPort([a]))
    out = await hybrid.search(_query())
    assert out[0].memory_id == a.memory_id


@pytest.mark.asyncio
async def test_top_k_truncates_merged_list():
    docs = [_result(i) for i in range(1, 31)]
    cfg = HybridSearchConfig(top_k=5)
    hybrid = HybridMemorySearch(_StubPort(docs), _StubPort(docs), cfg)
    out = await hybrid.search(_query())
    assert len(out) == 5


@pytest.mark.asyncio
async def test_rrf_score_matches_formula():
    """For two docs each at rank 1 in their respective single channel,
    score = w / (k + 1). Equal weights → tie. Increasing one weight
    flips the order."""
    a, b = _result(1), _result(2)
    cfg_equal = HybridSearchConfig(vector_weight=0.5, fts_weight=0.5, top_k=2)
    hybrid = HybridMemorySearch(_StubPort([a]), _StubPort([b]), cfg_equal)
    out_equal = await hybrid.search(_query())
    ids = {str(r.memory_id.value) for r in out_equal}
    assert ids == {str(a.memory_id.value), str(b.memory_id.value)}

    cfg_vec_heavy = HybridSearchConfig(
        vector_weight=0.9, fts_weight=0.1, top_k=2
    )
    hybrid2 = HybridMemorySearch(_StubPort([a]), _StubPort([b]), cfg_vec_heavy)
    out_vec_heavy = await hybrid2.search(_query())
    assert out_vec_heavy[0].memory_id == a.memory_id


@pytest.mark.asyncio
async def test_dedup_uses_memory_id_not_object_identity():
    """Same `memory_id` across channels → counted once with summed score."""
    same_id = MemoryId(value=UUID(int=42))
    base = _result(42)
    # Re-create a different object with the SAME id to simulate two
    # adapters returning the same row.
    from copy import deepcopy

    twin = deepcopy(base)

    other = _result(99)
    hybrid = HybridMemorySearch(
        _StubPort([base, other]),  # vector: base@1, other@2
        _StubPort([twin]),  # fts:    twin@1 (same id as base)
    )
    out = await hybrid.search(_query())
    ids = [str(r.memory_id.value) for r in out]
    assert ids.count(str(same_id.value)) == 1
    # `base` got vector@1 + fts@1; `other` only vector@2 → base must rank first.
    assert ids[0] == str(same_id.value)


def test_default_k_is_60():
    assert DEFAULT_RRF_K == 60


def test_default_weights_are_equal():
    cfg = HybridSearchConfig()
    assert cfg.vector_weight == cfg.fts_weight == 0.5
