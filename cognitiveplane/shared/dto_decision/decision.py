"""L1 Cognitive Plane -- BrainDecision and DecisionOutput DTOs.

Source: L1_Port_and_Contract_Design.md (Phase 4, Sections 2.5, 2.6).
"""

from datetime import datetime

from pydantic import BaseModel, Field

from cognitiveplane.shared.enums import (
    AggregatedValidationResult,
    BrainStateType,
    DecisionPointType,
    EventType,
    PersonaType,
    ReasoningMode,
)
from cognitiveplane.shared.types import CaseId, DecisionId


class DecisionOutput(BaseModel):
    content: "DecisionOutputContent"
    confidence: float = Field(ge=0.0, le=1.0)


class BrainDecision(BaseModel):
    decision_id: DecisionId
    case_id: CaseId
    trigger_event_type: EventType
    decision_point: DecisionPointType
    persona: PersonaType
    reasoning_mode: ReasoningMode
    state: BrainStateType
    outputs: list[DecisionOutput]
    validation_result: AggregatedValidationResult | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    created_at: datetime
    published_at: datetime | None = None
