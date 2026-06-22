"""L1 Cognitive Plane -- Decision assessment subtypes.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 2.5).

Contains: RiskAssessment, ConsensusRecommendation, RootCauseHypotheses.
"""

from typing import Literal

from pydantic import BaseModel, Field

from cognitiveplane.shared.enums import ConsensusStatus, RiskLevel
from cognitiveplane.shared.dto_decision.outputs import (
    EvidenceReference,
    MitigationSuggestion,
    RiskFactor,
    RootCauseHypothesis,
)


class RiskAssessment(BaseModel):
    type: Literal["risk_assessment"] = "risk_assessment"
    risk_level: RiskLevel
    risk_factors: list[RiskFactor]
    confidence: float = Field(ge=0.0, le=1.0)
    mitigation_suggestions: list[MitigationSuggestion]


class ConsensusRecommendation(BaseModel):
    type: Literal["consensus_recommendation"] = "consensus_recommendation"
    consensus_status: ConsensusStatus
    agreeing_evidence: list[EvidenceReference]
    disagreeing_evidence: list[EvidenceReference]
    recommendation: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class RootCauseHypotheses(BaseModel):
    type: Literal["root_cause_hypotheses"] = "root_cause_hypotheses"
    hypotheses: list[RootCauseHypothesis]
    ranked_by_likelihood: bool
    confidence: float = Field(ge=0.0, le=1.0)
