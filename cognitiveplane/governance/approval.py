"""ApprovalService — sync/async approval routing (spec §9 lines 1669-1676).

Routes approval requests by urgency:
- CRITICAL / URGENT → sync approval (caller blocks until human responds, with
  poll-based wait — designed to be implemented over WebSocket / NATS in Phase 2)
- ROUTINE → async approval (immediately schedules a HumanReview, returns
  the ReviewRequestId; caller proceeds; resolution arrives via callback or
  later poll)

The service is a thin coordinator over `HumanReviewWorkflow`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from cognitiveplane.governance.review import HumanReviewWorkflow
from cognitiveplane.shared.dto_collaboration import (
    HumanReviewRequest,
    ResolutionContent,
    ReviewContent,
)
from cognitiveplane.shared.enums import (
    CollaborationLayer,
    ReviewStatus,
    ReviewType,
    UrgencyLevel,
)
from cognitiveplane.shared.types import DecisionId, ReviewRequestId


@dataclass
class ApprovalRequest:
    decision_id: DecisionId
    urgency: UrgencyLevel
    layer: CollaborationLayer
    review_type: ReviewType
    content: ReviewContent
    timeout_seconds: float = 30.0
    poll_interval_seconds: float = 0.2


@dataclass
class ApprovalResult:
    request_id: ReviewRequestId
    status: ReviewStatus  # PENDING (async) / APPROVED / DENIED / EXPIRED
    resolution: ResolutionContent | None = None
    is_sync: bool = False


class ApprovalService:
    """Decide between sync and async approval, then dispatch.

    Sync path is implemented as a polling wait against the workflow store —
    which is enough for in-process tests and for a Phase 2 transport
    (WebSocket / NATS) to publish status updates onto the same store.
    """

    SYNC_URGENCIES = {UrgencyLevel.CRITICAL, UrgencyLevel.URGENT}

    def __init__(self, workflow: HumanReviewWorkflow) -> None:
        self._workflow = workflow

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResult:
        if req.urgency in self.SYNC_URGENCIES:
            return await self._sync_approval(req)
        return await self._async_approval(req)

    # internal ----------------------------------------------------------

    async def _async_approval(self, req: ApprovalRequest) -> ApprovalResult:
        review = await self._workflow.create_review(
            decision_id=req.decision_id,
            layer=req.layer,
            review_type=req.review_type,
            content=req.content,
        )
        return ApprovalResult(
            request_id=review.request_id,
            status=ReviewStatus.PENDING,
            is_sync=False,
        )

    async def _sync_approval(self, req: ApprovalRequest) -> ApprovalResult:
        review = await self._workflow.create_review(
            decision_id=req.decision_id,
            layer=req.layer,
            review_type=req.review_type,
            content=req.content,
        )
        deadline = (
            datetime.now(timezone.utc).timestamp() + req.timeout_seconds
        )
        while datetime.now(timezone.utc).timestamp() < deadline:
            current: HumanReviewRequest = await self._workflow.get(
                review.request_id
            )
            if current.status in (
                ReviewStatus.APPROVED,
                ReviewStatus.DENIED,
            ):
                return ApprovalResult(
                    request_id=current.request_id,
                    status=current.status,
                    resolution=current.resolution,
                    is_sync=True,
                )
            await asyncio.sleep(req.poll_interval_seconds)

        # Timeout — mark expired in the store so callers can see it
        expired = (await self._workflow.get(review.request_id)).model_copy(
            update={
                "status": ReviewStatus.EXPIRED,
                "resolved_at": datetime.now(timezone.utc),
            }
        )
        # Save through the underlying repo to keep state consistent
        await self._workflow._repo.save(expired)  # noqa: SLF001
        return ApprovalResult(
            request_id=expired.request_id,
            status=ReviewStatus.EXPIRED,
            is_sync=True,
        )
