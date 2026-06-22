"""Tests for LiveInstruction."""

from datetime import datetime
from uuid import uuid4

from cognitiveplane.interaction.signals.instruction import LiveInstruction
from cognitiveplane.shared.enums import InstructionStatus, InstructionType, InterventionGranularity
from cognitiveplane.shared.types import CaseId, InstructionId


class TestLiveInstruction:
    def test_create(self):
        inst = LiveInstruction(
            instruction_id=InstructionId(value=uuid4()),
            instruction_type=InstructionType.SKIP_IMAGE,
            target_case_id=CaseId(value="CASE-001"),
            target_image_index=3,
            reason="defect detected",
            issued_by="zhangsan",
        )
        assert inst.status == InstructionStatus.ACTIVE
        assert inst.granularity == InterventionGranularity.IMAGE

    def test_with_cp_status(self):
        inst = LiveInstruction(
            instruction_id=InstructionId(value=uuid4()),
            instruction_type=InstructionType.UPGRADE_STRATEGY,
            status=InstructionStatus.SUPERSEDED,
            granularity=InterventionGranularity.CASE,
        )
        assert inst.status == InstructionStatus.SUPERSEDED
