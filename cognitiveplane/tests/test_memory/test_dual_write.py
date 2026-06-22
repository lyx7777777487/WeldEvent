"""Tests for memory/dual_write.py — DualWriteMemoryService with RRF fusion."""

from uuid import uuid4

import pytest

from cognitiveplane.memory.dual_write import DualWriteMemoryService, VectorStorePort
from cognitiveplane.shared.dto_memory import MemoryContent, MemorySearchQuery, MemorySearchResult
from cognitiveplane.shared.enums import MemoryType, PromotionStatus
from cognitiveplane.shared.types import MemoryId


def _make_memory_id() -> MemoryId:
    return MemoryId(value=uuid4())


def _make_result(mid: MemoryId, score: float = 0.9) -> MemorySearchResult:
    return MemorySearchResult(
        memory_id=mid,
        content=MemoryContent(summary="test", details={}, feature_vector=[0.1, 0.2]),
        similarity_score=score,
        promotion_status=PromotionStatus.VALIDATED,
        created_at="2026-01-01T00:00:00Z",
    )


class MockVectorStore:
    """Mock vector store that is available and returns IDs."""

    def __init__(self, available: bool = True, ids: list[str] | None = None):
        self._available = available
        self._ids = ids or []

    def is_available(self) -> bool:
        return self._available

    async def upsert(self, id: str, vector: list[float], metadata: dict) -> None:
        self._ids.append(id)

    async def search(self, vector: list[float], limit: int, filter_tags=None) -> list[str]:
        return self._ids[:limit]


class MockPGRepo:
    """Mock PG repo with save and search."""

    def __init__(self, records: list | None = None):
        self._records = records or []
        self._next_id = 1

    async def save(self, record) -> str:
        rid = f"mem-{self._next_id}"
        self._next_id += 1
        self._records.append(record)
        return rid

    async def search(self, query: MemorySearchQuery) -> list[MemorySearchResult]:
        return self._records[: query.max_results]


class TestDualWriteStore:
    @pytest.mark.asyncio
    async def test_store_pg_only(self):
        pg = MockPGRepo()
        svc = DualWriteMemoryService(pg_repo=pg, vector_store=None)

        class FakeRecord:
            memory_id = _make_memory_id()

        result_id = await svc.store(FakeRecord())
        assert result_id.startswith("mem-")

    @pytest.mark.asyncio
    async def test_store_with_vector(self):
        pg = MockPGRepo()
        vs = MockVectorStore()
        svc = DualWriteMemoryService(pg_repo=pg, vector_store=vs)

        class FakeRecord:
            memory_id = _make_memory_id()
            content = MemoryContent(summary="test", details={}, feature_vector=[0.1, 0.2])
            case_id = "W-001"
            memory_type = MemoryType.APPROVED_DECISION

        result_id = await svc.store(FakeRecord())
        assert result_id.startswith("mem-")
        assert len(vs._ids) == 1

    @pytest.mark.asyncio
    async def test_store_vector_unavailable_falls_back(self):
        pg = MockPGRepo()
        vs = MockVectorStore(available=False)
        svc = DualWriteMemoryService(pg_repo=pg, vector_store=vs)

        class FakeRecord:
            memory_id = _make_memory_id()

        result_id = await svc.store(FakeRecord())
        assert result_id.startswith("mem-")


class TestDualWriteSearch:
    @pytest.mark.asyncio
    async def test_search_pg_only_no_vector(self):
        mid = _make_memory_id()
        pg = MockPGRepo(records=[_make_result(mid)])
        svc = DualWriteMemoryService(pg_repo=pg, vector_store=None)

        query = MemorySearchQuery(
            case_features={}, feature_vector=[], max_results=10
        )
        results = await svc.search(query)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_hybrid_with_rrf(self):
        mid1 = _make_memory_id()
        mid2 = _make_memory_id()
        results_list = [_make_result(mid1, 0.9), _make_result(mid2, 0.7)]
        pg = MockPGRepo(records=results_list)
        vs = MockVectorStore(available=True, ids=[str(mid1.value)])
        svc = DualWriteMemoryService(pg_repo=pg, vector_store=vs)

        query = MemorySearchQuery(
            case_features={}, feature_vector=[0.1, 0.2], max_results=10
        )
        results = await svc.search(query)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_search_vector_failure_falls_back_to_pg(self):
        mid = _make_memory_id()
        pg = MockPGRepo(records=[_make_result(mid)])

        class FailingVectorStore:
            def is_available(self) -> bool:
                return True

            async def search(self, vector, limit, filter_tags=None):
                raise RuntimeError("vector store down")

        svc = DualWriteMemoryService(pg_repo=pg, vector_store=FailingVectorStore())

        query = MemorySearchQuery(
            case_features={}, feature_vector=[0.1, 0.2], max_results=10
        )
        results = await svc.search(query)
        assert len(results) == 1


class TestRRFFusion:
    def test_rrf_fuse_basic(self):
        mid1 = _make_memory_id()
        mid2 = _make_memory_id()
        r1 = _make_result(mid1, 0.9)
        r2 = _make_result(mid2, 0.7)

        fused = DualWriteMemoryService._rrf_fuse([r1], [r1, r2], k=60)
        assert len(fused) == 2
        # r1 appears in both lists so should rank higher
        assert fused[0].memory_id == mid1

    def test_rrf_fuse_empty_inputs(self):
        fused = DualWriteMemoryService._rrf_fuse([], [], k=60)
        assert fused == []

    def test_rrf_fuse_single_list(self):
        mid = _make_memory_id()
        r = _make_result(mid)
        fused = DualWriteMemoryService._rrf_fuse([r], [], k=60)
        assert len(fused) == 1
