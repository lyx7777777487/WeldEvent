"""Tests for CopilotOperate — production line operation guidance."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from cognitiveplane.copilot.operate import CopilotOperate, OperationGuidance
from cognitiveplane.gateway.adapters.in_memory import InMemoryGatewayAdapter
from cognitiveplane.gateway.ports import CognitiveGatewayWritePort
from cognitiveplane.interaction.signals.instruction import LiveInstruction
from cognitiveplane.shared.dto_gateway import PublishResult
from cognitiveplane.shared.enums import InstructionType
from cognitiveplane.shared.types import CaseId, InstructionId


class _StubInstructionGateway(InMemoryGatewayAdapter):
    """InMemoryGatewayAdapter + publish_instruction support."""

    def __init__(self):
        super().__init__()
        self._published_instructions: list[LiveInstruction] = []

    async def publish_instruction(self, instruction: LiveInstruction) -> PublishResult:
        self._published_instructions.append(instruction)
        return PublishResult(
            success=True,
            weldmap_path=f"/instructions/{instruction.instruction_id.value}",
            timestamp=datetime.now(timezone.utc),
        )


@pytest.fixture
def gateway() -> _StubInstructionGateway:
    return _StubInstructionGateway()


@pytest.fixture
def operate(gateway: _StubInstructionGateway) -> CopilotOperate:
    return CopilotOperate(gateway_read=gateway, gateway_write=gateway)


class TestCopilotOperate:
    @pytest.mark.asyncio
    async def test_recommend_without_publish(
        self, operate: CopilotOperate
    ) -> None:
        case_id = CaseId(value="case-001")
        guidance = await operate.recommend(
            case_id=case_id,
            rationale="Test guidance",
        )
        assert isinstance(guidance, OperationGuidance)
        assert guidance.case_id == case_id
        assert guidance.instruction.reason == "Test guidance"
        assert guidance.publish_result is None

    @pytest.mark.asyncio
    async def test_recommend_with_publish(
        self, operate: CopilotOperate, gateway: _StubInstructionGateway
    ) -> None:
        case_id = CaseId(value="case-002")
        guidance = await operate.recommend(
            case_id=case_id,
            publish=True,
            rationale="Published guidance",
        )
        assert guidance.publish_result is not None
        assert guidance.publish_result.success is True
        assert len(gateway._published_instructions) == 1

    @pytest.mark.asyncio
    async def test_recommend_auto_rationale(
        self, operate: CopilotOperate
    ) -> None:
        case_id = CaseId(value="case-003")
        guidance = await operate.recommend(case_id=case_id)
        assert "copilot:" in guidance.rationale
        assert "annotate" in guidance.rationale
