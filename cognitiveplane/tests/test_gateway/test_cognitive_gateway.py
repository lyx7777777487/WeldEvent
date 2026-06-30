"""Tests for CognitiveGateway ports and implementations."""

import pytest

from cognitiveplane.gateway.ports import CognitiveGatewayWritePort, CognitiveGatewayReadPort
from cognitiveplane.gateway.weldmap_client import WeldMapHTTPClient, WeldMapConfig
from cognitiveplane.gateway.write_gateway import WeldMapWriteGateway
from cognitiveplane.gateway.read_gateway import WeldMapReadGateway
from cognitiveplane.shared.dto_decision import BrainDecision, DecisionOutput
from cognitiveplane.shared.dto.gateway import Escalation, PublishResult
from cognitiveplane.shared.enums import (
    AggregatedValidationResult,
    BrainStateType,
    DecisionPointType,
    EventType,
    FallbackMode,
    PersonaType,
    ReasoningMode,
    UrgencyLevel,
)
from cognitiveplane.shared.types import CaseId, DecisionId
from datetime import datetime, timezone
from uuid import uuid4


class TestWeldMapHTTPClient:
    def test_config_from_env(self):
        config = WeldMapConfig.from_env()
        assert isinstance(config, WeldMapConfig)

    @pytest.mark.asyncio
    async def test_write_stub(self):
        client = WeldMapHTTPClient()
        result = await client.write("decision", "key1", {"data": "test"})
        assert result["status"] == "written"

    @pytest.mark.asyncio
    async def test_read_stub(self):
        client = WeldMapHTTPClient()
        result = await client.read("decision", "key1")
        assert result is None


class TestWeldMapWriteGateway:
    @pytest.mark.asyncio
    async def test_publish_decision(self):
        client = WeldMapHTTPClient()
        gateway = WeldMapWriteGateway(client)
        decision = BrainDecision(
            decision_id=DecisionId(value=uuid4()),
            case_id=CaseId(value="test-case"),
            trigger_event_type=EventType.WORKFLOW_ENTERED,
            decision_point=DecisionPointType.DP0,
            persona=PersonaType.COPILOT,
            reasoning_mode=ReasoningMode.ROUTINE,
            state=BrainStateType.VALIDATION,
            outputs=[],
            confidence=0.9,
            created_at=datetime.now(timezone.utc),
        )
        result = await gateway.publish_decision(decision)
        assert result.success is True
        assert "/decision/" in result.weldmap_path

    @pytest.mark.asyncio
    async def test_publish_escalation(self):
        client = WeldMapHTTPClient()
        gateway = WeldMapWriteGateway(client)
        escalation = Escalation(
            escalation_id=uuid4(),
            decision_id=DecisionId(value=uuid4()),
            case_id=CaseId(value="test-case"),
            reason="Test escalation",
            urgency=UrgencyLevel.URGENT,
            fallback_mode=FallbackMode.COGNITIVE_FALLBACK,
            supporting_evidence=[],
            created_at=datetime.now(timezone.utc),
        )
        result = await gateway.publish_escalation(escalation)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_notify_workflow_trigger(self):
        client = WeldMapHTTPClient()
        gateway = WeldMapWriteGateway(client)
        await gateway.notify_workflow_trigger(
            CaseId(value="test-case"),
            {"workflow_type": "FULL"},
        )


class TestWeldMapReadGateway:
    @pytest.mark.asyncio
    async def test_read_weldmap_snapshot(self):
        client = WeldMapHTTPClient()
        gateway = WeldMapReadGateway(client)
        snapshot = await gateway.read_weldmap_snapshot(CaseId(value="test-case"))
        assert snapshot is not None
        assert snapshot.case_id.value == "test-case"

    @pytest.mark.asyncio
    async def test_read_workflow_state_none(self):
        client = WeldMapHTTPClient()
        gateway = WeldMapReadGateway(client)
        result = await gateway.read_workflow_state(CaseId(value="test-case"))
        assert result is None

    @pytest.mark.asyncio
    async def test_read_case_data_none(self):
        client = WeldMapHTTPClient()
        gateway = WeldMapReadGateway(client)
        result = await gateway.read_case_data(CaseId(value="test-case"))
        assert result is None
