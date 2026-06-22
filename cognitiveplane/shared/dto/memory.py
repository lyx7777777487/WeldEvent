"""L1 Cognitive Plane -- Memory DTOs.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 2.8).
"""

from datetime import datetime

from pydantic import BaseModel, Field

from cognitiveplane.shared.enums import MemoryType, PromotionStatus
from cognitiveplane.shared.types import DecisionId, MemoryId

__all__ = [
    "MemoryContent",
    "MemoryRecord",
    "MemorySearchQuery",
    "MemorySearchResult",
]


class MemoryContent(BaseModel):
    summary: str = Field(min_length=1)
    details: dict
    feature_vector: list[float]


class MemorySearchQuery(BaseModel):
    case_features: dict
    feature_vector: list[float]
    memory_type: MemoryType | None = None
    max_results: int = Field(default=10, ge=1, le=100)
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status_filter: list[PromotionStatus] = Field(
        default=[PromotionStatus.PROMOTED, PromotionStatus.VALIDATED]
    )


class MemorySearchResult(BaseModel):
    memory_id: MemoryId
    content: MemoryContent
    similarity_score: float = Field(ge=0.0, le=1.0)
    promotion_status: PromotionStatus
    created_at: datetime


class MemoryRecord(BaseModel):
    memory_id: MemoryId
    memory_type: MemoryType
    content: MemoryContent
    source_decision_id: DecisionId
    promotion_status: PromotionStatus
    created_at: datetime
    promoted_at: datetime | None = None
    archived_at: datetime | None = None
