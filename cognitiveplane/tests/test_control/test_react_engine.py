"""Tests for ReActEngine and SessionManager."""

import pytest

from cognitiveplane.capability.provider import LLMProvider, LLMRequest, LLMResponse
from cognitiveplane.control.deps import CognitiveDependencies
from cognitiveplane.control.react import ReActEngine, InteractionTier, InteractionResponse
from cognitiveplane.control.skills import Skill, SkillRegistry
from cognitiveplane.control.registry.tool_registry import ToolRegistry
from cognitiveplane.control.tools import BrainTool, ToolResult
from cognitiveplane.control.hooks import SafetyHook, PolicyHook
from cognitiveplane.governance.tool_policy import ToolPolicy
from cognitiveplane.interaction.session import SessionManager
from cognitiveplane.shared.dto.context import ContextSnapshot
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
        # Skill classification requests — return null so keyword fallback is used.
        # This prevents the classification call from consuming the scripted call sequence.
        sys_msg = next((m.get("content", "") for m in request.messages if m.get("role") == "system"), "")
        if "意图分类器" in sys_msg:
            return LLMResponse(content='{"skill": null}', model_used="classification")
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


class _EscalateScriptedLLM(LLMProvider):
    """LLM that calls escalate without a reason on round 1, then
    produces a final text reply on round 2. Used to test Hook interception."""

    def __init__(self, final_text: str = "已取消升级") -> None:
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
            # Schema-valid args (correct field names, valid types) but NO 'reason' —
            # this passes schema pre-validation (test tools use empty schema) and lets
            # PolicyHook deny based on require_reason=True policy rule.
            return LLMResponse(
                content="",
                tool_calls=[{
                    "id": f"call-{self._call_count}",
                    "function": {
                        "name": "escalate",
                        "arguments": __import__("json").dumps({
                            "target": "human_review",
                            "summary": "需要人工介入",
                        }),
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
    """§11.4.1 Hook interception test: phase 2 calls escalate without
    a reason → PolicyHook DENY → rejection fed back to LLM as tool message.

    Verifies:
      1. Hook denies before tool executes (tools_used excludes escalate).
      2. Rejection reason is fed back to LLM via role=tool message.
      3. Engine emits tool_call event with rejected=True.
      4. first-DENY-wins: when multiple hooks are registered, the first DENY
         short-circuits the rest.
    """

    @pytest.mark.asyncio
    async def test_policy_hook_denies_escalate_without_reason(self) -> None:
        from cognitiveplane.control.tools import BrainTool, ToolResult

        class _EscalateTool(BrainTool):
            @property
            def name(self) -> str: return "escalate"
            @property
            def description(self) -> str: return "test"
            @property
            def parameters_schema(self) -> dict: return {"type": "object", "properties": {}}
            async def execute(self, **kwargs) -> ToolResult:
                return ToolResult(output={"applied": True})

        llm = _EscalateScriptedLLM()
        registry = ToolRegistry()
        registry.register(_EscalateTool())
        policy = ToolPolicy()
        hooks = [SafetyHook(), PolicyHook(policy)]
        engine = ReActEngine(
            _make_deps_with_llm(llm),
            tool_registry=registry,
            hooks=hooks,
        )
        response = await engine.run("请升级到人工审核", _make_context())

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

        class _EscalateTool(BrainTool):
            @property
            def name(self) -> str: return "escalate"
            @property
            def description(self) -> str: return "test"
            @property
            def parameters_schema(self) -> dict: return {"type": "object", "properties": {}}
            async def execute(self, **kwargs) -> ToolResult:
                return ToolResult(output={"applied": True})

        llm = _EscalateScriptedLLM()
        registry = ToolRegistry()
        registry.register(_EscalateTool())
        policy = ToolPolicy()
        hooks = [PolicyHook(policy)]
        engine = ReActEngine(
            _make_deps_with_llm(llm),
            tool_registry=registry,
            hooks=hooks,
        )
        callback = _RecordingCallback()
        engine._event_callback = callback
        await engine.run("请升级到人工审核", _make_context())

        rejected_events = [
            (t, p) for t, p in callback.events
            if t == "tool_call" and p.get("rejected") is True
        ]
        assert len(rejected_events) == 1, f"expected 1 rejected event, got {rejected_events}"
        assert rejected_events[0][1]["tool"] == "escalate"
        assert "reason" in rejected_events[0][1]

    @pytest.mark.asyncio
    async def test_first_deny_wins_short_circuits(self) -> None:
        """When multiple hooks would deny, the first DENY wins — subsequent
        hooks are not invoked."""
        from cognitiveplane.control.hooks import BeforeToolHook, HookDecision, HookResult

        call_log: list[str] = []

        class _FirstDenyHook(BeforeToolHook):
            async def before_execute(self, tool_name, arguments, context, session_id="default"):
                call_log.append(f"first:{tool_name}")
                return HookResult(decision=HookDecision.DENY, reason="first-deny")

        class _SecondHook(BeforeToolHook):
            async def before_execute(self, tool_name, arguments, context, session_id="default"):
                call_log.append(f"second:{tool_name}")
                return HookResult(decision=HookDecision.ALLOW)

        llm = _ScriptedLLM("escalate", {"reason": "test"}, "done")

        class _StubEscalateTool(BrainTool):
            @property
            def name(self) -> str: return "escalate"
            @property
            def description(self) -> str: return "test"
            @property
            def parameters_schema(self) -> dict: return {"type": "object", "properties": {}}
            async def execute(self, **kwargs) -> ToolResult:
                return ToolResult(output={"applied": True})

        registry = ToolRegistry()
        registry.register(_StubEscalateTool())
        engine = ReActEngine(
            _make_deps_with_llm(llm),
            tool_registry=registry,
            hooks=[_FirstDenyHook(), _SecondHook()],
        )
        await engine.run("test", _make_context())

        assert call_log == ["first:escalate"], \
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


# ===================================================================
# P0-2: §5.1 line 894-902 Worldview injection tests
# ===================================================================


def _make_context_with_case_data() -> ContextSnapshot:
    """Context with non-empty case_data — exercises CASE_BRIEF injection."""
    return ContextSnapshot(
        case_id=CaseId(value="CASE-2026-001"),
        event_type=EventType.WORKFLOW_ENTERED,
        workflow_state={"stage": "welding", "operator": "op-001"},
        case_data={"material": "Q345R", "thickness": 12.5},
        measurements=[],
        memory_match_confidence=0.5,
        knowledge_coverage=0.5,
        event_novelty=NoveltyLevel.KNOWN,
        validation_critical_count=0,
        timestamp=datetime.now(timezone.utc),
    )


class _CapturingLLM(LLMProvider):
    """LLM that captures the system prompt for inspection, returns immediate final reply."""

    def __init__(self) -> None:
        self.captured_system: str | None = None

    async def complete(self, request: LLMRequest) -> LLMResponse:
        # Skill classification requests — return null so keyword fallback is used.
        sys_msg = next((m.get("content", "") for m in request.messages if m.get("role") == "system"), "")
        if "意图分类器" in sys_msg:
            return LLMResponse(content='{"skill": null}', model_used="classification")
        for m in request.messages:
            if m.get("role") == "system":
                self.captured_system = m.get("content", "")
                break
        return LLMResponse(content="ok", model_used="capturing")

    async def stream(self, request: LLMRequest):
        yield "ok"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    def health_check(self) -> bool:
        return True


class _StubMemorySearch:
    """Stub MemorySearchPort that returns scripted hits."""

    def __init__(self, hits: list) -> None:
        self._hits = hits

    async def search(self, input_data) -> object:
        from cognitiveplane.shared.ports.memory import MemorySearchOutput
        return MemorySearchOutput(results=self._hits)


class TestWorldviewInjection:
    """P0-2: §5.1 line 894-902 system prompt auto-injection."""

    @pytest.mark.asyncio
    async def test_system_prompt_injects_case_brief(self) -> None:
        """CASE_BRIEF from ContextSnapshot is injected into system prompt."""
        llm = _CapturingLLM()
        engine = ReActEngine(_make_deps_with_llm(llm))
        await engine.run("hi", _make_context_with_case_data())
        assert llm.captured_system is not None
        assert "CASE-2026-001" in llm.captured_system
        assert "Q345R" in llm.captured_system

    @pytest.mark.asyncio
    async def test_system_prompt_injects_memory_search(self) -> None:
        """Memory.search hits are injected when MemoryDeps.search is wired."""
        from cognitiveplane.shared.dto.memory import MemorySearchResult, MemoryContent
        from cognitiveplane.shared.enums import MemoryType, PromotionStatus
        from cognitiveplane.shared.types import MemoryId
        from datetime import datetime, timezone
        import uuid

        hit = MemorySearchResult(
            memory_id=MemoryId(value=uuid.uuid4()),
            content=MemoryContent(
                summary="过去对 Q345R 角焊的修正：气孔判为 II 级",
                details={"defect": "气孔", "grade": "II", "material": "Q345R"},
                feature_vector=[0.1, 0.2, 0.3],
            ),
            similarity_score=0.87,
            promotion_status=PromotionStatus.PROMOTED,
            created_at=datetime.now(timezone.utc),
        )
        llm = _CapturingLLM()
        deps = _make_deps_with_llm(llm)
        deps.memory.search = _StubMemorySearch([hit])  # type: ignore[assignment]
        engine = ReActEngine(deps)
        await engine.run("hi", _make_context_with_case_data())
        assert llm.captured_system is not None
        assert "Memory.search" in llm.captured_system
        assert "Q345R 角焊" in llm.captured_system  # summary content
        assert "0.87" in llm.captured_system  # similarity score

    @pytest.mark.asyncio
    async def test_system_prompt_skips_missing_worldview_files(self) -> None:
        """WELDEVENT.md / OPERATOR.md don't exist in test env — system prompt
        builds without those sections. CASE_BRIEF still includes event_type
        (always set) but skips case_id/workflow/case_data when empty."""
        llm = _CapturingLLM()
        engine = ReActEngine(_make_deps_with_llm(llm))
        empty_context = ContextSnapshot(
            case_id=CaseId(value="unknown"),
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
        await engine.run("hi", empty_context)
        assert llm.captured_system is not None
        # Tools + rules section always present
        assert "可用工具" in llm.captured_system
        # WELDEVENT.md / OPERATOR.md files don't exist in test env — not in prompt
        assert "项目规约" not in llm.captured_system
        assert "操作员偏好" not in llm.captured_system
        # Memory.search not wired — not in prompt
        assert "相关历史" not in llm.captured_system
        # CASE_BRIEF still present (event_type always has value)
        assert "Event Type" in llm.captured_system
        # But case_id="unknown" is filtered out
        assert "Case ID" not in llm.captured_system

    @pytest.mark.asyncio
    async def test_memory_search_failure_does_not_break_react(self) -> None:
        """Memory.search raising must not break the ReAct loop (§5.1: worldview is optional)."""

        class _FailingMemorySearch:
            async def search(self, input_data):
                raise RuntimeError("Memory backend down")

        llm = _CapturingLLM()
        deps = _make_deps_with_llm(llm)
        deps.memory.search = _FailingMemorySearch()  # type: ignore[assignment]
        engine = ReActEngine(deps)
        response = await engine.run("hi", _make_context())
        # Engine did not crash; still produced a reply
        assert response.text_reply == "ok"

    @pytest.mark.asyncio
    async def test_worldview_injection_recorded_in_eventlog(self) -> None:
        """§0.1 规则 1: 架构替 LLM 做的 worldview 注入必须记进 EventLog."""
        from cognitiveplane.control.event_log import EventLog, BrainEventType
        from cognitiveplane.shared.types import CaseId

        event_log = EventLog(case_id=CaseId(value="test-eventlog"))
        llm = _CapturingLLM()
        engine = ReActEngine(_make_deps_with_llm(llm), event_log=event_log)
        await engine.run("hi", _make_context_with_case_data())

        # Find the worldview_injected event
        injected_events = [
            e for e in event_log._events
            if e.event_type == BrainEventType.STATE_TRANSITION
            and e.data.get("phase") == "worldview_injected"
        ]
        assert len(injected_events) == 1, (
            f"Expected 1 worldview_injected event, got {len(injected_events)}"
        )
        meta = injected_events[0].data
        # CASE_BRIEF always injected when context has case_data
        assert "case_brief" in meta["sections"]
        assert injected_events[0].source == "react_engine"

    @pytest.mark.asyncio
    async def test_worldview_injection_records_memory_hit_ids(self) -> None:
        """§0.1 规则 1: Memory hit ids 记进 EventLog, LLM 下一轮可查."""
        from cognitiveplane.control.event_log import EventLog, BrainEventType
        from cognitiveplane.shared.dto.memory import MemorySearchResult, MemoryContent
        from cognitiveplane.shared.enums import MemoryType, PromotionStatus
        from cognitiveplane.shared.types import MemoryId
        from datetime import datetime, timezone
        import uuid

        hit_id = uuid.uuid4()
        hit = MemorySearchResult(
            memory_id=MemoryId(value=hit_id),
            content=MemoryContent(
                summary="Q345R 角焊历史修正",
                details={"defect": "气孔"},
                feature_vector=[0.1],
            ),
            similarity_score=0.87,
            promotion_status=PromotionStatus.PROMOTED,
            created_at=datetime.now(timezone.utc),
        )
        event_log = EventLog(case_id=CaseId(value="test-eventlog"))
        llm = _CapturingLLM()
        deps = _make_deps_with_llm(llm)
        deps.memory.search = _StubMemorySearch([hit])  # type: ignore[assignment]
        engine = ReActEngine(deps, event_log=event_log)
        await engine.run("hi", _make_context_with_case_data())

        injected_events = [
            e for e in event_log._events
            if e.data.get("phase") == "worldview_injected"
        ]
        assert len(injected_events) == 1
        meta = injected_events[0].data
        assert "memory_search" in meta["sections"]
        assert meta["memory_hit_count"] == 1
        assert str(hit_id) in meta["memory_hit_ids"]

    @pytest.mark.asyncio
    async def test_no_eventlog_no_crash(self) -> None:
        """When event_log is None (default), worldview injection still works, just no logging."""
        llm = _CapturingLLM()
        engine = ReActEngine(_make_deps_with_llm(llm))  # no event_log
        response = await engine.run("hi", _make_context_with_case_data())
        assert response.text_reply == "ok"
        assert llm.captured_system is not None
        assert "世界观" in llm.captured_system  # injection still happened


# ===================================================================
# P0-3: §3.6 line 601 Tier-A schema retry tests
# ===================================================================


class _ScriptedMalformedLLM(LLMProvider):
    """LLM that returns malformed JSON arguments on first call, valid on retry,
    then a final text reply after the successful tool execution.

    retry_count: how many times to emit malformed JSON before returning valid args.
    """

    def __init__(self, tool_name: str, malformed: str, valid_args: dict, final_text: str, retry_count: int = 1) -> None:
        self._tool_name = tool_name
        self._malformed = malformed
        self._valid_args = valid_args
        self._final_text = final_text
        self._retry_budget = retry_count
        self._call_count = 0
        self._emitted_valid = False

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self._call_count += 1
        import json
        # Phase 1: emit malformed args until retry budget exhausted
        if self._retry_budget > 0:
            self._retry_budget -= 1
            return LLMResponse(
                content="",
                tool_calls=[{
                    "id": f"call-{self._call_count}",
                    "function": {
                        "name": self._tool_name,
                        "arguments": self._malformed,
                    },
                }],
                model_used="scripted",
            )
        # Phase 2: emit valid args once
        if not self._emitted_valid:
            self._emitted_valid = True
            return LLMResponse(
                content="",
                tool_calls=[{
                    "id": f"call-{self._call_count}",
                    "function": {
                        "name": self._tool_name,
                        "arguments": json.dumps(self._valid_args),
                    },
                }],
                model_used="scripted",
            )
        # Phase 3: final text reply (no more tool calls)
        return LLMResponse(content=self._final_text, model_used="scripted")

    async def stream(self, request: LLMRequest):
        yield ""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    def health_check(self) -> bool:
        return True


class _AlwaysMalformedLLM(LLMProvider):
    """LLM that always returns malformed JSON — used to test exhausted retries."""

    def __init__(self, tool_name: str) -> None:
        self._tool_name = tool_name
        self._call_count = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self._call_count += 1
        # After 5 calls (enough for retry+failure+new-attempt+retry+failure), return text
        if self._call_count >= 6:
            return LLMResponse(content="I give up", model_used="scripted")
        return LLMResponse(
            content="",
            tool_calls=[{
                "id": f"call-{self._call_count}",
                "function": {
                    "name": self._tool_name,
                    "arguments": "{not valid json",  # always malformed
                },
            }],
            model_used="scripted",
        )

    async def stream(self, request: LLMRequest):
        yield ""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    def health_check(self) -> bool:
        return True


class _TierATestTool(BrainTool):
    """A test tool registered under a Tier-A name (web_search)."""

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Test Tier-A tool"

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {"query": {"type": "string"}}}

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(output={"hits": ["result"]})


class _TierBTestTool(BrainTool):
    """A test tool registered under a Tier-B name (escalate)."""

    @property
    def name(self) -> str:
        return "escalate"

    @property
    def description(self) -> str:
        return "Test Tier-B tool"

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {"param": {"type": "string"}}}

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(output={"adjusted": True})


class TestTierARetry:
    """P0-3: §3.6 line 601 Tier-A schema failure → retry 1 time."""

    @pytest.mark.asyncio
    async def test_tier_a_json_parse_failure_retries_once(self) -> None:
        """Tier-A tool with malformed JSON args → LLM gets error, retries, succeeds."""
        llm = _ScriptedMalformedLLM(
            tool_name="web_search",
            malformed="{not valid json",
            valid_args={"query": "Q345R"},
            final_text="found it",
            retry_count=1,
        )
        registry = ToolRegistry()
        registry.register(_TierATestTool())
        engine = ReActEngine(_make_deps_with_llm(llm), tool_registry=registry)
        response = await engine.run("search", _make_context())
        # Tool was eventually called after retry
        assert "web_search" in response.tools_used
        assert response.text_reply == "found it"

    @pytest.mark.asyncio
    async def test_tier_a_exhausted_retries_emits_failure(self) -> None:
        """Tier-A tool with persistent malformed JSON → after 1 retry, ToolFailureObservation."""
        llm = _AlwaysMalformedLLM(tool_name="web_search")
        registry = ToolRegistry()
        registry.register(_TierATestTool())
        engine = ReActEngine(_make_deps_with_llm(llm), tool_registry=registry)
        response = await engine.run("search", _make_context())
        # Tool was added to tools_used (fatal failure path tracks it)
        assert "web_search" in response.tools_used
        # Engine didn't crash, produced some reply
        assert response.text_reply is not None

    @pytest.mark.asyncio
    async def test_tier_b_json_parse_failure_no_retry(self) -> None:
        """Tier-B tool with malformed JSON → immediate rejection, no retry."""
        llm = _ScriptedMalformedLLM(
            tool_name="escalate",
            malformed="{not valid json",
            valid_args={"param": "current"},
            final_text="done",
            retry_count=1,  # Would retry if it were Tier-A
        )
        registry = ToolRegistry()
        registry.register(_TierBTestTool())
        engine = ReActEngine(_make_deps_with_llm(llm), tool_registry=registry)
        response = await engine.run("adjust", _make_context())
        # Tier-B immediate rejection — tool_call counted, but real execute was never called
        # (the rejection still adds it to tools_used per the fatal-failure path)
        assert "escalate" in response.tools_used
        # LLM did get a chance to retry (its scripted retry), but Tier-B rejects
        # both times — so tool was never actually executed successfully
        assert response.text_reply == "done"


class _FCDisabledLLM(_ScriptedLLM):
    """LLM that supports JSON mode but NOT function calling — exercises LLMTier-2."""

    @property
    def supports_function_calling(self) -> bool:
        return False

    @property
    def supports_json_mode(self) -> bool:
        return True


class _MinimalLLM(_ScriptedLLM):
    """LLM that supports neither FC nor JSON mode — exercises LLMTier-3."""

    @property
    def supports_function_calling(self) -> bool:
        return False

    @property
    def supports_json_mode(self) -> bool:
        return False


class TestLLMTierSelection:
    """Plan §2.2: LLMTier selected at startup per LLM capability, three tiers."""

    def test_tier_1_when_function_calling_available(self) -> None:
        llm = _ScriptedLLM("test_tool", {"query": "x"}, "done")
        engine = ReActEngine(_make_deps_with_llm(llm))
        assert engine._select_tier() == InteractionTier.REACT_FUNCTION_CALLING

    def test_tier_2_when_json_mode_only(self) -> None:
        llm = _FCDisabledLLM("test_tool", {"query": "x"}, "done")
        engine = ReActEngine(_make_deps_with_llm(llm))
        assert engine._select_tier() == InteractionTier.STRUCTURED_OUTPUT

    def test_tier_3_when_no_llm_capabilities(self) -> None:
        llm = _MinimalLLM("test_tool", {"query": "x"}, "done")
        engine = ReActEngine(_make_deps_with_llm(llm))
        assert engine._select_tier() == InteractionTier.EMBEDDING_RULES

    def test_tier_3_when_no_llm_provider(self) -> None:
        deps = CognitiveDependencies()
        deps.capability.llm_provider = None
        engine = ReActEngine(deps)
        assert engine._select_tier() == InteractionTier.EMBEDDING_RULES


class _SlowTool(BrainTool):
    """Tool that sleeps beyond §4.2.2 30s timeout — for ratchet timeout trigger test.

    Registered under a Tier-A name (web_search) so hooks don't block it.
    """

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Slow test tool"

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {"query": {"type": "string"}}}

    async def execute(self, **kwargs) -> ToolResult:
        import asyncio
        await asyncio.sleep(60)  # Exceeds 30s timeout
        return ToolResult(output={"slow": True})


class _EscalateNoReasonLLM(LLMProvider):
    """LLM that calls escalate without 'reason' — triggers PolicyHook DENY.

    Phase 1: emit tool_call without reason (PolicyHook rejects).
    Phase 2: emit text reply.
    """

    def __init__(self) -> None:
        self._call_count = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self._call_count += 1
        if self._call_count == 1:
            import json
            return LLMResponse(
                content="",
                tool_calls=[{
                    "id": "call-1",
                    "function": {
                        "name": "escalate",
                        "arguments": json.dumps({"target": "human_review", "summary": "需要人工介入"}),
                    },
                }],
                model_used="scripted",
            )
        return LLMResponse(content="ok got it", model_used="scripted")

    async def stream(self, request: LLMRequest):
        yield ""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    def health_check(self) -> bool:
        return True


class TestPolicyRatchetIntegration:
    """Plan §4.2.2 棘轮机制: failure events append to draft store."""

    @pytest.mark.asyncio
    async def test_tier_a_exhausted_retries_records_schema_fail(self, tmp_path) -> None:
        """Tier-A 2x schema fail → ratchet.record(trigger=schema_fail)."""
        from cognitiveplane.governance.policy_ratchet import PolicyRatchet, TRIGGER_SCHEMA_FAIL
        ratchet = PolicyRatchet(path=tmp_path / "r.yaml")
        llm = _AlwaysMalformedLLM(tool_name="web_search")
        registry = ToolRegistry()
        registry.register(_TierATestTool())
        engine = ReActEngine(_make_deps_with_llm(llm), tool_registry=registry, policy_ratchet=ratchet)
        await engine.run("search", _make_context())
        # Ratchet should have at least one schema_fail entry for web_search
        entries = ratchet.list_entries()
        schema_fail_entries = [e for e in entries if e["trigger_event"] == TRIGGER_SCHEMA_FAIL]
        assert len(schema_fail_entries) >= 1
        assert schema_fail_entries[0]["tool_name"] == "web_search"

    @pytest.mark.asyncio
    async def test_tier_b_schema_fail_records_reask_reject(self, tmp_path) -> None:
        """Tier-B schema fail → ratchet.record(trigger=reask_reject)."""
        from cognitiveplane.governance.policy_ratchet import PolicyRatchet, TRIGGER_REASK_REJECT
        ratchet = PolicyRatchet(path=tmp_path / "r.yaml")
        llm = _ScriptedMalformedLLM(
            tool_name="escalate",
            malformed="{not valid",
            valid_args={"param": "x"},
            final_text="done",
            retry_count=1,
        )
        registry = ToolRegistry()
        registry.register(_TierBTestTool())
        engine = ReActEngine(_make_deps_with_llm(llm), tool_registry=registry, policy_ratchet=ratchet)
        await engine.run("adjust", _make_context())
        entries = ratchet.list_entries()
        reask_entries = [e for e in entries if e["trigger_event"] == TRIGGER_REASK_REJECT]
        assert len(reask_entries) >= 1
        assert reask_entries[0]["tool_name"] == "escalate"

    @pytest.mark.asyncio
    async def test_policyhook_deny_on_tier_b_records_reask_reject(self, tmp_path) -> None:
        """PolicyHook denies Tier-B (missing reason) → ratchet.record(reask_reject)."""
        from cognitiveplane.governance.policy_ratchet import PolicyRatchet, TRIGGER_REASK_REJECT
        from cognitiveplane.control.hooks import PolicyHook
        from cognitiveplane.governance.tool_policy import ToolPolicy
        ratchet = PolicyRatchet(path=tmp_path / "r.yaml")
        llm = _EscalateNoReasonLLM()
        registry = ToolRegistry()
        registry.register(_TierBTestTool())
        engine = ReActEngine(
            _make_deps_with_llm(llm),
            tool_registry=registry,
            hooks=[PolicyHook(ToolPolicy())],
            policy_ratchet=ratchet,
        )
        await engine.run("adjust", _make_context())
        entries = ratchet.list_entries()
        reask_entries = [e for e in entries if e["trigger_event"] == TRIGGER_REASK_REJECT]
        assert len(reask_entries) >= 1
        assert "DENY" in reask_entries[0]["failure_pattern"] or "reason" in reask_entries[0]["failure_pattern"].lower()

    @pytest.mark.asyncio
    async def test_tool_timeout_records_timeout_trigger(self, tmp_path, monkeypatch) -> None:
        """Tool execution exceeds 30s → ratchet.record(trigger=timeout)."""
        from cognitiveplane.governance.policy_ratchet import PolicyRatchet, TRIGGER_TIMEOUT
        import cognitiveplane.control.engine.session_notes as session_notes_module
        # Lower timeout to 0.1s so test doesn't wait 30s.
        # Target engine.session_notes (where TOOL_TIMEOUT_SECONDS is defined)
        # because tool_execution.execute_with_timeout lazy-imports it from there.
        monkeypatch.setattr(session_notes_module, "TOOL_TIMEOUT_SECONDS", 0.1)
        ratchet = PolicyRatchet(path=tmp_path / "r.yaml")
        # _ScriptedLLM emits tool_call then final reply
        llm = _ScriptedLLM("web_search", {"query": "x"}, "done after timeout")
        registry = ToolRegistry()
        registry.register(_SlowTool())
        engine = ReActEngine(_make_deps_with_llm(llm), tool_registry=registry, policy_ratchet=ratchet)
        await engine.run("slow", _make_context())
        entries = ratchet.list_entries()
        timeout_entries = [e for e in entries if e["trigger_event"] == TRIGGER_TIMEOUT]
        assert len(timeout_entries) == 1
        assert timeout_entries[0]["tool_name"] == "web_search"

    @pytest.mark.asyncio
    async def test_successful_run_records_nothing(self, tmp_path) -> None:
        """No failures → ratchet stays empty."""
        from cognitiveplane.governance.policy_ratchet import PolicyRatchet
        ratchet = PolicyRatchet(path=tmp_path / "r.yaml")
        llm = _ScriptedLLM("web_search", {"query": "x"}, "ok")
        registry = ToolRegistry()
        registry.register(_TierATestTool())
        engine = ReActEngine(_make_deps_with_llm(llm), tool_registry=registry, policy_ratchet=ratchet)
        await engine.run("search", _make_context())
        assert ratchet.count() == 0


class _DesignWorkflowCaptureTool(BrainTool):
    """Captures the arguments design_workflow was called with — verifies fallback
    paths use the WorkflowSpec entry shape (objective + reason), not legacy query."""

    phase = 3

    def __init__(self) -> None:
        self.captured_args: dict | None = None

    @property
    def name(self) -> str:
        return "design_workflow"

    @property
    def description(self) -> str:
        return "capture-only test stub"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "objective": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["objective", "reason"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        self.captured_args = dict(kwargs)
        return ToolResult(output={"status": "draft", "workflow_spec": {"nodes": []}})


class TestFallbackDesignWorkflowShape:
    """Boundary-pinning 2026-06-25: Tier-2/Tier-3 fallback must call design_workflow
    with the WorkflowSpec entry shape (objective + reason), not legacy {"query": ...}."""

    @pytest.mark.asyncio
    async def test_tier_3_fallback_uses_workflow_spec_shape(self) -> None:
        """Tier-3 keyword routing → design_workflow gets {objective, reason}."""
        deps = CognitiveDependencies()  # No LLM → Tier-3
        registry = ToolRegistry(current_phase=3)
        capture = _DesignWorkflowCaptureTool()
        registry.register(capture)
        engine = ReActEngine(deps, tool_registry=registry)
        # Keyword map routes "设计"/"方案"/"检测" to design_workflow
        response = await engine.run("请帮我设计检测方案", _make_context())
        assert response.tier_used == InteractionTier.EMBEDDING_RULES
        assert "design_workflow" in response.tools_used
        assert capture.captured_args is not None
        assert "objective" in capture.captured_args
        assert "reason" in capture.captured_args
        assert "query" not in capture.captured_args

    @pytest.mark.asyncio
    async def test_tier_2_fallback_uses_workflow_spec_shape(self) -> None:
        """Tier-2 structured-output routing → design_workflow gets {objective, reason}."""
        # LLM that classifies intent as workflow_design
        class _IntentLLM(LLMProvider):
            def __init__(self) -> None:
                self._n = 0

            async def complete(self, request: LLMRequest) -> LLMResponse:
                self._n += 1
                # First call = intent classification → "workflow_design"
                if self._n == 1:
                    return LLMResponse(content="workflow_design", model_used="intent")
                return LLMResponse(content="ok", model_used="intent")

            async def stream(self, request: LLMRequest):
                yield "ok"

            async def embed(self, texts: list[str]) -> list[list[float]]:
                return [[0.0] for _ in texts]

            def health_check(self) -> bool:
                return True

            @property
            def supports_function_calling(self) -> bool:
                return False

            @property
            def supports_json_mode(self) -> bool:
                return True

        deps = CognitiveDependencies()
        deps.capability.llm_provider = _IntentLLM()
        registry = ToolRegistry(current_phase=3)
        capture = _DesignWorkflowCaptureTool()
        registry.register(capture)
        engine = ReActEngine(deps, tool_registry=registry)
        response = await engine.run("设计一个方案", _make_context())
        assert response.tier_used == InteractionTier.STRUCTURED_OUTPUT
        assert "design_workflow" in response.tools_used
        assert capture.captured_args is not None
        assert "objective" in capture.captured_args
        assert "reason" in capture.captured_args
        assert "query" not in capture.captured_args


# ===================================================================
# Phase 5: Agent Skills 模块化
# ===================================================================


class _ToolCaptureLLM(_ScriptedLLM):
    """Scripted LLM that captures the tool definitions sent by ReActEngine."""

    def __init__(self, tool_name: str, tool_args: dict, final_text: str) -> None:
        super().__init__(tool_name, tool_args, final_text)
        self.captured_tool_defs: list[dict] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.captured_tool_defs = request.tools or []
        return await super().complete(request)


class TestAgentSkills:
    """Phase 5: Agent Skills — skill selection + tool whitelist filtering + prompt injection."""

    @pytest.mark.asyncio
    async def test_skill_filters_llm_tool_definitions(self) -> None:
        """选中 skill 后，传给 LLM 的 tool definitions 只包含 allowed_tools。"""
        registry = ToolRegistry()
        registry.register(TestTool())
        registry.register(_TierATestTool())
        registry.register(_TierBTestTool())

        skill = Skill(
            name="web_only",
            description="Only web search",
            allowed_tools=["web_search"],
            triggers=["web"],
        )
        skill_registry = SkillRegistry([skill])

        llm = _ToolCaptureLLM("web_search", {"query": "x"}, "done")
        engine = ReActEngine(
            _make_deps_with_llm(llm),
            tool_registry=registry,
            skill_registry=skill_registry,
        )
        response = await engine.run("web search something", _make_context())

        tool_names = [t.get("function", {}).get("name") for t in llm.captured_tool_defs]
        assert "web_search" in tool_names
        assert "test_tool" not in tool_names
        assert "escalate" not in tool_names
        assert response.tier_used == InteractionTier.REACT_FUNCTION_CALLING

    @pytest.mark.asyncio
    async def test_skill_injects_system_prompt(self) -> None:
        """选中 skill 后，system prompt 包含其专业化指令。"""
        llm = _CapturingLLM()
        skill = Skill(
            name="test_skill",
            description="Test skill prompt injection",
            system_prompt="你是测试专家，只能回答测试相关问题。",
            allowed_tools=[],
            triggers=["测试"],
        )
        engine = ReActEngine(
            _make_deps_with_llm(llm),
            skill_registry=SkillRegistry([skill]),
        )
        await engine.run("这是一个测试", _make_context())
        assert llm.captured_system is not None
        assert "你是测试专家" in llm.captured_system

    @pytest.mark.asyncio
    async def test_no_skill_registry_keeps_all_visible_tools(self) -> None:
        """未配置 skill_registry 时，所有当前 phase 可见工具都暴露给 LLM。"""
        registry = ToolRegistry()
        registry.register(TestTool())
        registry.register(_TierATestTool())

        llm = _ToolCaptureLLM("test_tool", {"query": "x"}, "done")
        engine = ReActEngine(
            _make_deps_with_llm(llm),
            tool_registry=registry,
        )
        await engine.run("any", _make_context())

        tool_names = [t.get("function", {}).get("name") for t in llm.captured_tool_defs]
        assert "test_tool" in tool_names
        assert "web_search" in tool_names

    def test_skill_registry_selects_by_priority_on_tie(self) -> None:
        """同分 skill 按 priority 选择高优先级。"""
        low = Skill(name="low", description="", triggers=["测试"], priority=1)
        high = Skill(name="high", description="", triggers=["测试"], priority=10)
        registry = SkillRegistry([low, high])
        selected = registry.select_skill("测试")
        assert selected is not None
        assert selected.name == "high"

    @pytest.mark.asyncio
    async def test_skill_whitelist_blocks_non_allowed_tool_in_tier3(self) -> None:
        """Tier-3 fallback 也遵守 skill allowed_tools 白名单。"""
        deps = CognitiveDependencies()
        registry = ToolRegistry()
        registry.register(_TierATestTool())
        registry.register(_TierBTestTool())

        skill = Skill(
            name="no_escalate",
            description="Can use web_search but not escalate",
            allowed_tools=["web_search"],
            triggers=["参数"],
        )
        engine = ReActEngine(
            deps,
            tool_registry=registry,
            skill_registry=SkillRegistry([skill]),
        )
        # "调整" 命中 escalate 关键词，"search" 命中 web_search；
        # skill 只允许 web_search，因此 escalate 被拦截，web_search 胜出。
        response = await engine.run("帮我调整 search 参数", _make_context())
        assert "escalate" not in response.tools_used
        assert "web_search" in response.tools_used
