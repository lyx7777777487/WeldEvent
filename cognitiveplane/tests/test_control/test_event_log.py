"""Tests for control/event_log.py — EventLog, BrainEvent, ToolCallEvent, ToolResultEvent, DecisionPipelineView."""

from datetime import datetime, timezone

import pytest

from cognitiveplane.control.event_log import (
    BrainEvent,
    BrainEventType,
    DecisionPipelineView,
    EventLog,
    ToolCallEvent,
    ToolResultEvent,
)
from cognitiveplane.shared.types import CaseId


def _make_event_log() -> EventLog:
    return EventLog(case_id=CaseId(value="test-case"))


class TestBrainEvent:
    def test_brain_event_is_frozen(self):
        event = BrainEvent(
            event_id="e1",
            timestamp=datetime.now(timezone.utc),
            event_type=BrainEventType.STATE_TRANSITION,
            source="orchestrator",
            data={"from": "IDLE", "to": "OBSERVING"},
        )
        with pytest.raises(AttributeError):
            event.event_id = "changed"

    def test_brain_event_fields(self):
        event = BrainEvent(
            event_id="e1",
            timestamp=datetime.now(timezone.utc),
            event_type=BrainEventType.VALIDATION,
            source="governance",
            data={"result": "APPROVED"},
        )
        assert event.event_type == BrainEventType.VALIDATION
        assert event.source == "governance"
        assert event.data["result"] == "APPROVED"


class TestToolCallEvent:
    def test_tool_call_event_is_frozen(self):
        event = ToolCallEvent(
            event_id="tc1",
            timestamp=datetime.now(timezone.utc),
            event_type=BrainEventType.TOOL_CALL,
            source="react",
            data={"tool_name": "search_standards"},
            tool_call_id="tci1",
            tool_name="search_standards",
            arguments={"query": "ISO 5817"},
        )
        assert event.tool_call_id == "tci1"
        assert event.tool_name == "search_standards"
        with pytest.raises(AttributeError):
            event.tool_name = "changed"


class TestToolResultEvent:
    def test_tool_result_event_pairing(self):
        result = ToolResultEvent(
            event_id="tr1",
            timestamp=datetime.now(timezone.utc),
            event_type=BrainEventType.TOOL_RESULT,
            source="react",
            data={"success": True},
            action_id="tc1",
            tool_call_id="tci1",
            success=True,
            result={"standards": ["ISO 5817"]},
        )
        assert result.action_id == "tc1"
        assert result.tool_call_id == "tci1"
        assert result.success is True


