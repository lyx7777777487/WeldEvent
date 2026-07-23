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

class ToolEffectState(str, Enum):
    """Op 9: Four-state tool-effect ledger.

    Source: Pydantic AI Harness tool-effect ledger (调研报告 §5.4).

    Tracks the side effects of tool calls (external state changes like
    DB writes, workflow launches, file mutations). On crash recovery,
    any tool in STARTED state without a terminal state becomes
    UNKNOWN_AFTER_CRASH, signaling that side effects may or may not
    have been applied.
    """
    STARTED = "started"                # Tool call initiated, side effects may be in progress
    COMPLETED = "completed"            # Tool call finished, all side effects applied successfully
    FAILED = "failed"                  # Tool call finished with error, side effects may be partially applied
    UNKNOWN_AFTER_CRASH = "unknown_after_crash"  # Process crashed while tool was STARTED; effect state unknown



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


@dataclass
class ToolEffectRecord:
    """Op 9: Tool-effect ledger entry tracking external side effects.

    Source: Pydantic AI Harness tool-effect ledger (调研报告 §5.4).

    Unlike ToolCallEvent/ToolResultEvent (which track the tool call itself),
    ToolEffectRecord tracks what the tool *did to the outside world* --
    database writes, workflow launches, file mutations, API calls.

    Four-state lifecycle: STARTED -> COMPLETED | FAILED
    On crash: STARTED -> UNKNOWN_AFTER_CRASH (via mark_unknown_after_crash)

    This is critical for Saga compensation (Op 21) and crash recovery:
    if a tool's effect is UNKNOWN_AFTER_CRASH, the system must verify
    the external state before retrying or compensating.
    """
    tool_call_id: str
    tool_name: str
    state: ToolEffectState
    # Human-readable description of side effects (for audit + compensation)
    side_effect_description: str = ""
    # Structured side-effect data (e.g. {"workflow_id": "...", "action": "launch"})
    side_effect_data: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # When state changed to terminal (COMPLETED/FAILED/UNKNOWN_AFTER_CRASH)
    resolved_at: datetime | None = None

    def is_terminal(self) -> bool:
        return self.state in (ToolEffectState.COMPLETED, ToolEffectState.FAILED, ToolEffectState.UNKNOWN_AFTER_CRASH)

    def to_dict(self) -> dict:
        return {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "state": self.state.value,
            "side_effect_description": self.side_effect_description,
            "side_effect_data": self.side_effect_data,
            "timestamp": self.timestamp.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }


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
        # Op 9: Tool-effect ledger (separate from event list)
        self._tool_effect_ledger: list[ToolEffectRecord] = []

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


    # ── Op 9: ToolEffectRecord ledger ──────────────────────────

    def record_tool_effect_started(
        self,
        tool_call_id: str,
        tool_name: str,
        side_effect_description: str = "",
        side_effect_data: dict | None = None,
    ) -> ToolEffectRecord:
        """Record that a tool call has started and may produce side effects.

        Call this BEFORE the tool executes (after ToolCallEvent is emitted).
        If the process crashes after this call, the effect will be marked
        UNKNOWN_AFTER_CRASH on next recovery.
        """
        record = ToolEffectRecord(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            state=ToolEffectState.STARTED,
            side_effect_description=side_effect_description,
            side_effect_data=side_effect_data or {},
        )
        self._tool_effect_ledger.append(record)
        return record

    def record_tool_effect_completed(
        self,
        tool_call_id: str,
        side_effect_description: str = "",
        side_effect_data: dict | None = None,
    ) -> ToolEffectRecord | None:
        """Mark a tool effect as completed (all side effects applied)."""
        for record in reversed(self._tool_effect_ledger):
            if record.tool_call_id == tool_call_id and not record.is_terminal():
                record.state = ToolEffectState.COMPLETED
                record.resolved_at = datetime.now(timezone.utc)
                if side_effect_description:
                    record.side_effect_description = side_effect_description
                if side_effect_data:
                    record.side_effect_data.update(side_effect_data)
                return record
        return None

    def record_tool_effect_failed(
        self,
        tool_call_id: str,
        error: str = "",
    ) -> ToolEffectRecord | None:
        """Mark a tool effect as failed (side effects may be partially applied)."""
        for record in reversed(self._tool_effect_ledger):
            if record.tool_call_id == tool_call_id and not record.is_terminal():
                record.state = ToolEffectState.FAILED
                record.resolved_at = datetime.now(timezone.utc)
                if error:
                    record.side_effect_description += f" ERROR: {error}"
                return record
        return None

    def mark_unknown_after_crash(self) -> list[ToolEffectRecord]:
        """On crash recovery: mark all STARTED effects as UNKNOWN_AFTER_CRASH.

        Returns the list of records that were marked unknown, so the engine
        can decide how to handle each (retry, verify, compensate, or escalate).
        """
        unknown: list[ToolEffectRecord] = []
        for record in self._tool_effect_ledger:
            if record.state == ToolEffectState.STARTED:
                record.state = ToolEffectState.UNKNOWN_AFTER_CRASH
                record.resolved_at = datetime.now(timezone.utc)
                unknown.append(record)
        return unknown

    def get_tool_effect_ledger(self) -> list[ToolEffectRecord]:
        """Return the full tool-effect ledger."""
        return list(self._tool_effect_ledger)

    def get_unknown_effects(self) -> list[ToolEffectRecord]:
        """Return all effects in UNKNOWN_AFTER_CRASH state (needs attention)."""
        return [
            r for r in self._tool_effect_ledger
            if r.state == ToolEffectState.UNKNOWN_AFTER_CRASH
        ]

    def get_effect_for_tool_call(self, tool_call_id: str) -> ToolEffectRecord | None:
        """Get the latest tool-effect record for a given tool_call_id."""
        for record in reversed(self._tool_effect_ledger):
            if record.tool_call_id == tool_call_id:
                return record
        return None

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self._events)
