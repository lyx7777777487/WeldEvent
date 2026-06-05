"""L1 Cognitive Plane -- Human Collaboration DTOs.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 2.10).
"""

from datetime import datetime

from pydantic import BaseModel, Field

from src.shared.enums import CollaborationLayer, ReviewStatus, ReviewType
from src.shared.types import DecisionId, ReviewRequestId, SessionId


class ReviewContent(BaseModel):
    decision_id: DecisionId
    summary: str = Field(min_length=1)
    reasoning_summary: str = Field(min_length=1)
    risk_summary: str
    recommendation: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class ResolutionContent(BaseModel):
    resolution: str = Field(min_length=1)
    conditions: list[str]
    effective_until: datetime | None = None


class HumanReviewRequest(BaseModel):
    request_id: ReviewRequestId
    decision_id: DecisionId
    collaboration_layer: CollaborationLayer
    review_type: ReviewType
    status: ReviewStatus
    content: ReviewContent
    created_at: datetime
    resolved_at: datetime | None = None
    reviewer: str | None = None
    resolution: ResolutionContent | None = None


class FeedbackContent(BaseModel):
    decision_id: DecisionId
    operator_id: str = Field(min_length=1)
    feedback_type: str = Field(min_length=1)
    feedback_text: str = Field(min_length=1)
    rating: int = Field(ge=1, le=5)
    timestamp: datetime


class ConversationMessage(BaseModel):
    role: str
    content: str = Field(min_length=1)
    timestamp: datetime


class ConversationSession(BaseModel):
    session_id: SessionId
    decision_id: DecisionId
    operator_id: str
    messages: list[ConversationMessage]
    status: str
    created_at: datetime
