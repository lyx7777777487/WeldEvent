"""HumanReview Postgres repo — Phase 1 stub satisfying HumanReviewRequestRepository."""

from __future__ import annotations

from cognitiveplane.adapters.database.engine import AsyncDatabaseEngine
from cognitiveplane.shared.dto_collaboration import HumanReviewRequest
from cognitiveplane.shared.enums import CollaborationLayer, ReviewStatus
from cognitiveplane.shared.ports.collaboration import HumanReviewRequestRepository
from cognitiveplane.shared.types import DecisionId, ReviewRequestId


class PostgresHumanReviewRequestRepository(HumanReviewRequestRepository):
    def __init__(self, engine: AsyncDatabaseEngine) -> None:
        self._engine = engine
        self._store: dict[str, HumanReviewRequest] = {}

    async def save(self, request: HumanReviewRequest) -> None:
        async with self._engine.session():
            self._store[str(request.request_id.value)] = request

    async def find_by_id(
        self, request_id: ReviewRequestId
    ) -> HumanReviewRequest | None:
        return self._store.get(str(request_id.value))

    async def find_pending_by_layer(
        self, layer: CollaborationLayer
    ) -> list[HumanReviewRequest]:
        return [
            r
            for r in self._store.values()
            if r.collaboration_layer == layer and r.status == ReviewStatus.PENDING
        ]

    async def find_by_decision_id(
        self, decision_id: DecisionId
    ) -> list[HumanReviewRequest]:
        return [r for r in self._store.values() if r.decision_id == decision_id]
