"""L1 Cognitive Plane — Human Collaboration port ABCs and Repository.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 6.5).
"""

from abc import ABC, abstractmethod

from cognitiveplane.shared.dto_collaboration import HumanReviewRequest
from cognitiveplane.shared.enums import CollaborationLayer
from cognitiveplane.shared.types import DecisionId, ReviewRequestId


class HumanReviewRequestRepository(ABC):
    """Repository for persisting and querying HumanReviewRequest records."""

    @abstractmethod
    async def save(self, request: HumanReviewRequest) -> None: ...

    @abstractmethod
    async def find_by_id(
        self, request_id: ReviewRequestId
    ) -> HumanReviewRequest | None: ...

    @abstractmethod
    async def find_pending_by_layer(
        self, layer: CollaborationLayer
    ) -> list[HumanReviewRequest]: ...

    @abstractmethod
    async def find_by_decision_id(
        self, decision_id: DecisionId
    ) -> list[HumanReviewRequest]: ...
