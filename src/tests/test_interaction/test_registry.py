"""Tests for ModeRegistry and @register decorator."""

import pytest

from src.interaction.base import IntentPattern, ModeProtocol, UserMessage, ModeResponse
from src.interaction.registry import ModeRegistry, mode_registry
from src.shared.enums import MatchStrategy


class _FakeMode(ModeProtocol):
    mode_id = "test.fake"
    display_name = "Fake"
    description = "A test mode"
    intent_patterns = [
        IntentPattern(
            match_strategy=MatchStrategy.KEYWORD,
            patterns=["测试", "fake"],
            priority=50,
            confidence_threshold=0.3,
        )
    ]
    required_ports = []
    creates_session = False
    session_data_schema = None

    async def handle(self, message, deps):
        from src.shared.enums import ResponseType
        from src.shared.types import SessionId
        from uuid import uuid4
        return ModeResponse(
            mode_id=self.mode_id,
            session_id=SessionId(value=uuid4()),
            response_type=ResponseType.TEXT_REPLY,
            text_reply="fake response",
        )


class TestModeRegistry:
    def test_register_and_get(self):
        registry = ModeRegistry()
        registry.register(_FakeMode)
        mode = registry.get("test.fake")
        assert mode is not None
        assert mode.mode_id == "test.fake"

    def test_get_unknown_returns_none(self):
        registry = ModeRegistry()
        assert registry.get("nonexistent") is None

    def test_all_modes(self):
        registry = ModeRegistry()
        registry.register(_FakeMode)
        all_modes = registry.all_modes()
        assert "test.fake" in all_modes

    def test_modes_matching_intent(self):
        registry = ModeRegistry()
        registry.register(_FakeMode)
        matches = registry.modes_matching_intent("测试")
        assert len(matches) == 1

    def test_register_as_decorator(self):
        registry = ModeRegistry()

        @registry.register
        class DecoratedMode(ModeProtocol):
            mode_id = "test.decorated"
            display_name = "Decorated"
            description = "Decorated mode"
            intent_patterns = []
            required_ports = []
            creates_session = False
            session_data_schema = None

            async def handle(self, message, deps):
                pass

        assert registry.get("test.decorated") is not None

    def test_global_singleton(self):
        assert mode_registry is not None
        assert isinstance(mode_registry, ModeRegistry)
