"""L1 Cognitive Plane -- Learning DTOs.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 2.11).
"""

from datetime import datetime

from pydantic import BaseModel, Field

from cognitiveplane.shared.enums import LearningEventType, ProcessingStatus
from cognitiveplane.shared.types import DecisionId, LearningEventId


class LearningContent(BaseModel):
    event_type: LearningEventType
    source_decision_id: DecisionId | None = None
    data: dict


class LearningEvent(BaseModel):
    learning_event_id: LearningEventId
    event_type: LearningEventType
    source_decision_id: DecisionId | None = None
    content: LearningContent
    processing_status: ProcessingStatus
    created_at: datetime
    processed_at: datetime | None = None


__all__ = [
    "LearningContent",
    "LearningEvent",
]