class TestEventLog:
    def test_append_event(self):
        log = _make_event_log()
        event = BrainEvent(
            event_id="e1",
            timestamp=datetime.now(timezone.utc),
            event_type=BrainEventType.STATE_TRANSITION,
            source="orchestrator",
            data={},
        )
        result = log.append(event)
        assert result is event
        assert len(log) == 1

    def test_append_duplicate_event_id_raises(self):
        log = _make_event_log()
        event = BrainEvent(
            event_id="e1",
            timestamp=datetime.now(timezone.utc),
            event_type=BrainEventType.STATE_TRANSITION,
            source="orchestrator",
            data={},
        )
        log.append(event)
        with pytest.raises(ValueError, match="already exists"):
            log.append(event)

    def test_emit(self):
        log = _make_event_log()
        event = log.emit(BrainEventType.STATE_TRANSITION, "orchestrator", {"from": "IDLE", "to": "OBSERVING"})
        assert event.event_type == BrainEventType.STATE_TRANSITION
        assert event.source == "orchestrator"
        assert len(log) == 1

    def test_emit_tool_call(self):
        log = _make_event_log()
        event = log.emit_tool_call("react", "search_standards", {"query": "ISO 5817"})
        assert isinstance(event, ToolCallEvent)
        assert event.tool_name == "search_standards"
        assert event.tool_call_id != ""
        assert event.arguments["query"] == "ISO 5817"

    def test_emit_tool_result(self):
        log = _make_event_log()
        call = log.emit_tool_call("react", "search_standards", {"query": "ISO 5817"})
        result = log.emit_tool_result(
            source="react",
            action_id=call.event_id,
            tool_call_id=call.tool_call_id,
            success=True,
            result={"standards": ["ISO 5817"]},
        )
        assert isinstance(result, ToolResultEvent)
        assert result.action_id == call.event_id
        assert result.tool_call_id == call.tool_call_id

    def test_query_by_type(self):
        log = _make_event_log()
        log.emit(BrainEventType.STATE_TRANSITION, "orchestrator", {})
        log.emit(BrainEventType.VALIDATION, "governance", {})
        log.emit(BrainEventType.STATE_TRANSITION, "orchestrator", {})

        state_events = log.query(event_type=BrainEventType.STATE_TRANSITION)
        assert len(state_events) == 2

    def test_query_by_source(self):
        log = _make_event_log()
        log.emit(BrainEventType.STATE_TRANSITION, "orchestrator", {})
        log.emit(BrainEventType.VALIDATION, "governance", {})

        gov_events = log.query(source="governance")
        assert len(gov_events) == 1
        assert gov_events[0].source == "governance"

    def test_query_by_type_and_source(self):
        log = _make_event_log()
        log.emit(BrainEventType.STATE_TRANSITION, "orchestrator", {})
        log.emit(BrainEventType.STATE_TRANSITION, "react", {})
        log.emit(BrainEventType.VALIDATION, "orchestrator", {})

        events = log.query(event_type=BrainEventType.STATE_TRANSITION, source="orchestrator")
        assert len(events) == 1

    def test_find_paired_result(self):
        log = _make_event_log()
        call = log.emit_tool_call("react", "search_standards", {"query": "ISO 5817"})
        log.emit_tool_result(
            source="react",
            action_id=call.event_id,
            tool_call_id=call.tool_call_id,
            success=True,
            result={"standards": ["ISO 5817"]},
        )

        paired = log.find_paired_result(call)
        assert paired is not None
        assert paired.success is True
        assert paired.action_id == call.event_id

    def test_find_paired_result_not_found(self):
        log = _make_event_log()
        call = log.emit_tool_call("react", "search_standards", {})
        # No result emitted
        assert log.find_paired_result(call) is None

    def test_find_events_by_tool_call_id(self):
        log = _make_event_log()
        call = log.emit_tool_call("react", "search_standards", {})
        log.emit_tool_result(
            source="react",
            action_id=call.event_id,
            tool_call_id=call.tool_call_id,
            success=True,
        )

        events = log.find_events_by_tool_call_id(call.tool_call_id)
        assert len(events) == 2

    def test_len(self):
        log = _make_event_log()
        assert len(log) == 0
        log.emit(BrainEventType.STATE_TRANSITION, "orchestrator", {})
        assert len(log) == 1

    def test_iter(self):
        log = _make_event_log()
        log.emit(BrainEventType.STATE_TRANSITION, "orchestrator", {})
        log.emit(BrainEventType.VALIDATION, "governance", {})
        events = list(log)
        assert len(events) == 2


class TestDecisionPipelineView:
    def test_empty_view(self):
        view = DecisionPipelineView()
        assert view.current_state is None
        assert view.last_validation_result is None
        assert view.tool_call_count == 0

    def test_apply_state_transition(self):
        view = DecisionPipelineView()
        view.apply(BrainEvent(
            event_id="e1",
            timestamp=datetime.now(timezone.utc),
            event_type=BrainEventType.STATE_TRANSITION,
            source="orchestrator",
            data={"to_state": "OBSERVING"},
        ))
        assert view.current_state == "OBSERVING"
        assert len(view.state_transitions) == 1

    def test_apply_tool_call(self):
        view = DecisionPipelineView()
        view.apply(BrainEvent(
            event_id="e1",
            timestamp=datetime.now(timezone.utc),
            event_type=BrainEventType.TOOL_CALL,
            source="react",
            data={"tool_name": "search_standards"},
        ))
        assert view.tool_call_count == 1

    def test_apply_validation(self):
        view = DecisionPipelineView()
        view.apply(BrainEvent(
            event_id="e1",
            timestamp=datetime.now(timezone.utc),
            event_type=BrainEventType.VALIDATION,
            source="governance",
            data={"result": "APPROVED"},
        ))
        assert view.last_validation_result == "APPROVED"

    def test_project_view_from_event_log(self):
        log = _make_event_log()
        log.emit(BrainEventType.STATE_TRANSITION, "orchestrator", {"to_state": "OBSERVING"})
        log.emit(BrainEventType.TOOL_CALL, "react", {"tool_name": "search_standards"})
        log.emit(BrainEventType.VALIDATION, "governance", {"result": "APPROVED"})

        view = log.project_view()
        assert view.current_state == "OBSERVING"
        assert view.tool_call_count == 1
        assert view.last_validation_result == "APPROVED"

    def test_project_view_accumulates(self):
        log = _make_event_log()
        log.emit(BrainEventType.STATE_TRANSITION, "orchestrator", {"to_state": "OBSERVING"})
        log.emit(BrainEventType.STATE_TRANSITION, "orchestrator", {"to_state": "VALIDATION"})

        view = log.project_view()
        assert view.current_state == "VALIDATION"
        assert len(view.state_transitions) == 2
