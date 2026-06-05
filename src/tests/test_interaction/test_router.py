"""Tests for SessionRouter."""

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from src.interaction.base import (
    IntentPattern,
    ModeProtocol,
    ModeResponse,
    ResponseType,
    UserMessage,
)
from src.interaction.context import ContextResolver
from src.interaction.dependencies import ModeDependencies, PortProvider
from src.interaction.router import SessionRouter
from src.interaction.registry import ModeRegistry
from src.shared.enums import MatchStrategy
from src.shared.types import SessionId


class _KnowledgeMode(ModeProtocol):
    mode_id = "cognitive.knowledge_query"
    display_name = "Knowledge Query"
    description = "Answer knowledge questions"
    intent_patterns = [
        IntentPattern(
            match_strategy=MatchStrategy.KEYWORD,
            patterns=["多少", "温度", "参数"],
            priority=50,
            confidence_threshold=0.3,
        )
    ]
    required_ports = []
    creates_session = False
    session_data_schema = None

    async def handle(self, message, deps):
        return ModeResponse(
            mode_id=self.mode_id,
            session_id=SessionId(value=uuid4()),
            response_type=ResponseType.TEXT_REPLY,
            text_reply="Knowledge answer",
        )


class _MonitorMode(ModeProtocol):
    mode_id = "cognitive.monitor"
    display_name = "Monitor"
    description = "Monitor execution"
    intent_patterns = [
        IntentPattern(
            match_strategy=MatchStrategy.KEYWORD,
            patterns=["进度", "状态", "怎样"],
            priority=50,
            confidence_threshold=0.3,
        )
    ]
    required_ports = []
    creates_session = False
    session_data_schema = None

    async def handle(self, message, deps):
        return ModeResponse(
            mode_id=self.mode_id,
            session_id=SessionId(value=uuid4()),
            response_type=ResponseType.TEXT_REPLY,
            text_reply="Monitor answer",
        )


def _make_router() -> SessionRouter:
    registry = ModeRegistry()
    registry.register(_KnowledgeMode)
    registry.register(_MonitorMode)
    provider = PortProvider()
    resolver = ContextResolver()
    return SessionRouter(registry, provider, resolver)


class TestSessionRouter:
    @pytest.mark.asyncio
    async def test_route_knowledge_query(self):
        router = _make_router()
        msg = UserMessage(
            raw_text="Q345R预热温度多少",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
            intent_label="cognitive.knowledge_query",
        )
        response = await router.route(msg)
        assert response.mode_id == "cognitive.knowledge_query"
        assert response.response_type == ResponseType.TEXT_REPLY

    @pytest.mark.asyncio
    async def test_route_monitor_query(self):
        router = _make_router()
        msg = UserMessage(
            raw_text="当前进度怎样",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
            intent_label="cognitive.monitor",
        )
        response = await router.route(msg)
        assert response.mode_id == "cognitive.monitor"

    @pytest.mark.asyncio
    async def test_route_unknown_returns_error(self):
        router = _make_router()
        msg = UserMessage(
            raw_text="今天天气怎么样",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
            intent_label="unknown",
        )
        response = await router.route(msg)
        assert response.response_type == ResponseType.ERROR

    @pytest.mark.asyncio
    async def test_route_uses_context_resolver(self):
        router = _make_router()
        # Verify context resolver is wired
        assert router._context_resolver is not None
        ctx = router._context_resolver.resolve("zhangsan")
        assert ctx.operator_id == "zhangsan"
