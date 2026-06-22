"""L1 Cognitive Plane -- DeepAgents DTOs.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 2.15).

NOTE: Kept temporarily for Phase 1a compatibility. Will be removed in Phase 1f
when shared/ports/deepagents.py is replaced by control/ports.py.
"""

from pydantic import BaseModel, Field

from cognitiveplane.shared.enums import UrgencyLevel


class Conclusion(BaseModel):
    statement: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence: list[str]


class Plan(BaseModel):
    steps: list[str]
    expected_outcome: str
    confidence: float = Field(ge=0.0, le=1.0)


class Strategy(BaseModel):
    name: str
    description: str
    applicable_conditions: list[str]


class Gap(BaseModel):
    description: str = Field(min_length=1)
    severity: float = Field(ge=0.0, le=1.0)


class Suggestion(BaseModel):
    description: str = Field(min_length=1)
    expected_improvement: str
    priority: UrgencyLevel


class Factor(BaseModel):
    name: str
    description: str
    weight: float = Field(ge=0.0, le=1.0)


class Experience(BaseModel):
    case_id: str
    outcome: str
    applicability: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1)


class ApplicabilityScore(BaseModel):
    experience_id: str
    score: float = Field(ge=0.0, le=1.0)
    reasoning: str


class AdaptationSuggestion(BaseModel):
    parameter: str
    current_value: str
    suggested_value: str
    rationale: str


class SituationDescription(BaseModel):
    current_state: dict
    relevant_factors: list[str]
    constraints: list[str]


__all__ = [
    "Conclusion",
    "Plan",
    "Strategy",
    "Gap",
    "Suggestion",
    "Factor",
    "Experience",
    "ApplicabilityScore",
    "AdaptationSuggestion",
    "SituationDescription",
]
