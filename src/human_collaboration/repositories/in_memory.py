"""In-memory implementation of HumanReviewRequestRepository for testing and development.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 6.5).
"""

from src.shared.dto_collaboration import HumanReviewRequest
from src.shared.enums import CollaborationLayer, ReviewStatus
from src.shared.ports.collaboration import HumanReviewRequestRepository
from src.shared.types import DecisionId, ReviewRequestId


class InMemoryHumanReviewRequestRepository(HumanReviewRequestRepository):
    """In-memory repository backed by plain dicts.

    Suitable for unit tests and local development.  Not thread-safe.
    """

    def __init__(self) -> None:
        self._store: dict[str, HumanReviewRequest] = {}
        self._decision_index: dict[str, list[str]] = {}
        self._layer_index: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # Repository interface
    # ------------------------------------------------------------------

    async def save(self, request: HumanReviewRequest) -> None:
        key = str(request.request_id.value)
        self._store[key] = request

        # Maintain the decision-id reverse index
        dec_key = str(request.decision_id.value)
        self._decision_index.setdefault(dec_key, []).append(key)

        # Maintain the collaboration-layer index
        layer_key = request.collaboration_layer.value
        self._layer_index.setdefault(layer_key, []).append(key)

    async def find_by_id(
        self, request_id: ReviewRequestId
    ) -> HumanReviewRequest | None:
        key = str(request_id.value)
        return self._store.get(key)

    async def find_pending_by_layer(
        self, layer: CollaborationLayer
    ) -> list[HumanReviewRequest]:
        layer_key = layer.value
        keys = self._layer_index.get(layer_key, [])
        results: list[HumanReviewRequest] = []
        for k in keys:
            req = self._store.get(k)
            if req is not None and req.status == ReviewStatus.PENDING:
                results.append(req)
        return results

    async def find_by_decision_id(
        self, decision_id: DecisionId
    ) -> list[HumanReviewRequest]:
        dec_key = str(decision_id.value)
        keys = self._decision_index.get(dec_key, [])
        return [self._store[k] for k in keys if k in self._store]

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Reset all internal storage. Required for test isolation."""
        self._store.clear()
        self._decision_index.clear()
        self._layer_index.clear()
