"""L1 Cognitive Plane — DeepAgents port ABCs and Input/Output models.

Source: L1_Port_and_Contract_Design.md (Phase 4, Sections 7.1–7.5).
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from src.shared.dto_context import ContextSnapshot
from src.shared.dto_decision import BrainDecision
from src.shared.dto_deepagents import (
    AdaptationSuggestion,
    ApplicabilityScore,
    Conclusion,
    Experience,
    Factor,
    Gap,
    Plan,
    SituationDescription,
    Strategy,
    Suggestion,
)
from src.shared.dto_knowledge import KnowledgeResult
from src.shared.dto_memory import MemorySearchResult
from src.shared.dto_validation import ValidationResult
from src.shared.dto_collaboration import FeedbackContent
from src.shared.dto_decision.outputs import Constraint
from src.shared.enums import AudienceType


# ---------------------------------------------------------------------------
# 7.1 DeepAgentsReasoningPort
# ---------------------------------------------------------------------------


class ReasoningInput(BaseModel):
    context: ContextSnapshot
    question: str = Field(min_length=1)
    knowledge_results: list[KnowledgeResult]
    memory_results: list[MemorySearchResult]


class ReasoningOutput(BaseModel):
    reasoning_trace: str = Field(min_length=1)
    conclusions: list[Conclusion]
    confidence: float = Field(ge=0.0, le=1.0)


class DeepAgentsReasoningPort(ABC):

    @abstractmethod
    async def reason(self, input_data: ReasoningInput) -> ReasoningOutput: ...


# ---------------------------------------------------------------------------
# 7.2 DeepAgentsPlanningPort
# ---------------------------------------------------------------------------


class PlanningInput(BaseModel):
    context: ContextSnapshot
    objective: str = Field(min_length=1)
    constraints: list[Constraint]
    available_strategies: list[Strategy]


class PlanningOutput(BaseModel):
    plan: Plan
    alternative_plans: list[Plan]
    confidence: float = Field(ge=0.0, le=1.0)


class DeepAgentsPlanningPort(ABC):

    @abstractmethod
    async def plan(self, input_data: PlanningInput) -> PlanningOutput: ...


# ---------------------------------------------------------------------------
# 7.3 DeepAgentsReflectionPort
# ---------------------------------------------------------------------------


class ReflectionInput(BaseModel):
    context: ContextSnapshot
    decision: BrainDecision
    validation_result: ValidationResult
    feedback: FeedbackContent | None = None


class ReflectionOutput(BaseModel):
    reflection: str = Field(min_length=1)
    identified_gaps: list[Gap]
    improvement_suggestions: list[Suggestion]


class DeepAgentsReflectionPort(ABC):

    @abstractmethod
    async def reflect(self, input_data: ReflectionInput) -> ReflectionOutput: ...


# ---------------------------------------------------------------------------
# 7.4 DeepAgentsExplanationPort
# ---------------------------------------------------------------------------


class ExplanationInput(BaseModel):
    context: ContextSnapshot
    decision: BrainDecision
    audience: AudienceType


class ExplanationOutput(BaseModel):
    explanation: str = Field(min_length=1)
    key_factors: list[Factor]
    confidence_justification: str = Field(min_length=1)


class DeepAgentsExplanationPort(ABC):

    @abstractmethod
    async def explain(self, input_data: ExplanationInput) -> ExplanationOutput: ...


# ---------------------------------------------------------------------------
# 7.5 DeepAgentsMemoryUtilizationPort
# ---------------------------------------------------------------------------


class MemoryUtilizationInput(BaseModel):
    context: ContextSnapshot
    memory_results: list[MemorySearchResult]
    current_situation: SituationDescription


class MemoryUtilizationOutput(BaseModel):
    relevant_experiences: list[Experience]
    applicability_assessment: list[ApplicabilityScore]
    adaptation_suggestions: list[AdaptationSuggestion]


class DeepAgentsMemoryUtilizationPort(ABC):

    @abstractmethod
    async def utilize_memory(
        self, input_data: MemoryUtilizationInput
    ) -> MemoryUtilizationOutput: ...
