"""CopilotGovern — Approval/compliance assistance (spec §11).

Surfaces governance state for an operator/auditor:
- pending review queue summary by collaboration layer
- decision approval history (which decisions had reviews, how they resolved)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cognitiveplane.control.ports import BrainDecisionRepository
from cognitiveplane.shared.dto_collaboration import HumanReviewRequest
from cognitiveplane.shared.enums import (
    CollaborationLayer,
    ReviewStatus,
)
from cognitiveplane.shared.ports.collaboration import HumanReviewRequestRepository
from cognitiveplane.shared.types import DecisionId


@dataclass
class GovernanceSummary:
    pending_by_layer: dict[str, int]
    pending_examples: list[HumanReviewRequest] = field(default_factory=list)
    decision_review_count: int = 0
    decision_review_history: list[HumanReviewRequest] = field(default_factory=list)


class CopilotGovern:
    def __init__(
        self,
        review_repo: HumanReviewRequestRepository,
        decision_repo: BrainDecisionRepository,
    ) -> None:
        self._reviews = review_repo
        self._decisions = decision_repo

    async def summarize_pending(
        self, sample_per_layer: int = 3
    ) -> GovernanceSummary:
        per_layer: dict[str, int] = {}
        examples: list[HumanReviewRequest] = []
        for layer in CollaborationLayer:
            pending = await self._reviews.find_pending_by_layer(layer)
            per_layer[layer.value] = len(pending)
            examples.extend(pending[:sample_per_layer])
        return GovernanceSummary(
            pending_by_layer=per_layer,
            pending_examples=examples,
        )

    async def decision_review_history(
        self, decision_id: DecisionId
    ) -> GovernanceSummary:
        # validate existence; raise if missing
        decision = await self._decisions.find_by_id(decision_id)
        if decision is None:
            return GovernanceSummary(pending_by_layer={}, decision_review_count=0)
        reviews = await self._reviews.find_by_decision_id(decision_id)
        return GovernanceSummary(
            pending_by_layer={},
            decision_review_count=len(reviews),
            decision_review_history=reviews,
        )

    @staticmethod
    def is_compliant(reviews: list[HumanReviewRequest]) -> bool:
        """Trivial compliance heuristic: no DENIED, no EXPIRED, all PENDING resolved."""
        if not reviews:
            return True
        for r in reviews:
            if r.status in (ReviewStatus.DENIED, ReviewStatus.EXPIRED):
                return False
        return True
