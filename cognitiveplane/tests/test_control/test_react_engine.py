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
        # Captured user messages for multimodal-degradation assertions.
        self.captured_user_messages: list = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self._call_count += 1
        # First call: capture the user message (first message with role=user).
        if self._call_count == 1:
            for m in request.messages:
                if m.get("role") == "user":
                    self.captured_user_messages.append(m.get("content"))
                    break
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


class _VisionScriptedLLM(_ScriptedLLM):
    """Scripted LLM that advertises supports_vision=True (multimodal primary)."""

    @property
    def supports_vision(self) -> bool:
        return True


class TestMultimodalDegradation:
    """Plan §2.3 dual-track + line 3166: ReActEngine accepts multimodal
    content and adapts per LLM capability.

    - Text-only primary (DeepSeek): image_url parts stripped, text preserved.
    - Multimodal primary: full content_parts forwarded unchanged.
    """

    @staticmethod
    def _multimodal_input() -> list[dict]:
        return [
            {"type": "text", "text": "分析这张焊缝图"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BBB"}},
        ]

    @pytest.mark.asyncio
    async def test_text_only_llm_strips_image_url_parts(self) -> None:
        """DeepSeek-style LLM: user_input list is degraded to plain text."""
        llm = _ScriptedLLM("test_tool", {"query": "x"}, "done")
        engine = ReActEngine(_make_deps_with_llm(llm), tool_registry=ToolRegistry())
        await engine.run(self._multimodal_input(), _make_context())
        captured = llm.captured_user_messages[0]
        assert isinstance(captured, str), "text-only LLM should receive str, not list"
        assert "分析这张焊缝图" in captured
        assert "data:image" not in captured, "image_url parts must be filtered"

    @pytest.mark.asyncio
    async def test_vision_llm_keeps_multimodal_content(self) -> None:
        """Multimodal primary: content_parts forwarded unchanged."""
        llm = _VisionScriptedLLM("test_tool", {"query": "x"}, "done")
        engine = ReActEngine(_make_deps_with_llm(llm), tool_registry=ToolRegistry())
        await engine.run(self._multimodal_input(), _make_context())
        captured = llm.captured_user_messages[0]
        assert isinstance(captured, list), "vision LLM should receive list content"
        assert len(captured) == 3
        assert captured[0]["type"] == "text"
        assert captured[1]["type"] == "image_url"
        assert captured[2]["type"] == "image_url"

    @pytest.mark.asyncio
    async def test_str_input_passes_through_unchanged(self) -> None:
        """str input: no adaptation needed, passes through as str for both LLM kinds."""
        llm = _ScriptedLLM("test_tool", {"query": "x"}, "done")
        engine = ReActEngine(_make_deps_with_llm(llm), tool_registry=ToolRegistry())
        await engine.run("plain text", _make_context())
        assert llm.captured_user_messages[0] == "plain text"

    def test_coerce_text_extracts_text_parts_only(self) -> None:
        """_coerce_text: image_url parts contribute no text; text parts joined by \\n."""
        result = ReActEngine._coerce_text([
            {"type": "text", "text": "first"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
            {"type": "text", "text": "second"},
        ])
        assert result == "first\nsecond"


class _AdjustParamScriptedLLM(LLMProvider):
    """LLM that calls adjust_parameter without a reason on round 1, then
    produces a final text reply on round 2. Used to test Hook interception."""

    def __init__(self, final_text: str = "已取消参数调整") -> None:
        self._final_text = final_text
        self._call_count = 0
        self.received_tool_messages: list[str] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self._call_count += 1
        # Capture any tool-role messages (rejection feedback from prior round).
        for m in request.messages:
            if m.get("role") == "tool":
                self.received_tool_messages.append(m.get("content", ""))
        if self._call_count == 1:
            return LLMResponse(
                content="",
                tool_calls=[{
                    "id": f"call-{self._call_count}",
                    "function": {
                        "name": "adjust_parameter",
                        "arguments": __import__("json").dumps({"parameter": "current", "value": "200A"}),
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


class TestHookInterception:
    """§11.4.1 Hook interception test: phase 2 calls adjust_parameter without
    a reason → PolicyHook DENY → rejection fed back to LLM as tool message.

    Verifies:
      1. Hook denies before tool executes (tools_used excludes adjust_parameter).
      2. Rejection reason is fed back to LLM via role=tool message.
      3. Engine emits tool_call event with rejected=True.
      4. first-DENY-wins: when multiple hooks are registered, the first DENY
         short-circuits the rest.
    """

    @pytest.mark.asyncio
    async def test_policy_hook_denies_adjust_parameter_without_reason(self) -> None:
        from cognitiveplane.control.tools import BrainTool, ToolResult

        class _AdjustTool(BrainTool):
            @property
            def name(self) -> str: return "adjust_parameter"
            @property
            def description(self) -> str: return "test"
            @property
            def parameters_schema(self) -> dict: return {"type": "object", "properties": {}}
            async def execute(self, **kwargs) -> ToolResult:
                return ToolResult(output={"applied": True})

        llm = _AdjustParamScriptedLLM()
        registry = ToolRegistry()
        registry.register(_AdjustTool())
        policy = ToolPolicy()
        hooks = [SafetyHook(), PolicyHook(policy)]
        engine = ReActEngine(
            _make_deps_with_llm(llm),
            tool_registry=registry,
            hooks=hooks,
        )
        response = await engine.run("把电流调到200A", _make_context())

        # Tool was NOT executed (hook denied before execute).
        assert response.tools_used == [], "denied tool must not appear in tools_used"
        # LLM received rejection feedback as a tool-role message.
        assert len(llm.received_tool_messages) >= 1, "LLM should have received tool rejection"
        rejection = llm.received_tool_messages[0]
        assert "rejected" in rejection or "reason" in rejection, \
            f"rejection message missing rejection marker: {rejection}"

    @pytest.mark.asyncio
    async def test_hook_emits_rejected_tool_call_event(self) -> None:
        """Engine emits tool_call event with rejected=True when a hook denies."""
        from cognitiveplane.control.tools import BrainTool, ToolResult

        class _AdjustTool(BrainTool):
            @property
            def name(self) -> str: return "adjust_parameter"
            @property
            def description(self) -> str: return "test"
            @property
            def parameters_schema(self) -> dict: return {"type": "object", "properties": {}}
            async def execute(self, **kwargs) -> ToolResult:
                return ToolResult(output={"applied": True})

        llm = _AdjustParamScriptedLLM()
        registry = ToolRegistry()
        registry.register(_AdjustTool())
        policy = ToolPolicy()
        hooks = [PolicyHook(policy)]
        engine = ReActEngine(
            _make_deps_with_llm(llm),
            tool_registry=registry,
            hooks=hooks,
        )
        callback = _RecordingCallback()
        engine._event_callback = callback
        await engine.run("把电流调到200A", _make_context())

        rejected_events = [
            (t, p) for t, p in callback.events
            if t == "tool_call" and p.get("rejected") is True
        ]
        assert len(rejected_events) == 1, f"expected 1 rejected event, got {rejected_events}"
        assert rejected_events[0][1]["tool"] == "adjust_parameter"
        assert "reason" in rejected_events[0][1]

    @pytest.mark.asyncio
    async def test_first_deny_wins_short_circuits(self) -> None:
        """When multiple hooks would deny, the first DENY wins — subsequent
        hooks are not invoked."""
        from cognitiveplane.control.hooks import BeforeToolHook, HookDecision, HookResult

        call_log: list[str] = []

        class _FirstDenyHook(BeforeToolHook):
            async def before_execute(self, tool_name, arguments, context):
                call_log.append(f"first:{tool_name}")
                return HookResult(decision=HookDecision.DENY, reason="first-deny")

        class _SecondHook(BeforeToolHook):
            async def before_execute(self, tool_name, arguments, context):
                call_log.append(f"second:{tool_name}")
                return HookResult(decision=HookDecision.ALLOW)

        llm = _AdjustParamScriptedLLM()
        engine = ReActEngine(
            _make_deps_with_llm(llm),
            tool_registry=ToolRegistry(),
            hooks=[_FirstDenyHook(), _SecondHook()],
        )
        await engine.run("test", _make_context())

        assert call_log == ["first:adjust_parameter"], \
            f"second hook must not run after first DENY; got {call_log}"


class TestEventLogReplay:
    """§11.4.1 EventLog replay test: record ReAct event stream into EventLog,
    then replay to verify the sequence is reconstructable and deterministic.

    Plan §5 Append-Only EventLog: per-run audit trail supports time-travel
    debugging. Replaying the log must yield the same event sequence and
    reconstruct the same DecisionPipelineView.
    """

    @pytest.mark.asyncio
    async def test_event_log_records_and_replays_react_session(self) -> None:
        from cognitiveplane.control.event_log import (
            EventLog, BrainEventType, DecisionPipelineView,
        )
        from cognitiveplane.shared.types import CaseId

        case_id = CaseId(value="replay-test")
        event_log = EventLog(case_id)

        async def recorder(event_type: str, payload: dict) -> None:
            """Bridge ReAct event_callback → EventLog.append."""
            source = "react_engine"
            if event_type == "thinking":
                event_log.emit(BrainEventType.STATE_TRANSITION, source, payload)
            elif event_type == "tool_call":
                event_log.emit_tool_call(
                    source=source,
                    tool_name=payload.get("tool", ""),
                    arguments=payload.get("arguments", {}),
                )
            elif event_type == "tool_result":
                event_log.emit(
                    BrainEventType.TOOL_RESULT, source,
                    {"tool": payload.get("tool"), "result": payload.get("result")},
                )
            elif event_type == "final":
                event_log.emit(BrainEventType.DECISION, source, payload)

        llm = _ScriptedLLM("test_tool", {"query": "x"}, "final answer")
        registry = ToolRegistry()
        registry.register(TestTool())
        engine = ReActEngine(
            _make_deps_with_llm(llm),
            tool_registry=registry,
            event_callback=recorder,
        )
        response = await engine.run("any", _make_context())
        assert response.text_reply == "final answer"

        # ── Replay: reconstruct view from event log ──
        view: DecisionPipelineView = event_log.project_view()
        # One thinking per iteration; one tool_call; one tool_result; one final.
        assert len(view.state_transitions) >= 1, "thinking events missing"
        assert len(view.tool_calls) == 1, f"expected 1 tool_call, got {len(view.tool_calls)}"
        assert view.tool_calls[0].get("tool_name") == "test_tool"
        assert len(view.tool_results) == 1, "tool_result event missing"
        assert len(view.decisions) == 1, "final decision event missing"
        assert view.decisions[0].get("reply") == "final answer"

        # ── Determinism: event count stable across re-projection ──
        view_again = event_log.project_view()
        assert len(view_again.tool_calls) == len(view.tool_calls)
        assert len(view_again.tool_results) == len(view.tool_results)

    @pytest.mark.asyncio
    async def test_event_log_pairs_tool_call_with_result(self) -> None:
        """ToolCallEvent ↔ ToolResultEvent pairing via action_id back-pointer."""
        from cognitiveplane.control.event_log import (
            EventLog, ToolCallEvent,
        )
        from cognitiveplane.shared.types import CaseId

        case_id = CaseId(value="pairing-test")
        event_log = EventLog(case_id)
        # Track last ToolCallEvent to pair the next ToolResultEvent with.
        last_tool_call: dict[str, ToolCallEvent | None] = {"event": None}

        async def recorder(event_type: str, payload: dict) -> None:
            if event_type == "tool_call":
                tc = event_log.emit_tool_call(
                    source="react_engine",
                    tool_name=payload.get("tool", ""),
                    arguments=payload.get("arguments", {}),
                )
                last_tool_call["event"] = tc
            elif event_type == "tool_result":
                tc = last_tool_call["event"]
                if tc is not None:
                    event_log.emit_tool_result(
                        source="react_engine",
                        action_id=tc.event_id,
                        tool_call_id=tc.tool_call_id,
                        success=not bool(payload.get("error")),
                        result=payload.get("result"),
                        error=payload.get("error"),
                    )

        llm = _ScriptedLLM("test_tool", {"query": "x"}, "done")
        registry = ToolRegistry()
        registry.register(TestTool())
        engine = ReActEngine(
            _make_deps_with_llm(llm),
            tool_registry=registry,
            event_callback=recorder,
        )
        await engine.run("any", _make_context())

        tool_calls = [e for e in event_log if isinstance(e, ToolCallEvent)]
        assert len(tool_calls) == 1
        paired = event_log.find_paired_result(tool_calls[0])
        assert paired is not None, "ToolCallEvent must pair with ToolResultEvent"
        assert paired.tool_call_id == tool_calls[0].tool_call_id


class TestContrastStability:
    """§11.4.1 Contrast test: same input N≥5 times → deterministic output.

    Plan §11.4.1 测试方法论 requires N≥5 contrast runs to verify ReAct loop
    stability. With a scripted (deterministic) LLM, every run must produce
    the same text_reply, tools_used, and tier_used.
    """

    @pytest.mark.asyncio
    async def test_deterministic_output_across_n_runs(self) -> None:
        N = 5
        outputs: list[tuple[str, tuple[str, ...], str]] = []
        for _ in range(N):
            llm = _ScriptedLLM("test_tool", {"query": "x"}, "final answer")
            registry = ToolRegistry()
            registry.register(TestTool())
            engine = ReActEngine(_make_deps_with_llm(llm), tool_registry=registry)
            response = await engine.run("same input", _make_context())
            outputs.append((
                response.text_reply,
                tuple(response.tools_used),
                response.tier_used.value,
            ))
        # All N runs produced identical output.
        assert len(set(outputs)) == 1, (
            f"contrast test failed — {N} runs produced {len(set(outputs))} distinct outputs: {outputs}"
        )
        reply, tools, tier = outputs[0]
        assert reply == "final answer"
        assert tools == ("test_tool",)
        assert tier == "react_function_calling"
