"""Tests for instruction lifecycle via InMemoryEventBus."""

import pytest
from uuid import uuid4

from src.interaction.signals.event_bus import InMemoryEventBus
from src.interaction.signals.instruction import LiveInstruction
from src.shared.enums import InstructionStatus, InstructionType
from src.shared.ports.event_bus import InstructionPublishInput
from src.shared.types import CaseId, InstructionId


class TestInstructionLifecycle:
    @pytest.mark.asyncio
    async def test_instruction_creation(self):
        inst = LiveInstruction(
            instruction_id=InstructionId(value=uuid4()),
            instruction_type=InstructionType.UPGRADE_STRATEGY,
            target_case_id=CaseId(value="CASE-001"),
            target_image_index=4,
            reason="anomaly detected",
            issued_by="zhangsan",
        )
        assert inst.status == InstructionStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_instruction_publish_via_eventbus(self):
        bus = InMemoryEventBus()
        result = await bus.publish_instruction(
            InstructionPublishInput(
                instruction_type="upgrade_strategy",
                target_case_id="CASE-001",
                target_image_index=4,
                reason="anomaly",
                issued_by="zhangsan",
            )
        )
        assert result.published is True
        assert len(bus._instructions) == 1

    @pytest.mark.asyncio
    async def test_instruction_supersede(self):
        inst1 = LiveInstruction(
            instruction_id=InstructionId(value=uuid4()),
            instruction_type=InstructionType.UPGRADE_STRATEGY,
            status=InstructionStatus.ACTIVE,
            target_case_id=CaseId(value="CASE-001"),
        )
        inst2_id = InstructionId(value=uuid4())
        inst1_superseded = inst1.model_copy(update={
            "status": InstructionStatus.SUPERSEDED,
            "superseded_by": inst2_id,
        })
        assert inst1_superseded.status == InstructionStatus.SUPERSEDED
        assert inst1_superseded.superseded_by == inst2_id
