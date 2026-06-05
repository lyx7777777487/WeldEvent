"""L1 Cognitive Plane -- Decision output base types.

Source: L1_Port_and_Contract_Design.md (Phase 4, Sections 2.4, 2.5).

Contains shared value objects and the first batch of DecisionOutputContent
subtypes (WorkflowRecommendation, InspectionStrategyRecommendation,
ParameterRecommendation, ROIRecommendation).
"""

from typing import Literal

from pydantic import BaseModel, Field

from src.shared.enums import (
    EffortLevel,
    InspectionStrategyType,
    RiskLevel,
    UrgencyLevel,
    WorkflowType,
)


# ---------------------------------------------------------------------------
# Section 2.4 -- Shared decision value objects
# ---------------------------------------------------------------------------


class RiskFlag(BaseModel):
    flag_type: str
    description: str
    severity: RiskLevel


class Constraint(BaseModel):
    name: str
    value: str
    source: str


class ParameterSet(BaseModel):
    parameters: dict[str, str]


class ParameterAdjustment(BaseModel):
    parameter_name: str
    current_value: str
    proposed_value: str
    unit: str


class ROIEstimate(BaseModel):
    estimated_roi: float
    cost_projection: dict
    benefit_projection: dict
    assumptions: list[str]


class MarginalRange(BaseModel):
    parameter: str
    lower_bound: str
    upper_bound: str
    unit: str


class InspectionStrategy(BaseModel):
    strategy_type: InspectionStrategyType
    coverage_areas: list[str]


class StandardReference(BaseModel):
    standard_id: str
    section: str
    clause: str


class EvidenceReference(BaseModel):
    source: str
    reference_id: str
    description: str


class RiskFactor(BaseModel):
    factor: str
    likelihood: float = Field(ge=0.0, le=1.0)
    impact: RiskLevel


class MitigationSuggestion(BaseModel):
    suggestion: str
    effectiveness: float = Field(ge=0.0, le=1.0)


class RiskImpact(BaseModel):
    risk_level: RiskLevel
    affected_areas: list[str]
    description: str


class RootCauseHypothesis(BaseModel):
    hypothesis: str
    likelihood: float = Field(ge=0.0, le=1.0)
    supporting_evidence: list[EvidenceReference]
    investigation_suggestions: list[str]


class FocusArea(BaseModel):
    area: str
    rationale: str


class Optimization(BaseModel):
    description: str
    target_parameter: str
    current_value: str
    proposed_value: str


class ImpactAssessment(BaseModel):
    quality_impact: str
    efficiency_impact: str
    risk_impact: str


class CoverageArea(BaseModel):
    area_id: str
    area_name: str
    priority: UrgencyLevel


class EscalationTarget(BaseModel):
    target_type: str
    target_id: str


# ---------------------------------------------------------------------------
# Section 2.5 -- Decision point output DTOs (outputs.py batch)
# ---------------------------------------------------------------------------


class WorkflowRecommendation(BaseModel):
    type: Literal["workflow_recommendation"] = "workflow_recommendation"
    workflow_type: WorkflowType
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    inspection_strategy: InspectionStrategy
    parameters: ParameterSet
    roi_estimate: ROIEstimate
    marginal_range: MarginalRange
    risk_flags: list[RiskFlag]


class InspectionStrategyRecommendation(BaseModel):
    type: Literal["inspection_strategy_recommendation"] = "inspection_strategy_recommendation"
    strategy_type: InspectionStrategyType
    coverage_areas: list[CoverageArea]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)


class ParameterRecommendation(BaseModel):
    type: Literal["parameter_recommendation"] = "parameter_recommendation"
    parameters: ParameterSet
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    constraints_applied: list[Constraint]


class ROIRecommendation(BaseModel):
    type: Literal["roi_recommendation"] = "roi_recommendation"
    estimated_roi: float
    cost_projection: dict
    benefit_projection: dict
    confidence: float = Field(ge=0.0, le=1.0)
    assumptions: list[str]


class MarginalRangeRecommendation(BaseModel):
    type: Literal["marginal_range_recommendation"] = "marginal_range_recommendation"
    marginal_ranges: list[MarginalRange]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    applicable_standards: list[StandardReference]


class ParameterAdjustmentRecommendation(BaseModel):
    type: Literal["parameter_adjustment_recommendation"] = "parameter_adjustment_recommendation"
    adjustments: list[ParameterAdjustment]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    risk_impact: RiskImpact
