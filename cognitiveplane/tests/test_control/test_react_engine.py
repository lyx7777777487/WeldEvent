"""Tests for ReActEngine and SessionManager."""

import pytest

from cognitiveplane.capability.provider import LLMProvider, LLMRequest, LLMResponse
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


# ===================================================================
# Event callback tests (B2-impl: ReActEngine event emission)
# ===================================================================


class _ScriptedLLM(LLMProvider):
    """LLM that returns a scripted sequence of responses — first tool_call,
    then a final text reply. Used to drive the ReAct loop deterministically."""

    def __init__(self, tool_name: str, tool_args: dict, final_text: str) -> None:
        self._tool_name = tool_name
        self._tool_args = tool_args
        self._final_text = final_text
        self._call_count = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self._call_count += 1
        if self._call_count == 1:
            return LLMResponse(
                content="",
                tool_calls=[{
                    "id": f"call-{self._call_count}",
                    "function": {
                        "name": self._tool_name,
                        "arguments": __import__("json").dumps(self._tool_args),
                    },
                }],
                model_used="scripted",
            )
        return LLMResponse(content=self._final_text, model_used="scripted")

    async def stream(self, request: LLMRequest):
        yield self._final_text

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    def health_check(self) -> bool:
        return True


class _RecordingCallback:
    """Captures every event fired by ReActEngine into a list."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def __call__(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))


def _make_deps_with_llm(llm: LLMProvider) -> CognitiveDependencies:
    deps = CognitiveDependencies()
    deps.capability.llm_provider = llm
    return deps


class TestEventCallback:
    """B2-impl: ReActEngine emits thinking/tool_call/tool_result/final events."""

    @pytest.mark.asyncio
    async def test_emits_thinking_before_each_llm_call(self) -> None:
        llm = _ScriptedLLM("test_tool", {"query": "x"}, "done")
        registry = ToolRegistry()
        registry.register(TestTool())
        cb = _RecordingCallback()
        engine = ReActEngine(
            _make_deps_with_llm(llm),
            tool_registry=registry,
            event_callback=cb,
        )
        await engine.run("any", _make_context())
        thinking_events = [p for t, p in cb.events if t == "thinking"]
        assert len(thinking_events) == 2  # one for tool-call round, one for final round
        assert thinking_events[0]["iteration"] == 1
        assert thinking_events[1]["iteration"] == 2

    @pytest.mark.asyncio
    async def test_emits_tool_call_and_tool_result_pair(self) -> None:
        llm = _ScriptedLLM("test_tool", {"query": "x"}, "final answer")
        registry = ToolRegistry()
        registry.register(TestTool())
        cb = _RecordingCallback()
        engine = ReActEngine(
            _make_deps_with_llm(llm),
            tool_registry=registry,
            event_callback=cb,
        )
        await engine.run("any", _make_context())
        types = [t for t, _ in cb.events]
        assert "tool_call" in types
        assert "tool_result" in types
        call_idx = types.index("tool_call")
        result_idx = types.index("tool_result")
        assert result_idx > call_idx
        call_payload = cb.events[call_idx][1]
        assert call_payload["tool"] == "test_tool"
        assert call_payload["arguments"] == {"query": "x"}
        result_payload = cb.events[result_idx][1]
        assert result_payload["tool"] == "test_tool"
        assert result_payload["result"] == {"output": {"result": "test_output"}}
        assert result_payload["error"] is None
        assert result_payload["error_type"] is None

    @pytest.mark.asyncio
    async def test_emits_final_once_at_end(self) -> None:
        llm = _ScriptedLLM("test_tool", {"query": "x"}, "final answer")
        registry = ToolRegistry()
        registry.register(TestTool())
        cb = _RecordingCallback()
        engine = ReActEngine(
            _make_deps_with_llm(llm),
            tool_registry=registry,
            event_callback=cb,
        )
        response = await engine.run("any", _make_context())
        final_events = [p for t, p in cb.events if t == "final"]
        assert len(final_events) == 1
        assert final_events[0]["reply"] == response.text_reply
        assert final_events[0]["tier"] == "react_function_calling"
        assert final_events[0]["error"] is None
        assert cb.events[-1][0] == "final"

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_break_loop(self) -> None:
        llm = _ScriptedLLM("test_tool", {"query": "x"}, "final answer")
        registry = ToolRegistry()
        registry.register(TestTool())

        async def broken_callback(event_type: str, payload: dict) -> None:
            raise RuntimeError("callback crashed")

        engine = ReActEngine(
            _make_deps_with_llm(llm),
            tool_registry=registry,
            event_callback=broken_callback,
        )
        response = await engine.run("any", _make_context())
        assert response.error is None
        assert response.text_reply == "final answer"
        assert response.tools_used == ["test_tool"]

    @pytest.mark.asyncio
    async def test_no_callback_works_as_before(self) -> None:
        """Regression guard: omitting event_callback must not change behavior."""
        llm = _ScriptedLLM("test_tool", {"query": "x"}, "final answer")
        registry = ToolRegistry()
        registry.register(TestTool())
        engine = ReActEngine(
            _make_deps_with_llm(llm),
            tool_registry=registry,
        )
        response = await engine.run("any", _make_context())
        assert response.tier_used == InteractionTier.REACT_FUNCTION_CALLING
        assert response.tools_used == ["test_tool"]
        assert response.text_reply == "final answer"
