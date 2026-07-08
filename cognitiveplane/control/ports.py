"""L1 Cognitive Plane — Brain-specific ports.

Brain-internal inbound interfaces and repository contracts.
Spec §4 lines 549-553: ReasoningPort, PlanningPort, ReflectionPort,
SubAgentDelegationPort live in the Control plane (replacing the legacy
`shared/ports/deepagents.py` umbrella, which remains as a deprecated
re-export until all legacy callers migrate).
"""

from abc import ABC, abstractmethod
from typing import Any

from cognitiveplane.shared.enums import BrainStateType, EventType
from cognitiveplane.shared.events import DomainEvent
from cognitiveplane.shared.dto_decision import BrainDecision
from cognitiveplane.shared.types import CaseId, DecisionId


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


# ---------------------------------------------------------------------------
# Reasoning / Planning / Reflection / Sub-Agent ports (spec §4)
#
# These are the canonical Phase 1 contracts. They take and return DTOs
# directly per spec §4 rule 2 (no `*Input`/`*Output` wrapper classes). The
# previously published wrapper-based ABCs in `shared/ports/deepagents.py`
# are kept for backward compatibility while legacy modes are still wired,
# and will be deleted alongside the Mode classes in the follow-on cleanup.
# ---------------------------------------------------------------------------


class ReasoningPort(ABC):
    """Brain-internal reasoning capability — multi-step inference over context.

    Implementations call an LLM (or ROUTINE-tier rule engine) and return a
    structured `Conclusion` DTO. Callers MUST pre-fetch knowledge / memory
    context and pass it via `context` rather than expecting the port to do
    side-effectful retrieval.
    """

    @abstractmethod
    async def reason(
        self,
        case_id: CaseId,
        question: str,
        context: dict[str, Any],
    ) -> Any:  # returns shared.dto_deepagents.Conclusion (kept loose to avoid cycles)
        ...


class PlanningPort(ABC):
    """Brain-internal planning capability — task decomposition into a plan tree."""

    @abstractmethod
    async def plan(
        self,
        case_id: CaseId,
        goal: str,
        constraints: dict[str, Any],
    ) -> Any:  # returns shared.dto_deepagents.Plan
        ...


class ReflectionPort(ABC):
    """Brain-internal reflection capability — gap analysis over a draft answer."""

    @abstractmethod
    async def reflect(
        self,
        case_id: CaseId,
        draft: Any,
        evidence: dict[str, Any],
    ) -> Any:  # returns shared.dto_deepagents.Gap | Suggestion bundle
        ...


class SubAgentDelegationPort(ABC):
    """Brain-internal sub-agent delegation — spawn a focused sub-task agent.

    Implementations may bind to specialised sub-agent runtimes; currently
    pending — see AGENT_COMPARISON_REPORT P0 roadmap.
    """

    @abstractmethod
    async def delegate(
        self,
        case_id: CaseId,
        sub_task: str,
        context: dict[str, Any],
    ) -> Any:  # returns sub-agent result payload
        ...

