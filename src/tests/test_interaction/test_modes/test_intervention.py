"""Tests for Intervention Mode (Mode D)."""

import pytest
from datetime import datetime, timezone

from src.interaction.base import UserMessage, ResponseType
from src.interaction.dependencies import ModeDependencies
from src.interaction.modes.intervention import InterventionMode
from src.interaction.registry import ModeRegistry
from src.interaction.signals.event_bus import InMemoryEventBus
from src.shared.enums import InstructionType, MatchStrategy
from src.shared.ports.event_bus import InstructionPublishPort
from src.shared.types import CaseId


class TestInterventionMode:
    def test_mode_metadata(self):
        mode = InterventionMode()
        assert mode.mode_id == "cognitive.intervention"
        assert mode.creates_session is True
        assert "InstructionPublishPort" in mode.required_ports

    @pytest.mark.asyncio
    async def test_handle_upgrade_strategy(self):
        mode = InterventionMode()
        bus = InMemoryEventBus()
        deps = ModeDependencies(InstructionPublishPort=bus)
        msg = UserMessage(
            raw_text="从图4起加强检测",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
            case_id=CaseId(value="CASE-001"),
        )
        response = await mode.handle(msg, deps)
        assert response.response_type == ResponseType.TEXT_REPLY
        assert "UPGRADE_STRATEGY" in response.text_reply or "upgrade_strategy" in response.text_reply
        assert "产线未停止" in response.text_reply

    @pytest.mark.asyncio
    async def test_handle_downgrade_strategy(self):
        mode = InterventionMode()
        bus = InMemoryEventBus()
        deps = ModeDependencies(InstructionPublishPort=bus)
        msg = UserMessage(
            raw_text="图6起恢复正常",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
            case_id=CaseId(value="CASE-001"),
        )
        response = await mode.handle(msg, deps)
        assert "DOWNGRADE" in response.text_reply or "downgrade" in response.text_reply

    @pytest.mark.asyncio
    async def test_handle_skip_image(self):
        mode = InterventionMode()
        bus = InMemoryEventBus()
        deps = ModeDependencies(InstructionPublishPort=bus)
        msg = UserMessage(
            raw_text="跳过这张",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
            case_id=CaseId(value="CASE-001"),
        )
        response = await mode.handle(msg, deps)
        assert "SKIP" in response.text_reply or "skip" in response.text_reply

    @pytest.mark.asyncio
    async def test_handle_without_image_number(self):
        mode = InterventionMode()
        bus = InMemoryEventBus()
        deps = ModeDependencies(InstructionPublishPort=bus)
        msg = UserMessage(
            raw_text="加强检测",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
            case_id=CaseId(value="CASE-001"),
        )
        response = await mode.handle(msg, deps)
        assert "当前图片起" in response.text_reply

    def test_detect_instruction_type(self):
        assert InterventionMode._detect_instruction_type("加强检测") == InstructionType.UPGRADE_STRATEGY
        assert InterventionMode._detect_instruction_type("恢复正常") == InstructionType.DOWNGRADE_STRATEGY
        assert InterventionMode._detect_instruction_type("跳过这张") == InstructionType.SKIP_IMAGE
        assert InterventionMode._detect_instruction_type("调整参数") == InstructionType.ADJUST_PARAMETER

    def test_detect_image_index(self):
        assert InterventionMode._detect_image_index("从图4起加强") == 4
        assert InterventionMode._detect_image_index("从第3张开始") == 3
        assert InterventionMode._detect_image_index("图5") == 5
        assert InterventionMode._detect_image_index("加强检测") is None

    def test_register_in_registry(self):
        registry = ModeRegistry()
        registry.register(InterventionMode)
        mode = registry.get("cognitive.intervention")
        assert mode is not None
        assert isinstance(mode, InterventionMode)
