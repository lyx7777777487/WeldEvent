"""L1 Cognitive Plane -- Context DTOs.

Source: L1_Port_and_Contract_Design.md (Phase 4, Sections 2.3, 3.1, 3.2, 3.3).
"""

from datetime import datetime

from pydantic import BaseModel, Field

from src.shared.enums import EventType, NoveltyLevel
from src.shared.types import CaseId, EventId


# ---------------------------------------------------------------------------
# Section 2.3 -- Context snapshots
# ---------------------------------------------------------------------------


class ContextSnapshot(BaseModel):
    case_id: CaseId
    event_type: EventType
    workflow_state: dict
    case_data: dict
    measurements: list[dict]
    memory_match_confidence: float = Field(ge=0.0, le=1.0)
    knowledge_coverage: float = Field(ge=0.0, le=1.0)
    event_novelty: NoveltyLevel
    validation_critical_count: int = Field(ge=0)
    timestamp: datetime


class WeldMapSnapshot(BaseModel):
    case_id: CaseId
    workflow_state: dict
    measurements: list[dict]
    decisions: list[dict]
    events: list[dict]
    snapshot_at: datetime


# ---------------------------------------------------------------------------
# Section 3.1 -- Domain event base
# ---------------------------------------------------------------------------


class DomainEvent(BaseModel):
    event_id: EventId
    event_type: EventType
    case_id: CaseId
    timestamp: datetime
    source: str
    payload: dict


# ---------------------------------------------------------------------------
# Section 3.2 -- Typed event payloads
# ---------------------------------------------------------------------------


class WorkflowEnteredPayload(BaseModel):
    workflow_type: str
    case_type: str
    parameters: dict
    operator_id: str


class IQACompletedPayload(BaseModel):
    iqa_result: str
    defect_indicators: list[str]
    measurement_summary: dict
    inspector_id: str


class PPACompletedPayload(BaseModel):
    ppa_result: str
    parameter_deviations: list[dict]
    quality_indicators: dict
    inspector_id: str


class MEACompletedPayload(BaseModel):
    mea_result: str
    defect_classification: str | None = None
    severity: "RiskLevel"
    location: str | None = None
    inspector_id: str


class RDAVDACompletedPayload(BaseModel):
    rda_findings: list[dict]
    vda_findings: list[dict]
    combined_assessment: str
    inspector_id: str


class HumanFeedbackReceivedPayload(BaseModel):
    decision_id: "DecisionId"
    operator_id: str
    feedback_type: str
    feedback_text: str
    rating: int = Field(ge=1, le=5)


class MemoryPromotionRequestedPayload(BaseModel):
    memory_id: "MemoryId"
    promotion_rationale: str
    source_decision_id: "DecisionId"


class ValidationCriticalPayload(BaseModel):
    validation_id: "ValidationId"
    decision_id: "DecisionId"
    consecutive_critical_count: int = Field(ge=0)
    fallback_mode: "FallbackMode"


# ---------------------------------------------------------------------------
# Section 3.3 -- Brain-internal events
# ---------------------------------------------------------------------------


from src.shared.enums import BrainInternalEventType  # noqa: E402
from src.shared.types import DecisionId  # noqa: E402
from src.shared.enums import FallbackMode  # noqa: E402
from src.shared.enums import RiskLevel  # noqa: E402
from src.shared.types import MemoryId  # noqa: E402
from src.shared.types import ValidationId  # noqa: E402


# Resolve forward references
MEACompletedPayload.model_rebuild()
HumanFeedbackReceivedPayload.model_rebuild()
MemoryPromotionRequestedPayload.model_rebuild()
ValidationCriticalPayload.model_rebuild()


class BrainInternalEvent(BaseModel):
    event_type: BrainInternalEventType
    decision_id: DecisionId
    timestamp: datetime
    payload: dict
