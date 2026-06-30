"""Tests for InMemoryMemoryRepository.

Covers: store, find_by_id, find_by_id miss, search by status_filter,
find_by_source_decision, update_promotion_status.
"""

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from cognitiveplane.shared.dto.memory import MemoryContent, MemoryRecord, MemorySearchQuery
from cognitiveplane.shared.enums import MemoryType, PromotionStatus
from cognitiveplane.shared.types import DecisionId, MemoryId
from cognitiveplane.memory.repositories.in_memory import InMemoryMemoryRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_memory_id() -> MemoryId:
    return MemoryId(value=uuid4())


def _make_decision_id() -> DecisionId:
    return DecisionId(value=uuid4())


def _make_record(
    memory_id: MemoryId | None = None,
    source_decision_id: DecisionId | None = None,
    promotion_status: PromotionStatus = PromotionStatus.RAW,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id or _make_memory_id(),
        memory_type=MemoryType.APPROVED_DECISION,
        content=MemoryContent(
            summary="test summary",
            details={"key": "value"},
            feature_vector=[0.1, 0.2, 0.3],
        ),
        source_decision_id=source_decision_id or _make_decision_id(),
        promotion_status=promotion_status,
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def repo() -> InMemoryMemoryRepository:
    return InMemoryMemoryRepository()


# ===================================================================
# Test cases
# ===================================================================


class TestStore:
    """store() returns MemoryId."""

    @pytest.mark.asyncio
    async def test_store_returns_memory_id(self, repo: InMemoryMemoryRepository):
        record = _make_record()
        result = await repo.store(record)
        assert isinstance(result, MemoryId)
        assert result.value == record.memory_id.value


class TestFindById:
    """find_by_id() returns stored record or None."""

    @pytest.mark.asyncio
    async def test_find_by_id_returns_stored_record(
        self, repo: InMemoryMemoryRepository
    ):
        record = _make_record()
        await repo.store(record)
        found = await repo.find_by_id(record.memory_id)
        assert found is not None
        assert found.memory_id.value == record.memory_id.value

    @pytest.mark.asyncio
    async def test_find_by_id_returns_none_for_unknown(
        self, repo: InMemoryMemoryRepository
    ):
        unknown_id = _make_memory_id()
        found = await repo.find_by_id(unknown_id)
        assert found is None


class TestSearch:
    """search() returns records matching status_filter."""

    @pytest.mark.asyncio
    async def test_search_returns_matching_status(
        self, repo: InMemoryMemoryRepository
    ):
        raw_record = _make_record(promotion_status=PromotionStatus.RAW)
        promoted_record = _make_record(promotion_status=PromotionStatus.PROMOTED)
        validated_record = _make_record(promotion_status=PromotionStatus.VALIDATED)
        await repo.store(raw_record)
        await repo.store(promoted_record)
        await repo.store(validated_record)

        query = MemorySearchQuery(
            case_features={},
            feature_vector=[0.0],
            status_filter=[PromotionStatus.PROMOTED, PromotionStatus.VALIDATED],
        )
        results = await repo.search(query)
        result_ids = {r.memory_id.value for r in results}
        assert promoted_record.memory_id.value in result_ids
        assert validated_record.memory_id.value in result_ids
        assert raw_record.memory_id.value not in result_ids


class TestFindBySourceDecision:
    """find_by_source_decision() returns indexed records."""

    @pytest.mark.asyncio
    async def test_find_by_source_decision(
        self, repo: InMemoryMemoryRepository
    ):
        decision_id = _make_decision_id()
        r1 = _make_record(source_decision_id=decision_id)
        r2 = _make_record(source_decision_id=decision_id)
        other = _make_record()  # different decision
        await repo.store(r1)
        await repo.store(r2)
        await repo.store(other)

        found = await repo.find_by_source_decision(decision_id)
        found_ids = {r.memory_id.value for r in found}
        assert r1.memory_id.value in found_ids
        assert r2.memory_id.value in found_ids
        assert other.memory_id.value not in found_ids


class TestUpdatePromotionStatus:
    """update_promotion_status() changes status."""

    @pytest.mark.asyncio
    async def test_update_promotion_status(
        self, repo: InMemoryMemoryRepository
    ):
        record = _make_record(promotion_status=PromotionStatus.RAW)
        await repo.store(record)

        await repo.update_promotion_status(
            record.memory_id, PromotionStatus.VALIDATED
        )

        updated = await repo.find_by_id(record.memory_id)
        assert updated is not None
        assert updated.promotion_status == PromotionStatus.VALIDATED
