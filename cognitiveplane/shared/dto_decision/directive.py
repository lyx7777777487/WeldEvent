"""L1 Cognitive Plane -- Decision directive subtypes.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 2.5).

Contains: SkipRecommendation, EscalationRecommendation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from cognitiveplane.shared.enums import UrgencyLevel
from cognitiveplane.shared.dto_decision.outputs import EscalationTarget, EvidenceReference
from cognitiveplane.shared.dto_decision.assessment import RiskAssessment


class SkipRecommendation(BaseModel):
    type: Literal["skip_recommendation"] = "skip_recommendation"
    skippable: bool
    justification: str = Field(min_length=1)
    risk_if_skipped: RiskAssessment
    confidence: float = Field(ge=0.0, le=1.0)


class EscalationRecommendation(BaseModel):
    type: Literal["escalation_recommendation"] = "escalation_recommendation"
    escalate: bool
    escalation_reason: str = Field(min_length=1)
    escalation_target: EscalationTarget
    urgency: UrgencyLevel
    supporting_evidence: list[EvidenceReference]
