"""Tests for ReActEngine and SessionManager."""

import pytest

from cognitiveplane.control.deps import CognitiveDependencies
from cognitiveplane.control.react import ReActEngine, InteractionTier, InteractionResponse
from cognitiveplane.control.tool_registry import ToolRegistry
from cognitiveplane.control.tools import BrainTool, ToolResult
from cognitiveplane.control.hooks import SafetyHook, PolicyHook
from cognitiveplane.governance.tool_policy import ToolPolicy
from cognitiveplane.interaction.session import SessionManager
from cognitiveplane.shared.dto_context import ContextSnapshot
from cognitiveplane.shared.enums import EventType, NoveltyLevel
from cognitiveplane.shared.types import CaseId
from datetime import datetime, timezone


def _make_context() -> ContextSnapshot:
    return ContextSnapshot(
        case_id=CaseId(value="test-case"),
        event_type=EventType.WORKFLOW_ENTERED,
        workflow_state={},
        case_data={},
        measurements=[],
        memory_match_confidence=0.5,
        knowledge_coverage=0.5,
        event_novelty=NoveltyLevel.KNOWN,
        validation_critical_count=0,
        timestamp=datetime.now(timezone.utc),
    )


class TestSessionManager:
    def test_create_session(self):
        mgr = SessionManager()
        session = mgr.create_session("operator-001")
        assert session.operator_id == "operator-001"
        assert session.session_id is not None

    def test_get_session(self):
        mgr = SessionManager()
        session = mgr.create_session("op-1")
        found = mgr.get_session("op-1", str(session.session_id.value))
        assert found is session

    def test_operator_isolation(self):
        mgr = SessionManager()
        s1 = mgr.create_session("op-1")
        s2 = mgr.create_session("op-2")
        assert mgr.get_session("op-1", str(s2.session_id.value)) is None
        assert mgr.get_session("op-2", str(s1.session_id.value)) is None

    def test_close_session(self):
        mgr = SessionManager()
        session = mgr.create_session("op-1")
        result = mgr.close_session("op-1", str(session.session_id.value))
        assert result is True
        assert mgr.get_session("op-1", str(session.session_id.value)) is None

    def test_get_active_session(self):
        mgr = SessionManager()
        s1 = mgr.create_session("op-1")
        s2 = mgr.create_session("op-1")
        active = mgr.get_active_session("op-1")
        assert active is s2  # Most recently created


class TestReActEngine:
    def test_select_tier_no_llm(self):
        deps = CognitiveDependencies()
        engine = ReActEngine(deps)
        assert engine._select_tier() == InteractionTier.EMBEDDING_RULES

    @pytest.mark.asyncio
    async def test_embedding_rules_tier(self):
        deps = CognitiveDependencies()
        registry = ToolRegistry()
        registry.register(TestTool())
        engine = ReActEngine(deps, tool_registry=registry)
        context = _make_context()
        response = await engine.run("查询标准参数", context)
        assert response.tier_used == InteractionTier.EMBEDDING_RULES

    @pytest.mark.asyncio
    async def test_unknown_intent(self):
        deps = CognitiveDependencies()
        engine = ReActEngine(deps)
        context = _make_context()
        response = await engine.run("hello world xyz", context)
        assert response.tier_used == InteractionTier.EMBEDDING_RULES


class TestTool(BrainTool):
    """Test tool for unit tests."""

    @property
    def name(self) -> str:
        return "test_tool"

    @property
    def description(self) -> str:
        return "A test tool"

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {"query": {"type": "string"}}}

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(output={"result": "test_output"})
