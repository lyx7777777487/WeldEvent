"""L1 Cognitive Plane — Identity types.

Each identity type wraps a single `value` field as a Pydantic BaseModel,
providing type-safe domain identity distinct from raw primitives.
Source: L1_Port_and_Contract_Design.md (Phase 4, Section 2.2).
"""

from uuid import UUID

from pydantic import BaseModel, Field


class DecisionId(BaseModel):
    value: UUID


class MemoryId(BaseModel):
    value: UUID


class ValidationId(BaseModel):
    value: UUID


class ReviewRequestId(BaseModel):
    value: UUID


class LearningEventId(BaseModel):
    value: UUID


class KnowledgeId(BaseModel):
    value: UUID


class SessionId(BaseModel):
    value: UUID


class CaseId(BaseModel):
    value: str = Field(min_length=1, max_length=256)


class EventId(BaseModel):
    value: UUID


class ImageId(BaseModel):
    value: str = Field(min_length=1, max_length=256)


class InstructionId(BaseModel):
    value: UUID


class OperatorId(BaseModel):
    value: str = Field(min_length=1, max_length=128)
