"""L1 Cognitive Plane -- Validation DTOs.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 2.7).
"""

from datetime import datetime

from pydantic import BaseModel, Field

from src.shared.enums import (
    AggregatedValidationResult,
    ConsistencyStatus,
    RuleStatus,
    SafetyStatus,
    ShadowStatus,
    ValidationStageStatus,
)
from src.shared.types import DecisionId, ValidationId


class DivergencePoint(BaseModel):
    output_field: str
    brain_value: str
    shadow_value: str
    divergence_severity: float = Field(ge=0.0, le=1.0)


class ShadowDecision(BaseModel):
    decision_type: str
    outputs: dict
    confidence: float = Field(ge=0.0, le=1.0)


class SafetyValidationResult(BaseModel):
    result: SafetyStatus
    checked_rules: list[str]
    timestamp: datetime


class RuleValidationResult(BaseModel):
    result: RuleStatus
    violated_rules: list[str]
    timestamp: datetime


class ShadowValidationResult(BaseModel):
    result: ShadowStatus
    shadow_decision: ShadowDecision
    alignment_score: float = Field(ge=0.0, le=1.0)
    divergence_points: list[DivergencePoint]
    timestamp: datetime


class InconsistencyDetail(BaseModel):
    check_type: str
    description: str
    severity: float = Field(ge=0.0, le=1.0)
    reference: str


class ConsistencyValidationResult(BaseModel):
    result: ConsistencyStatus
    factual_consistency: ConsistencyStatus
    historical_consistency: ConsistencyStatus
    inconsistencies: list[InconsistencyDetail]
    timestamp: datetime


class StageResult(BaseModel):
    stage: str
    status: ValidationStageStatus
    result: (
        SafetyValidationResult
        | RuleValidationResult
        | ShadowValidationResult
        | ConsistencyValidationResult
        | None
    ) = None
    duration_ms: int = Field(ge=0)


class ValidationResult(BaseModel):
    validation_id: ValidationId
    decision_id: DecisionId
    safety_result: SafetyValidationResult
    rule_result: RuleValidationResult | None = None
    shadow_result: ShadowValidationResult | None = None
    consistency_result: ConsistencyValidationResult | None = None
    aggregated_result: AggregatedValidationResult
    stages: list[StageResult]
    total_duration_ms: int = Field(ge=0)
    timestamp: datetime
