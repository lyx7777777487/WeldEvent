"""Tests for StubLearningAdapter and InMemoryLearningEventRepository.

Covers: save() stores event, find_by_id() returns stored event,
find_by_id() returns None for unknown id, find_pending() returns
events with PENDING status, find_by_decision_id() returns indexed events.
"""

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from cognitiveplane.shared.dto.learning import LearningContent, LearningEvent
from cognitiveplane.shared.enums import LearningEventType, ProcessingStatus
from cognitiveplane.shared.types import DecisionId, LearningEventId
from cognitiveplane.memory.learning.adapters.stub import StubLearningAdapter
from cognitiveplane.memory.learning.repositories.in_memory import InMemoryLearningEventRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_learning_event_id() -> LearningEventId:
    return LearningEventId(value=uuid4())


def _make_decision_id() -> DecisionId:
    return DecisionId(value=uuid4())


def _make_learning_event(
    learning_event_id: LearningEventId | None = None,
    source_decision_id: DecisionId | None = None,
    processing_status: ProcessingStatus = ProcessingStatus.PENDING,
) -> LearningEvent:
    decision_id = source_decision_id or _make_decision_id()
    return LearningEvent(
        learning_event_id=learning_event_id or _make_learning_event_id(),
        event_type=LearningEventType.EXPERIENCE,
        source_decision_id=decision_id,
        content=LearningContent(
            event_type=LearningEventType.EXPERIENCE,
            source_decision_id=decision_id,
            data={"key": "value"},
        ),
        processing_status=processing_status,
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> StubLearningAdapter:
    return StubLearningAdapter()


@pytest.fixture
def repo() -> InMemoryLearningEventRepository:
    return InMemoryLearningEventRepository()


# ===================================================================
# Test cases — InMemoryLearningEventRepository
# ===================================================================


class TestSave:
    """save() stores event."""

    @pytest.mark.asyncio
    async def test_save_stores_event(
        self, repo: InMemoryLearningEventRepository
    ):
        event = _make_learning_event()
        await repo.save(event)
        found = await repo.find_by_id(event.learning_event_id)
        assert found is not None
        assert found.learning_event_id.value == event.learning_event_id.value


class TestFindById:
    """find_by_id() returns stored event or None."""

    @pytest.mark.asyncio
    async def test_find_by_id_returns_stored_event(
        self, repo: InMemoryLearningEventRepository
    ):
        event = _make_learning_event()
        await repo.save(event)
        found = await repo.find_by_id(event.learning_event_id)
        assert found is not None
        assert found.learning_event_id.value == event.learning_event_id.value

    @pytest.mark.asyncio
    async def test_find_by_id_returns_none_for_unknown(
        self, repo: InMemoryLearningEventRepository
    ):
        unknown_id = _make_learning_event_id()
        found = await repo.find_by_id(unknown_id)
        assert found is None


class TestFindPending:
    """find_pending() returns events with PENDING status."""

    @pytest.mark.asyncio
    async def test_find_pending_returns_pending_events(
        self, repo: InMemoryLearningEventRepository
    ):
        pending = _make_learning_event(processing_status=ProcessingStatus.PENDING)
        completed = _make_learning_event(
            processing_status=ProcessingStatus.COMPLETED
        )
        await repo.save(pending)
        await repo.save(completed)

        results = await repo.find_pending()
        result_ids = {e.learning_event_id.value for e in results}
        assert pending.learning_event_id.value in result_ids
        assert completed.learning_event_id.value not in result_ids


class TestFindByDecisionId:
    """find_by_decision_id() returns indexed events."""

    @pytest.mark.asyncio
    async def test_find_by_decision_id_returns_indexed(
        self, repo: InMemoryLearningEventRepository
    ):
        decision_id = _make_decision_id()
        e1 = _make_learning_event(source_decision_id=decision_id)
        e2 = _make_learning_event(source_decision_id=decision_id)
        other = _make_learning_event()  # different decision
        await repo.save(e1)
        await repo.save(e2)
        await repo.save(other)

        found = await repo.find_by_decision_id(decision_id)
        found_ids = {e.learning_event_id.value for e in found}
        assert e1.learning_event_id.value in found_ids
        assert e2.learning_event_id.value in found_ids
        assert other.learning_event_id.value not in found_ids


# ===================================================================
# Test cases — StubLearningAdapter
# ===================================================================


class TestStubAdapterReturnsEmpty:
    """StubLearningAdapter returns empty lists by default."""

    @pytest.mark.asyncio
    async def test_query_learning_events_returns_empty(
        self, adapter: StubLearningAdapter
    ):
        result = await adapter.query_learning_events()
        assert result == []

    @pytest.mark.asyncio
    async def test_query_pending_learning_returns_empty(
        self, adapter: StubLearningAdapter
    ):
        result = await adapter.query_pending_learning()
        assert result == []
