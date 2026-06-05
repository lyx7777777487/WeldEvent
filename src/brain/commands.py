"""L1 Cognitive Plane — Brain command schemas.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 4.1).
"""

from uuid import UUID

from pydantic import BaseModel, Field

from src.shared.enums import DecisionPointType, FallbackMode
from src.shared.events import DomainEvent
from src.shared.dto_context import ContextSnapshot
from src.shared.dto_decision import BrainDecision
from src.shared.dto_validation import ValidationResult
from src.shared.dto_persona import PersonaFrame
from src.shared.dto_knowledge import KnowledgeResult
from src.shared.dto_memory import MemorySearchResult
from src.shared.dto_collaboration import FeedbackContent
from src.shared.types import DecisionId


class ProcessEventCommand(BaseModel):
    event: DomainEvent
    correlation_id: UUID


class SelectReasoningModeCommand(BaseModel):
    context: ContextSnapshot


class SelectPersonaCommand(BaseModel):
    decision_point: DecisionPointType
    context: ContextSnapshot
    escalation_active: bool


class ExecuteReasoningCommand(BaseModel):
    context: ContextSnapshot
    persona_frame: PersonaFrame
    knowledge_results: list[KnowledgeResult]
    memory_results: list[MemorySearchResult]


class ExecuteMemoryMatchingCommand(BaseModel):
    context: ContextSnapshot
    memory_results: list[MemorySearchResult]


class SubmitForValidationCommand(BaseModel):
    context: ContextSnapshot
    decision: BrainDecision


class PublishDecisionCommand(BaseModel):
    """Brain-internal publish command — includes validation result."""

    decision: BrainDecision
    validation_result: ValidationResult


class PublishEscalationCommand(BaseModel):
    decision: BrainDecision
    validation_result: ValidationResult
    fallback_mode: FallbackMode


class RecordFeedbackCommand(BaseModel):
    feedback: FeedbackContent
    original_decision_id: DecisionId
