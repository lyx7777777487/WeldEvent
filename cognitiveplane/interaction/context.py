"""Context management for the Interaction Layer.

ActiveContext, StreamingContext, ContextResolver.
Source: L1-Interaction-Layer-Business-Requirements.md §2.7, §4.3.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from cognitiveplane.interaction.signals.instruction import LiveInstruction
from cognitiveplane.shared.enums import SessionStatus
from cognitiveplane.shared.types import CaseId, OperatorId, SessionId


class StrategySnapshot(BaseModel):
    """Current inspection strategy snapshot."""

    strategy_name: str = ""
    parameters: dict[str, Any] = {}
    last_updated: datetime = Field(default_factory=lambda: datetime.now())


class ActiveContext(BaseModel):
    """Per-operator active context for disambiguation.

    P2-13 fix: uses model_config to allow mutation, removing object.__setattr__.
    """

    model_config = {"frozen": False}

    operator_id: str
    case_id: CaseId | None = None
    active_session_type: str | None = None
    active_session_id: SessionId | None = None
    recent_intents: list[str] = []
    recent_entities: dict[str, Any] = {}

    def update_case(self, case_id: CaseId) -> None:
        """Update the active case."""
        self.case_id = case_id

    def update_session(self, session_type: str, session_id: SessionId) -> None:
        """Update the active session."""
        self.active_session_type = session_type
        self.active_session_id = session_id


class StreamingContext(BaseModel):
    """Real-time production line state tracking."""

    case_id: CaseId
    current_image_index: int = 0
    total_images: int = 0
    current_strategy: StrategySnapshot = Field(default_factory=StrategySnapshot)
    active_instructions: list[LiveInstruction] = []
    last_event_at: datetime | None = None


class ContextResolver:
    """Resolves and manages per-operator ActiveContext."""

    def __init__(self) -> None:
        self._contexts: dict[str, ActiveContext] = {}

    def resolve(self, operator_id: str) -> ActiveContext:
        """Get or create the active context for an operator."""
        if operator_id not in self._contexts:
            self._contexts[operator_id] = ActiveContext(operator_id=operator_id)
        return self._contexts[operator_id]

    def update(self, operator_id: str, context: ActiveContext) -> None:
        """Update the context for an operator."""
        self._contexts[operator_id] = context

    def is_isolated(self, operator_id: str) -> bool:
        """Check if operator has an isolated context."""
        return operator_id in self._contexts
