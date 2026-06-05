"""Tests for Execution Monitor Mode (Mode C)."""

import pytest
from datetime import datetime, timezone

from src.interaction.base import UserMessage, ResponseType
from src.interaction.dependencies import ModeDependencies
from src.interaction.modes.execution_monitor import ExecutionMonitorMode
from src.interaction.registry import ModeRegistry
from src.shared.dto_gateway import WorkflowState, CaseData
from src.shared.enums import MatchStrategy
from src.shared.types import CaseId, SessionId


class FakeGatewayReadPort:
    def __init__(self):
        pass

    async def read_workflow_state(self, case_id: CaseId) -> WorkflowState:
        return WorkflowState(
            case_id=case_id,
            status="processing",
            current_activity="image_inspection",
            parameters={"current_image": 5, "total_images": 20},
            updated_at=datetime.now(timezone.utc),
        )

    async def read_case(self, case_id: CaseId) -> CaseData:
        return CaseData(
            case_id=case_id,
            case_type="GMAW_T_joint",
            creation_date=datetime.now(timezone.utc),
            status="active",
            metadata={"material": "Q345R"},
        )

    # Stub out other methods to satisfy the protocol
    async def read_measurements(self, case_id, parameter_filter=None):
        return []

    async def read_events(self, case_id, since=None):
        return []

    async def read_audit_trail(self, case_id):
        return []

    async def read_weldmap_snapshot(self, case_id):
        return None


class TestExecutionMonitorMode:
    def test_mode_metadata(self):
        mode = ExecutionMonitorMode()
        assert mode.mode_id == "cognitive.execution_monitor"
        assert mode.creates_session is False
        assert "GatewayReadPort" in mode.required_ports

    def test_keyword_patterns(self):
        mode = ExecutionMonitorMode()
        keyword_pattern = next(
            p for p in mode.intent_patterns if p.match_strategy == MatchStrategy.KEYWORD
        )
        assert "进度" in keyword_pattern.patterns

    @pytest.mark.asyncio
    async def test_handle_with_case_id(self):
        mode = ExecutionMonitorMode()
        deps = ModeDependencies(GatewayReadPort=FakeGatewayReadPort())
        msg = UserMessage(
            raw_text="当前进度怎样",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
            case_id=CaseId(value="CASE-001"),
        )
        response = await mode.handle(msg, deps)
        assert response.response_type == ResponseType.TEXT_REPLY
        assert response.mode_id == "cognitive.execution_monitor"
        assert "CASE-001" in response.text_reply
        assert "processing" in response.text_reply
        assert "GatewayReadPort" in response.ports_accessed

    @pytest.mark.asyncio
    async def test_handle_without_case_id(self):
        mode = ExecutionMonitorMode()
        deps = ModeDependencies(GatewayReadPort=FakeGatewayReadPort())
        msg = UserMessage(
            raw_text="进度怎样",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
        )
        response = await mode.handle(msg, deps)
        assert response.response_type == ResponseType.CLARIFICATION_REQUEST
        assert "案例" in response.text_reply

    def test_register_in_registry(self):
        registry = ModeRegistry()
        registry.register(ExecutionMonitorMode)
        mode = registry.get("cognitive.execution_monitor")
        assert mode is not None
        assert isinstance(mode, ExecutionMonitorMode)
