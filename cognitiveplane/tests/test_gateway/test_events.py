"""Tests for gateway/events.py — GatewayActionEvent, GatewayObservationEvent, dual-ID pairing."""

from datetime import datetime, timezone

import pytest

from cognitiveplane.gateway.events import GatewayActionEvent, GatewayObservationEvent


class TestGatewayActionEvent:
    def test_create_action_event(self):
        event = GatewayActionEvent(
            action_id="act-1",
            tool_call_id="tci-1",
            action_type="publish_decision",
            domain="decision",
            payload={"decision_id": "d-123"},
        )
        assert event.action_id == "act-1"
        assert event.tool_call_id == "tci-1"
        assert event.action_type == "publish_decision"
        assert event.domain == "decision"

    def test_action_event_is_frozen(self):
        event = GatewayActionEvent(
            action_id="act-1",
            tool_call_id="tci-1",
            action_type="publish_decision",
            domain="decision",
        )
        with pytest.raises(AttributeError):
            event.action_type = "changed"

    def test_action_event_has_timestamp(self):
        event = GatewayActionEvent(
            action_id="act-1",
            tool_call_id="tci-1",
            action_type="publish_decision",
            domain="decision",
        )
        assert isinstance(event.timestamp, datetime)


class TestGatewayObservationEvent:
    def test_create_observation_event(self):
        event = GatewayObservationEvent(
            observation_id="obs-1",
            action_id="act-1",
            tool_call_id="tci-1",
            success=True,
            domain="decision",
            result={"weldmap_path": "/decision/d-123"},
        )
        assert event.observation_id == "obs-1"
        assert event.action_id == "act-1"
        assert event.tool_call_id == "tci-1"
        assert event.success is True

    def test_observation_event_is_frozen(self):
        event = GatewayObservationEvent(
            observation_id="obs-1",
            action_id="act-1",
            tool_call_id="tci-1",
            success=True,
            domain="decision",
        )
        with pytest.raises(AttributeError):
            event.success = False

    def test_observation_event_with_error(self):
        event = GatewayObservationEvent(
            observation_id="obs-2",
            action_id="act-2",
            tool_call_id="tci-2",
            success=False,
            domain="negotiation",
            error="WeldMap write failed",
        )
        assert event.error == "WeldMap write failed"
        assert event.result is None


class TestDualIDPairing:
    def test_pairing_via_action_id(self):
        """Observation pairs back to Action via action_id back-pointer."""
        action = GatewayActionEvent(
            action_id="act-1",
            tool_call_id="tci-1",
            action_type="publish_decision",
            domain="decision",
            payload={"decision_id": "d-123"},
        )
        observation = GatewayObservationEvent(
            observation_id="obs-1",
            action_id=action.action_id,
            tool_call_id=action.tool_call_id,
            success=True,
            domain="decision",
            result={"weldmap_path": "/decision/d-123"},
        )
        assert observation.action_id == action.action_id
        assert observation.tool_call_id == action.tool_call_id

    def test_tool_call_id_groups_events(self):
        """Multiple actions can share the same tool_call_id for grouping."""
        tci = "tci-group-1"
        action1 = GatewayActionEvent(
            action_id="act-1",
            tool_call_id=tci,
            action_type="publish_decision",
            domain="decision",
        )
        action2 = GatewayActionEvent(
            action_id="act-2",
            tool_call_id=tci,
            action_type="publish_escalation",
            domain="negotiation",
        )
        assert action1.tool_call_id == action2.tool_call_id
