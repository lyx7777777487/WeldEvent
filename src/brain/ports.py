"""L1 Cognitive Plane — Brain-specific ports.

Brain-internal inbound interfaces and repository contract.
Source: L1_Port_and_Contract_Design.md (Phase 4, Sections 1.1, 6.1).
"""

from abc import ABC, abstractmethod

from src.shared.enums import BrainStateType, EventType
from src.shared.events import DomainEvent
from src.shared.dto_decision import BrainDecision
from src.shared.types import CaseId, DecisionId


class BrainDecisionRepository(ABC):
    """Repository for persisting and querying BrainDecision aggregates."""

    @abstractmethod
    async def save(self, decision: BrainDecision) -> None: ...

    @abstractmethod
    async def find_by_id(self, decision_id: DecisionId) -> BrainDecision | None: ...

    @abstractmethod
    async def find_by_trigger_event(
        self, event_type: EventType, case_id: CaseId
    ) -> list[BrainDecision]: ...

    @abstractmethod
    async def find_pending_decisions(
        self, max_results: int = 10
    ) -> list[BrainDecision]: ...


class BrainEventConsumerPort(ABC):
    """Inbound port — receives domain events from the Cognitive Gateway."""

    @abstractmethod
    async def consume(self, event: DomainEvent) -> None: ...


class BrainHealthPort(ABC):
    """Inbound port — reports Brain health / current state."""

    @abstractmethod
    async def get_state(self) -> BrainStateType: ...
