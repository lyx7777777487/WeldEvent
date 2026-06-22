"""Stub human collaboration adapter that returns seeded or empty responses.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 6.5).
Implements HumanReviewRequestRepository ABC so it can be wired through the
port system without AttributeError.
"""

from cognitiveplane.shared.dto_collaboration import HumanReviewRequest
from cognitiveplane.shared.enums import CollaborationLayer, ReviewStatus
from cognitiveplane.shared.ports.collaboration import HumanReviewRequestRepository
from cognitiveplane.shared.types import DecisionId, ReviewRequestId


class StubCollaborationAdapter(HumanReviewRequestRepository):
    """Stub adapter implementing HumanReviewRequestRepository.

    Stores review requests in an in-memory dict.  Accepts optional
    ``seed_responses`` dict for Phase 5B harness testability.
    """

    def __init__(
        self, seed_responses: dict[str, list] | None = None
    ) -> None:
        self._seed = seed_responses or {}
        self._store: dict[ReviewRequestId, HumanReviewRequest] = {}

    def clear(self) -> None:
        """Reset seed responses and store. Required for test isolation."""
        self._seed = {}
        self._store.clear()

    # ------------------------------------------------------------------
    # HumanReviewRequestRepository ABC
    # ------------------------------------------------------------------

    async def save(self, request: HumanReviewRequest) -> None:
        self._store[request.request_id] = request

    async def find_by_id(
        self, request_id: ReviewRequestId
    ) -> HumanReviewRequest | None:
        return self._store.get(request_id)

    async def find_pending_by_layer(
        self, layer: CollaborationLayer
    ) -> list[HumanReviewRequest]:
        return [
            r for r in self._store.values()
            if r.collaboration_layer == layer and r.status == ReviewStatus.PENDING
        ]

    async def find_by_decision_id(
        self, decision_id: DecisionId
    ) -> list[HumanReviewRequest]:
        return [
            r for r in self._store.values()
            if r.decision_id == decision_id
        ]

    # ------------------------------------------------------------------
    # Legacy seed-based query methods (for backward compat with tests)
    # ------------------------------------------------------------------

    async def query_pending_reviews(self) -> list:
        return self._seed.get("query_pending_reviews", [])

    async def query_feedback(self) -> list:
        return self._seed.get("query_feedback", [])

    async def query_conversations(self) -> list:
        return self._seed.get("query_conversations", [])