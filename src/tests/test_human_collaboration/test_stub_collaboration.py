"""Tests for StubCollaborationAdapter and InMemoryHumanReviewRequestRepository.

Covers: save() stores request, find_by_id() returns stored request,
find_by_id() returns None for unknown id, find_pending_by_layer()
returns requests matching layer and PENDING status,
find_by_decision_id() returns indexed requests.
"""

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from src.shared.dto_collaboration import (
    HumanReviewRequest,
    ReviewContent,
)
from src.shared.enums import CollaborationLayer, ReviewStatus, ReviewType
from src.shared.types import DecisionId, ReviewRequestId
from src.human_collaboration.adapters.stub import StubCollaborationAdapter
from src.human_collaboration.repositories.in_memory import (
    InMemoryHumanReviewRequestRepository,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_review_request_id() -> ReviewRequestId:
    return ReviewRequestId(value=uuid4())


def _make_decision_id() -> DecisionId:
    return DecisionId(value=uuid4())


def _make_review_request(
    request_id: ReviewRequestId | None = None,
    decision_id: DecisionId | None = None,
    collaboration_layer: CollaborationLayer = CollaborationLayer.L1,
    status: ReviewStatus = ReviewStatus.PENDING,
    review_type: ReviewType = ReviewType.APPROVAL,
) -> HumanReviewRequest:
    return HumanReviewRequest(
        request_id=request_id or _make_review_request_id(),
        decision_id=decision_id or _make_decision_id(),
        collaboration_layer=collaboration_layer,
        review_type=review_type,
        status=status,
        content=ReviewContent(
            decision_id=decision_id or _make_decision_id(),
            summary="Test review summary",
            reasoning_summary="Test reasoning",
            risk_summary="Low risk",
            recommendation="Approve",
            confidence=0.9,
        ),
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> StubCollaborationAdapter:
    return StubCollaborationAdapter()


@pytest.fixture
def repo() -> InMemoryHumanReviewRequestRepository:
    return InMemoryHumanReviewRequestRepository()


# ===================================================================
# Test cases — InMemoryHumanReviewRequestRepository
# ===================================================================


class TestSave:
    """save() stores request."""

    @pytest.mark.asyncio
    async def test_save_stores_request(
        self, repo: InMemoryHumanReviewRequestRepository
    ):
        request = _make_review_request()
        await repo.save(request)
        found = await repo.find_by_id(request.request_id)
        assert found is not None
        assert found.request_id.value == request.request_id.value


class TestFindById:
    """find_by_id() returns stored request."""

    @pytest.mark.asyncio
    async def test_find_by_id_returns_stored_request(
        self, repo: InMemoryHumanReviewRequestRepository
    ):
        request = _make_review_request()
        await repo.save(request)
        found = await repo.find_by_id(request.request_id)
        assert found is not None
        assert found.request_id.value == request.request_id.value

    @pytest.mark.asyncio
    async def test_find_by_id_returns_none_for_unknown(
        self, repo: InMemoryHumanReviewRequestRepository
    ):
        unknown_id = _make_review_request_id()
        found = await repo.find_by_id(unknown_id)
        assert found is None


class TestFindPendingByLayer:
    """find_pending_by_layer() returns requests matching layer and PENDING status."""

    @pytest.mark.asyncio
    async def test_find_pending_by_layer_returns_matching(
        self, repo: InMemoryHumanReviewRequestRepository
    ):
        pending = _make_review_request(
            collaboration_layer=CollaborationLayer.L1,
            status=ReviewStatus.PENDING,
        )
        approved = _make_review_request(
            collaboration_layer=CollaborationLayer.L1,
            status=ReviewStatus.APPROVED,
        )
        other_layer = _make_review_request(
            collaboration_layer=CollaborationLayer.L2,
            status=ReviewStatus.PENDING,
        )
        await repo.save(pending)
        await repo.save(approved)
        await repo.save(other_layer)

        results = await repo.find_pending_by_layer(CollaborationLayer.L1)
        result_ids = {r.request_id.value for r in results}
        assert pending.request_id.value in result_ids
        assert approved.request_id.value not in result_ids
        assert other_layer.request_id.value not in result_ids


class TestFindByDecisionId:
    """find_by_decision_id() returns indexed requests."""

    @pytest.mark.asyncio
    async def test_find_by_decision_id_returns_indexed(
        self, repo: InMemoryHumanReviewRequestRepository
    ):
        decision_id = _make_decision_id()
        r1 = _make_review_request(decision_id=decision_id)
        r2 = _make_review_request(decision_id=decision_id)
        other = _make_review_request()  # different decision
        await repo.save(r1)
        await repo.save(r2)
        await repo.save(other)

        found = await repo.find_by_decision_id(decision_id)
        found_ids = {r.request_id.value for r in found}
        assert r1.request_id.value in found_ids
        assert r2.request_id.value in found_ids
        assert other.request_id.value not in found_ids


# ===================================================================
# Test cases — StubCollaborationAdapter
# ===================================================================


class TestStubAdapterReturnsEmpty:
    """StubCollaborationAdapter returns empty lists by default."""

    @pytest.mark.asyncio
    async def test_query_pending_reviews_returns_empty(
        self, adapter: StubCollaborationAdapter
    ):
        result = await adapter.query_pending_reviews()
        assert result == []

    @pytest.mark.asyncio
    async def test_query_feedback_returns_empty(
        self, adapter: StubCollaborationAdapter
    ):
        result = await adapter.query_feedback()
        assert result == []
