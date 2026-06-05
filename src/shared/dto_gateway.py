"""L1 Cognitive Plane -- Gateway DTOs.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 2.14).
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from src.shared.enums import (
    AudienceType,
    ConsensusStatus,
    FallbackMode,
    RiskLevel,
    UrgencyLevel,
)
from src.shared.types import CaseId, DecisionId, MemoryId
from src.shared.dto_decision.outputs import EvidenceReference, ParameterAdjustment


class PublishResult(BaseModel):
    success: bool
    weldmap_path: str
    timestamp: datetime


class Explanation(BaseModel):
    explanation_id: UUID
    decision_id: DecisionId
    audience: AudienceType
    explanation_text: str = Field(min_length=1)
    key_factors: list[str]
    confidence_justification: str
    created_at: datetime


class ParameterPatch(BaseModel):
    patch_id: UUID
    decision_id: DecisionId
    case_id: CaseId
    parameter_adjustments: list[ParameterAdjustment]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    created_at: datetime


class RecheckRequest(BaseModel):
    recheck_id: UUID
    decision_id: DecisionId
    case_id: CaseId
    recheck_type: str = Field(min_length=1)
    target_areas: list[str]
    rationale: str = Field(min_length=1)
    urgency: UrgencyLevel
    created_at: datetime


class ConsensusRequest(BaseModel):
    consensus_id: UUID
    decision_id: DecisionId
    case_id: CaseId
    consensus_status: ConsensusStatus
    agreeing_evidence: list[str]
    disagreeing_evidence: list[str]
    recommendation: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    created_at: datetime


class RiskAlert(BaseModel):
    alert_id: UUID
    decision_id: DecisionId
    case_id: CaseId
    risk_level: RiskLevel
    risk_factors: list[str]
    mitigation_suggestions: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    created_at: datetime


class Escalation(BaseModel):
    escalation_id: UUID
    decision_id: DecisionId
    case_id: CaseId
    reason: str = Field(min_length=1)
    urgency: UrgencyLevel
    fallback_mode: FallbackMode
    supporting_evidence: list[EvidenceReference]
    created_at: datetime


class PromotionRequest(BaseModel):
    memory_id: MemoryId
    promotion_rationale: str = Field(min_length=1)


class WorkflowState(BaseModel):
    case_id: CaseId
    status: str
    current_activity: str | None = None
    parameters: dict
    updated_at: datetime


class CaseData(BaseModel):
    case_id: CaseId
    case_type: str
    creation_date: datetime
    status: str
    metadata: dict


class Measurement(BaseModel):
    measurement_id: str
    case_id: CaseId
    parameter: str
    value: str
    unit: str
    timestamp: datetime


class AuditEntry(BaseModel):
    entry_id: str
    case_id: CaseId
    action: str
    actor: str
    timestamp: datetime
    details: dict
