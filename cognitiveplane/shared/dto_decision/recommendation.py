"""L1 Cognitive Plane -- Decision recommendation subtypes.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 2.5).

Contains: RDAStrategy, VDAStrategy, RDAStrategyRecommendation,
VDAStrategyRecommendation, RoutingRecommendation, OptimizationRecommendation.
"""

from typing import Literal

from pydantic import BaseModel, Field

from cognitiveplane.shared.enums import ConsensusStatus, EffortLevel, RoutingDecision, UrgencyLevel
from cognitiveplane.shared.dto_decision.outputs import (
    CoverageArea,
    EscalationTarget,
    EvidenceReference,
    FocusArea,
    ImpactAssessment,
    Optimization,
)


# ---------------------------------------------------------------------------
# Strategy value objects
# ---------------------------------------------------------------------------


class RDAStrategy(BaseModel):
    strategy_name: str
    description: str


class VDAStrategy(BaseModel):
    strategy_name: str
    description: str


# ---------------------------------------------------------------------------
# Recommendation subtypes
# ---------------------------------------------------------------------------


class RDAStrategyRecommendation(BaseModel):
    type: Literal["rda_strategy_recommendation"] = "rda_strategy_recommendation"
    strategy: RDAStrategy
    focus_areas: list[FocusArea]
    expected_findings: list[str]
    confidence: float = Field(ge=0.0, le=1.0)


class VDAStrategyRecommendation(BaseModel):
    type: Literal["vda_strategy_recommendation"] = "vda_strategy_recommendation"
    strategy: VDAStrategy
    focus_areas: list[FocusArea]
    expected_findings: list[str]
    confidence: float = Field(ge=0.0, le=1.0)


class RoutingRecommendation(BaseModel):
    type: Literal["routing_recommendation"] = "routing_recommendation"
    route: RoutingDecision
    destination: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)


class OptimizationRecommendation(BaseModel):
    type: Literal["optimization_recommendation"] = "optimization_recommendation"
    optimizations: list[Optimization]
    expected_impact: ImpactAssessment
    confidence: float = Field(ge=0.0, le=1.0)
    implementation_effort: EffortLevel
