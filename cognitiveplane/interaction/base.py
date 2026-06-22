"""Core domain models for the Interaction Layer.

ModeProtocol ABC, UserMessage, ModeResponse, IntentPattern, BaseSessionData.
Source: L1-Interaction-Layer-Business-Requirements.md §2.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from cognitiveplane.shared.enums import (
    MatchStrategy,
    ResponseType,
    SessionStatus,
)
from cognitiveplane.shared.types import CaseId, DecisionId, SessionId


class ContextRequirement(BaseModel):
    """Context precondition for intent matching."""

    needs_active_case: bool = False
    needs_active_decision: bool = False
    allowed_workflow_states: list[str] | None = None
    forbidden_cp: list[str] | None = None


class IntentPattern(BaseModel):
    """Single intent matching rule."""

    match_strategy: MatchStrategy
    patterns: list[str]
    required_context: ContextRequirement | None = None
    priority: int = 50
    confidence_threshold: float = Field(default=0.3, ge=0.0, le=1.0)


class UserAction(BaseModel):
    """An action requested from the user."""

    action_type: str
    label: str
    payload: dict[str, Any] = {}


class UserMessage(BaseModel):
    """Standardized inbound message entering the Interaction Layer."""

    raw_text: str = Field(min_length=1)
    operator_id: str = Field(min_length=1)
    timestamp: datetime

    intent_label: str = ""
    intent_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    extracted_entities: dict[str, Any] = {}
    ambiguity_clarifications: list[str] = []

    session_id: SessionId | None = None
    case_id: CaseId | None = None
    decision_id: DecisionId | None = None


class ModeResponse(BaseModel):
    """Standardized outbound response from any Mode."""

    mode_id: str
    session_id: SessionId
    response_type: ResponseType

    text_reply: str | None = None
    structured_output: Any | None = None
    action_required: UserAction | None = None
    follow_up_suggestions: list[str] = []
    ports_accessed: list[str] = []


class BaseSessionData(BaseModel):
    """Base class for all stateful mode session data."""

    mode_id: str
    session_id: SessionId
    case_id: CaseId | None = None
    operator_id: str
    created_at: datetime
    updated_at: datetime
    status: SessionStatus = SessionStatus.ACTIVE
    conversation_turns: int = 0


class ModeProtocol(ABC):
    """Unified contract for all interaction modes.

    All modes must implement this ABC and register via @mode_registry.register.
    """

    mode_id: str
    display_name: str
    description: str

    intent_patterns: list[IntentPattern]
    required_ports: list[str]
    creates_session: bool
    session_data_schema: type | None

    @abstractmethod
    async def handle(
        self, message: UserMessage, deps: "ModeDependencies"
    ) -> ModeResponse:
        """Process user input and return a standardized response."""
        ...
