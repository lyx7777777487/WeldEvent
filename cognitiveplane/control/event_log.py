"""Append-only EventLog for decision pipeline — inspired by OpenHands EventLog.

Source: 7-plane redesign spec §5 Append-Only EventLog.
Per-run event log: each Orchestrator execution creates a fresh EventLog.
Supports audit trail, time-travel debugging, and Action↔Observation pairing.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from cognitiveplane.shared.types import CaseId


class BrainEventType(str, Enum):
    STATE_TRANSITION = "state_transition"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    VALIDATION = "validation"
    DECISION = "decision"
    CHECKPOINT = "checkpoint"


@dataclass(frozen=True)
class BrainEvent:
    """Immutable event in the decision pipeline.

    Frozen to enforce append-only semantics.
    Modeled after OpenHands Event base (event/base.py:21-32).
    """
    event_id: str
    timestamp: datetime
    event_type: BrainEventType
    source: str
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCallEvent(BrainEvent):
    """Action: Brain initiates a tool call. Carries tool_call_id for pairing.

    Modeled after OpenHands ActionEvent.tool_call_id.
    """
    tool_call_id: str = ""
    tool_name: str = ""
    arguments: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResultEvent(BrainEvent):
    """Observation: Tool execution result. Pairs back to ToolCallEvent via action_id.

    Modeled after OpenHands ObservationEvent.action_id (observation.py:35-36).
    Dual-ID pairing: action_id back-pointer + tool_call_id grouping.
    """
    action_id: str = ""
    tool_call_id: str = ""
    success: bool = True
    result: dict | None = None
    error: str | None = None


class DecisionPipelineView:
    """Incremental projection — current state from event stream.

    Modeled after OpenHands View.from_events() (context/view/view.py:143-161).
    Reconstructs current pipeline state by replaying events.
    """

    def __init__(self) -> None:
        self.state_transitions: list[dict] = []
        self.tool_calls: list[dict] = []
        self.tool_results: list[dict] = []
        self.validations: list[dict] = []
        self.decisions: list[dict] = []
        self.checkpoints: list[dict] = []

    def apply(self, event: BrainEvent) -> None:
        if event.event_type == BrainEventType.STATE_TRANSITION:
            self.state_transitions.append(event.data)
        elif event.event_type == BrainEventType.TOOL_CALL:
            self.tool_calls.append(event.data)
        elif event.event_type == BrainEventType.TOOL_RESULT:
            self.tool_results.append(event.data)
        elif event.event_type == BrainEventType.VALIDATION:
            self.validations.append(event.data)
        elif event.event_type == BrainEventType.DECISION:
            self.decisions.append(event.data)
        elif event.event_type == BrainEventType.CHECKPOINT:
            self.checkpoints.append(event.data)

    @property
    def current_state(self) -> str | None:
        if self.state_transitions:
            return self.state_transitions[-1].get("to_state")
        return None

    @property
    def last_validation_result(self) -> str | None:
        if self.validations:
            return self.validations[-1].get("result")
        return None

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_calls)


class EventLog:
    """Append-only event log for a single decision pipeline run.

    Modeled after OpenHands EventLog (conversation/event_store.py:24).
    Enforces uniqueness of event_id to prevent duplicates.
    """

    def __init__(self, case_id: CaseId) -> None:
        self.case_id = case_id
        self._events: list[BrainEvent] = []

    def append(self, event: BrainEvent) -> BrainEvent:
        """Append an event. Raises ValueError if event_id already exists."""
        if any(e.event_id == event.event_id for e in self._events):
            raise ValueError(f"Event with ID '{event.event_id}' already exists")
        self._events.append(event)
        return event

    def emit(self, event_type: BrainEventType, source: str, data: dict | None = None) -> BrainEvent:
        """Create and append a generic BrainEvent."""
        event = BrainEvent(
            event_id=str(uuid4()),
            timestamp=datetime.now(timezone.utc),
            event_type=event_type,
            source=source,
            data=data or {},
        )
        return self.append(event)

    def emit_tool_call(self, source: str, tool_name: str, arguments: dict | None = None) -> ToolCallEvent:
        """Create and append a ToolCallEvent."""
        tool_call_id = str(uuid4())
        event = ToolCallEvent(
            event_id=str(uuid4()),
            timestamp=datetime.now(timezone.utc),
            event_type=BrainEventType.TOOL_CALL,
            source=source,
            data={"tool_name": tool_name},
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments or {},
        )
        self.append(event)
        return event

    def emit_tool_result(
        self,
        source: str,
        action_id: str,
        tool_call_id: str,
        success: bool,
        result: dict | None = None,
        error: str | None = None,
    ) -> ToolResultEvent:
        """Create and append a ToolResultEvent.

        action_id: back-pointer to the ToolCallEvent.event_id.
        tool_call_id: same tool_call_id as the paired ToolCallEvent.
        """
        event = ToolResultEvent(
            event_id=str(uuid4()),
            timestamp=datetime.now(timezone.utc),
            event_type=BrainEventType.TOOL_RESULT,
            source=source,
            data={"success": success},
            action_id=action_id,
            tool_call_id=tool_call_id,
            success=success,
            result=result,
            error=error,
        )
        self.append(event)
        return event

    def query(
        self,
        event_type: BrainEventType | None = None,
        source: str | None = None,
    ) -> list[BrainEvent]:
        """Query events by type and/or source."""
        return [
            e for e in self._events
            if (event_type is None or e.event_type == event_type)
            and (source is None or e.source == source)
        ]

    def find_paired_result(self, tool_call_event: ToolCallEvent) -> ToolResultEvent | None:
        """Find the Observation paired with a ToolCall, via action_id back-pointer."""
        for e in self._events:
            if isinstance(e, ToolResultEvent) and e.action_id == tool_call_event.event_id:
                return e
        return None

    def find_events_by_tool_call_id(self, tool_call_id: str) -> list[BrainEvent]:
        """Find all events grouped by tool_call_id."""
        return [
            e for e in self._events
            if isinstance(e, (ToolCallEvent, ToolResultEvent))
            and getattr(e, 'tool_call_id', '') == tool_call_id
        ]

    def project_view(self) -> DecisionPipelineView:
        """Incremental projection — current state from event stream.

        Modeled after OpenHands View.from_events() (context/view/view.py:143-161).
        """
        view = DecisionPipelineView()
        for event in self._events:
            view.apply(event)
        return view

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self._events)
