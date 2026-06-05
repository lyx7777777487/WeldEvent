"""L1 Cognitive Plane — Learning port ABCs and Repository.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 6.6).
"""

from abc import ABC, abstractmethod

from src.shared.dto_learning import LearningEvent
from src.shared.types import DecisionId, LearningEventId


class LearningEventRepository(ABC):
    """Repository for persisting and querying LearningEvent records."""

    @abstractmethod
    async def save(self, event: LearningEvent) -> None: ...

    @abstractmethod
    async def find_by_id(
        self, learning_event_id: LearningEventId
    ) -> LearningEvent | None: ...

    @abstractmethod
    async def find_pending(self) -> list[LearningEvent]: ...

    @abstractmethod
    async def find_by_decision_id(
        self, decision_id: DecisionId
    ) -> list[LearningEvent]: ...
