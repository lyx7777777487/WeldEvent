"""HumanReview workflow (spec §9 lines 1665-1700).

Lightweight orchestration over `HumanReviewRequestRepository`:
- create_review: build + persist a review request
- mark_in_progress / approve / deny: state transitions writing back
- find_pending: query helper

This is the deterministic baseline used by ApprovalService and Copilot.govern.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from cognitiveplane.shared.dto_collaboration import (
    HumanReviewRequest,
    ResolutionContent,
    ReviewContent,
)
from cognitiveplane.shared.enums import (
    CollaborationLayer,
    ReviewStatus,
    ReviewType,
)
from cognitiveplane.shared.ports.collaboration import HumanReviewRequestRepository
from cognitiveplane.shared.types import DecisionId, ReviewRequestId


class ReviewNotFoundError(Exception):
    def __init__(self, request_id: ReviewRequestId) -> None:
        super().__init__(f"HumanReviewRequest not found: {request_id.value}")


class HumanReviewWorkflow:
    """Coordinator on top of HumanReviewRequestRepository."""

    def __init__(self, repo: HumanReviewRequestRepository) -> None:
        self._repo = repo

    async def create_review(
        self,
        decision_id: DecisionId,
        layer: CollaborationLayer,
        review_type: ReviewType,
        content: ReviewContent,
    ) -> HumanReviewRequest:
        req = HumanReviewRequest(
            request_id=ReviewRequestId(value=uuid4()),
            decision_id=decision_id,
            collaboration_layer=layer,
            review_type=review_type,
            status=ReviewStatus.PENDING,
            content=content,
            created_at=datetime.now(timezone.utc),
        )
        await self._repo.save(req)
        return req

    async def mark_in_progress(
        self, request_id: ReviewRequestId, reviewer: str
    ) -> HumanReviewRequest:
        req = await self._require(request_id)
        updated = req.model_copy(
            update={"status": ReviewStatus.IN_PROGRESS, "reviewer": reviewer}
        )
        await self._repo.save(updated)
        return updated

    async def approve(
        self,
        request_id: ReviewRequestId,
        reviewer: str,
        resolution: ResolutionContent,
    ) -> HumanReviewRequest:
        return await self._resolve(
            request_id, reviewer, resolution, ReviewStatus.APPROVED
        )

    async def deny(
        self,
        request_id: ReviewRequestId,
        reviewer: str,
        resolution: ResolutionContent,
    ) -> HumanReviewRequest:
        return await self._resolve(
            request_id, reviewer, resolution, ReviewStatus.DENIED
        )

    async def find_pending(
        self, layer: CollaborationLayer
    ) -> list[HumanReviewRequest]:
        return await self._repo.find_pending_by_layer(layer)

    async def get(self, request_id: ReviewRequestId) -> HumanReviewRequest:
        return await self._require(request_id)

    # internal helpers ---------------------------------------------------

    async def _require(self, request_id: ReviewRequestId) -> HumanReviewRequest:
        req = await self._repo.find_by_id(request_id)
        if req is None:
            raise ReviewNotFoundError(request_id)
        return req

    async def _resolve(
        self,
        request_id: ReviewRequestId,
        reviewer: str,
        resolution: ResolutionContent,
        status: ReviewStatus,
    ) -> HumanReviewRequest:
        req = await self._require(request_id)
        updated = req.model_copy(
            update={
                "status": status,
                "reviewer": reviewer,
                "resolution": resolution,
                "resolved_at": datetime.now(timezone.utc),
            }
        )
        await self._repo.save(updated)
        return updated
