"""L1 Cognitive Plane — Validation port ABCs, Input/Output models, and Repository.

Source: L1_Port_and_Contract_Design.md (Phase 4, Sections 6.2, 9.advice.md–9.6).

CRITICAL: ConsistencyValidatorPort.check() uses ``prior_memories`` (list[MemorySearchResult])
instead of ``prior_decisions`` (list[BrainDecision]) to respect the DDD memory wall.
The Validation bounded context must not directly access BrainDecision aggregates;
it retrieves historical context through the Memory layer instead.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import BaseModel, Field

from cognitiveplane.shared.dto_context import ContextSnapshot, WeldMapSnapshot
from cognitiveplane.shared.dto_decision import BrainDecision
from cognitiveplane.shared.dto_memory import MemorySearchResult
from cognitiveplane.shared.dto_validation import (
    DivergencePoint,
    InconsistencyDetail,
    ShadowDecision,
    ValidationResult,
)
from cognitiveplane.shared.enums import (
    ConsistencyStatus,
    FallbackMode,
    ReasoningMode,
    RuleStatus,
    SafetyStatus,
    ShadowStatus,
)
from cognitiveplane.shared.types import CaseId, DecisionId, ValidationId


# ---------------------------------------------------------------------------
# 6.2 ValidationResultRepository
# ---------------------------------------------------------------------------


class ValidationResultRepository(ABC):
    """Repository for persisting and querying ValidationResult records."""

    @abstractmethod
    async def save(self, result: ValidationResult) -> None: ...

    @abstractmethod
    async def find_by_decision_id(
        self, decision_id: DecisionId
    ) -> list[ValidationResult]: ...

    @abstractmethod
    async def find_recent_criticals(
        self, since: datetime, max_results: int = 10
    ) -> list[ValidationResult]: ...


# ---------------------------------------------------------------------------
# 9.advice.md ValidationPipelinePort
# ---------------------------------------------------------------------------


class ValidationPipelineInput(BaseModel):
    context: ContextSnapshot
    decision: BrainDecision
    reasoning_mode: ReasoningMode


class ValidationPipelineOutput(BaseModel):
    validation_result: ValidationResult
    escalation_counter_updated: bool
    new_fallback_mode: FallbackMode | None = None


class ValidationPipelinePort(ABC):

    @abstractmethod
    async def validate(
        self, input_data: ValidationPipelineInput
    ) -> ValidationPipelineOutput: ...


# ---------------------------------------------------------------------------
# 9.2 SafetyValidatorPort
# ---------------------------------------------------------------------------


class SafetyValidatorInput(BaseModel):
    context: ContextSnapshot
    decision: BrainDecision


class SafetyValidatorOutput(BaseModel):
    result: SafetyStatus
    checked_rules: list[str]
    timestamp: datetime


class SafetyValidatorPort(ABC):

    @abstractmethod
    async def check(self, input_data: SafetyValidatorInput) -> SafetyValidatorOutput: ...


# ---------------------------------------------------------------------------
# 9.3 RuleValidatorPort
# ---------------------------------------------------------------------------


class RuleValidatorInput(BaseModel):
    context: ContextSnapshot
    decision: BrainDecision


class RuleValidatorOutput(BaseModel):
    result: RuleStatus
    violated_rules: list[str]
    timestamp: datetime


class RuleValidatorPort(ABC):

    @abstractmethod
    async def check(self, input_data: RuleValidatorInput) -> RuleValidatorOutput: ...


# ---------------------------------------------------------------------------
# 9.4 ShadowValidatorPort
# ---------------------------------------------------------------------------


class ShadowValidatorInput(BaseModel):
    context: ContextSnapshot
    decision: BrainDecision


class ShadowValidatorOutput(BaseModel):
    result: ShadowStatus
    shadow_decision: ShadowDecision
    alignment_score: float = Field(ge=0.0, le=1.0)
    divergence_points: list[DivergencePoint]
    timestamp: datetime


class ShadowValidatorPort(ABC):

    @abstractmethod
    async def check(self, input_data: ShadowValidatorInput) -> ShadowValidatorOutput: ...


# ---------------------------------------------------------------------------
# 9.5 ConsistencyValidatorPort
# ---------------------------------------------------------------------------


class ConsistencyValidatorInput(BaseModel):
    context: ContextSnapshot
    decision: BrainDecision
    prior_memories: list[MemorySearchResult]  # DDD memory wall — not prior_decisions
    weldmap_state: WeldMapSnapshot


class ConsistencyValidatorOutput(BaseModel):
    result: ConsistencyStatus
    factual_consistency: ConsistencyStatus
    historical_consistency: ConsistencyStatus
    inconsistencies: list[InconsistencyDetail]
    timestamp: datetime


class ConsistencyValidatorPort(ABC):

    @abstractmethod
    async def check(
        self, input_data: ConsistencyValidatorInput
    ) -> ConsistencyValidatorOutput: ...


# ---------------------------------------------------------------------------
# 9.6 EscalationTrackerPort
# ---------------------------------------------------------------------------


class EscalationState(BaseModel):
    consecutive_critical_count: int = Field(ge=0)
    current_fallback_mode: FallbackMode


class EscalationTrackerInput(BaseModel):
    shadow_result: ShadowStatus | None = None
    case_id: CaseId | None = None


class EscalationTrackerOutput(BaseModel):
    previous_state: EscalationState
    new_state: EscalationState
    mode_changed: bool


class EscalationTrackerPort(ABC):

    @abstractmethod
    async def update(
        self, input_data: EscalationTrackerInput
    ) -> EscalationTrackerOutput: ...

    @abstractmethod
    async def get_state(self) -> EscalationState: ...

    @abstractmethod
    async def reset(self, reason: str) -> EscalationState: ...
