"""Tests for ToolRegistry and BrainTool ABC."""

import pytest

from cognitiveplane.control.deps import CognitiveDependencies
from cognitiveplane.control.tool_registry import ToolRegistry
from cognitiveplane.control.tools import BrainTool, ToolResult
from cognitiveplane.control.tools.search_standards import SearchStandardsTool
from cognitiveplane.control.tools.request_confirmation import RequestConfirmationTool
from cognitiveplane.control.tools.explain_decision import ExplainDecisionTool
from cognitiveplane.control.tools.archive_memory import ArchiveMemoryTool
from cognitiveplane.control.hooks import HookDecision, HookResult, SafetyHook
from cognitiveplane.governance.tool_policy import ToolPolicy, ToolRule


class TestBrainToolABC:
    def test_tool_result_to_json(self):
        result = ToolResult(output={"key": "value"})
        assert result.to_json() == {"output": {"key": "value"}}
        assert result.error is None

    def test_tool_result_with_error(self):
        result = ToolResult(error="something failed")
        j = result.to_json()
        assert j["error"] == "something failed"


class TestToolRegistry:
    def test_register_and_list(self):
        registry = ToolRegistry()
        registry.register(RequestConfirmationTool())
        assert "request_confirmation" in registry.list_tools()

    def test_get_tool(self):
        registry = ToolRegistry()
        registry.register(ExplainDecisionTool())
        tool = registry.get_tool("explain_decision")
        assert tool is not None
        assert tool.name == "explain_decision"

    def test_get_unknown_tool(self):
        registry = ToolRegistry()
        assert registry.get_tool("nonexistent") is None

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        registry = ToolRegistry()
        result = await registry.execute("nonexistent", {})
        assert result.error is not None

    def test_get_llm_tool_definitions(self):
        registry = ToolRegistry()
        registry.register(RequestConfirmationTool())
        defs = registry.get_llm_tool_definitions()
        assert len(defs) == 1
        assert defs[0]["type"] == "function"
        assert defs[0]["function"]["name"] == "request_confirmation"

    @pytest.mark.asyncio
    async def test_execute_unknown_tool_async(self):
        registry = ToolRegistry()
        result = await registry.execute("nonexistent", {})
        assert result.error == "Unknown tool: nonexistent"

    @pytest.mark.asyncio
    async def test_request_confirmation_tool(self):
        tool = RequestConfirmationTool()
        result = await tool.execute(question="确认参数?", options=["是", "否"])
        assert result.output["status"] == "confirmation_requested"
        assert result.output["question"] == "确认参数?"

    @pytest.mark.asyncio
    async def test_explain_decision_tool(self):
        tool = ExplainDecisionTool()
        result = await tool.execute(decision_id="test-123")
        assert "decision_id" in result.output

    def test_to_function_definition(self):
        tool = RequestConfirmationTool()
        fd = tool.to_function_definition()
        assert fd["type"] == "function"
        assert fd["function"]["name"] == "request_confirmation"
        assert "parameters" in fd["function"]

    def test_to_embedding_description(self):
        tool = ExplainDecisionTool()
        desc = tool.to_embedding_description()
        assert "explain_decision" in desc


class TestToolPolicy:
    def test_default_rules(self):
        policy = ToolPolicy()
        rule = policy.get_rule("search_standards")
        assert rule.auto_approve is True

    def test_wildcard_default(self):
        policy = ToolPolicy()
        rule = policy.get_rule("unknown_tool")
        assert rule.auto_approve is False
        assert rule.enabled is True

    def test_design_workflow_requires_reason(self):
        policy = ToolPolicy()
        rule = policy.get_rule("design_workflow")
        assert rule.require_reason is True
        assert rule.auto_approve is False

    def test_adjust_parameter_max_calls(self):
        policy = ToolPolicy()
        rule = policy.get_rule("adjust_parameter")
        assert rule.max_calls_per_session == 5

    def test_overrides(self):
        policy = ToolPolicy(overrides={"search_standards": ToolRule(enabled=False)})
        rule = policy.get_rule("search_standards")
        assert rule.enabled is False

    @pytest.mark.asyncio
    async def test_auto_approve(self):
        from cognitiveplane.shared.dto_context import ContextSnapshot
        from cognitiveplane.shared.enums import EventType, NoveltyLevel
        from cognitiveplane.shared.types import CaseId
        from datetime import datetime, timezone

        policy = ToolPolicy()
        context = ContextSnapshot(
            case_id=CaseId(value="test"),
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
        result = await policy.request_approval("search_standards", {}, context)
        assert result.approved is True


class TestSafetyHook:
    @pytest.mark.asyncio
    async def test_allow_normal(self):
        from cognitiveplane.shared.dto_context import ContextSnapshot
        from cognitiveplane.shared.enums import EventType, NoveltyLevel, SafetyStatus
        from cognitiveplane.shared.types import CaseId
        from datetime import datetime, timezone

        hook = SafetyHook()
        context = ContextSnapshot(
            case_id=CaseId(value="test"),
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
        result = await hook.before_execute("search_standards", {}, context)
        assert result.decision == HookDecision.ALLOW


class TestMaxCallsPerSessionIsolation:
    """Plan §4.2 line 761: adjust_parameter 每 session 最多 5 次.
    Bug fix: ContextSnapshot 无 session_id 字段, 之前 getattr 退化为 process 级.
    Fix: session_id 由 PolicyHook 透传, 不同 session 计数器隔离."""

    @pytest.mark.asyncio
    async def test_max_calls_isolated_per_session(self) -> None:
        from cognitiveplane.shared.dto_context import ContextSnapshot
        from cognitiveplane.shared.enums import EventType, NoveltyLevel
        from cognitiveplane.shared.types import CaseId
        from datetime import datetime, timezone
        from cognitiveplane.governance.tool_policy import ToolPolicy, ToolRule

        policy = ToolPolicy(overrides={
            "adjust_parameter": ToolRule(
                enabled=True, auto_approve=False, require_reason=True, max_calls_per_session=2,
            ),
        })
        context = ContextSnapshot(
            case_id=CaseId(value="test"),
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
        args = {"parameter_name": "voltage", "proposed_value": "12V", "reason": "test"}

        # Session A: 2 calls OK, 3rd rejected
        assert (await policy.request_approval("adjust_parameter", args, context, session_id="A")).approved
        assert (await policy.request_approval("adjust_parameter", args, context, session_id="A")).approved
        r3 = await policy.request_approval("adjust_parameter", args, context, session_id="A")
        assert not r3.approved
        assert "max calls" in (r3.reason or "").lower()

        # Session B: independent counter — should be allowed despite A being full
        r_b1 = await policy.request_approval("adjust_parameter", args, context, session_id="B")
        assert r_b1.approved, "session B must have independent counter, not share A's quota"

    @pytest.mark.asyncio
    async def test_default_session_when_not_specified(self) -> None:
        from cognitiveplane.shared.dto_context import ContextSnapshot
        from cognitiveplane.shared.enums import EventType, NoveltyLevel
        from cognitiveplane.shared.types import CaseId
        from datetime import datetime, timezone
        from cognitiveplane.governance.tool_policy import ToolPolicy, ToolRule

        policy = ToolPolicy(overrides={
            "adjust_parameter": ToolRule(
                enabled=True, auto_approve=False, require_reason=True, max_calls_per_session=1,
            ),
        })
        context = ContextSnapshot(
            case_id=CaseId(value="test"),
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
        args = {"parameter_name": "voltage", "proposed_value": "12V", "reason": "test"}
        # No session_id → defaults to "default" bucket
        assert (await policy.request_approval("adjust_parameter", args, context)).approved
        r2 = await policy.request_approval("adjust_parameter", args, context)
        assert not r2.approved

    @pytest.mark.asyncio
    async def test_policyhook_passes_session_id_to_policy(self) -> None:
        """End-to-end: ReActEngine → PolicyHook → ToolPolicy with real session_id."""
        from cognitiveplane.control.hooks import PolicyHook
        from cognitiveplane.governance.tool_policy import ToolPolicy, ToolRule
        from cognitiveplane.shared.dto_context import ContextSnapshot
        from cognitiveplane.shared.enums import EventType, NoveltyLevel
        from cognitiveplane.shared.types import CaseId
        from datetime import datetime, timezone

        policy = ToolPolicy(overrides={
            "adjust_parameter": ToolRule(
                enabled=True, auto_approve=False, require_reason=True, max_calls_per_session=1,
            ),
        })
        hook = PolicyHook(policy)
        context = ContextSnapshot(
            case_id=CaseId(value="test"),
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
        args = {"parameter_name": "voltage", "proposed_value": "12V", "reason": "test"}

        # Session A: first call approved
        r1 = await hook.before_execute("adjust_parameter", args, context, session_id="A")
        assert r1.decision.value == "allow"
        # Session A: second call denied (quota 1)
        r2 = await hook.before_execute("adjust_parameter", args, context, session_id="A")
        assert r2.decision.value == "deny"
        # Session B: first call approved (independent quota)
        r3 = await hook.before_execute("adjust_parameter", args, context, session_id="B")
        assert r3.decision.value == "allow"


class TestSchemaValidation:
    """Plan §3.5 原则 5: ToolRegistry 用 JSON Schema 校验参数.
    Plan §3.6 line 653: schema 校验失败 → Tier-A retry / Tier-B reject."""

    def test_validate_accepts_valid_arguments(self) -> None:
        from cognitiveplane.control.tool_registry import ToolRegistry
        from cognitiveplane.control.tools import BrainTool, ToolResult

        class _Tool(BrainTool):
            @property
            def name(self) -> str: return "test_tool"
            @property
            def description(self) -> str: return "test"
            @property
            def parameters_schema(self) -> dict:
                return {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                }
            async def execute(self, **kwargs) -> ToolResult:
                return ToolResult(output={})

        registry = ToolRegistry()
        registry.register(_Tool())
        valid, error = registry.validate_arguments("test_tool", {"query": "hello"})
        assert valid
        assert error is None

    def test_validate_rejects_missing_required(self) -> None:
        from cognitiveplane.control.tool_registry import ToolRegistry
        from cognitiveplane.control.tools import BrainTool, ToolResult

        class _Tool(BrainTool):
            @property
            def name(self) -> str: return "test_tool"
            @property
            def description(self) -> str: return "test"
            @property
            def parameters_schema(self) -> dict:
                return {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                }
            async def execute(self, **kwargs) -> ToolResult:
                return ToolResult(output={})

        registry = ToolRegistry()
        registry.register(_Tool())
        valid, error = registry.validate_arguments("test_tool", {})
        assert not valid
        assert "query" in error

    def test_validate_rejects_wrong_type(self) -> None:
        from cognitiveplane.control.tool_registry import ToolRegistry
        from cognitiveplane.control.tools import BrainTool, ToolResult

        class _Tool(BrainTool):
            @property
            def name(self) -> str: return "test_tool"
            @property
            def description(self) -> str: return "test"
            @property
            def parameters_schema(self) -> dict:
                return {
                    "type": "object",
                    "properties": {"count": {"type": "number"}},
                    "required": ["count"],
                }
            async def execute(self, **kwargs) -> ToolResult:
                return ToolResult(output={})

        registry = ToolRegistry()
        registry.register(_Tool())
        valid, error = registry.validate_arguments("test_tool", {"count": "not a number"})
        assert not valid
        assert "count" in error

    def test_validate_rejects_invalid_enum(self) -> None:
        from cognitiveplane.control.tool_registry import ToolRegistry
        from cognitiveplane.control.tools import BrainTool, ToolResult

        class _Tool(BrainTool):
            @property
            def name(self) -> str: return "test_tool"
            @property
            def description(self) -> str: return "test"
            @property
            def parameters_schema(self) -> dict:
                return {
                    "type": "object",
                    "properties": {"action": {"type": "string", "enum": ["a", "b", "c"]}},
                    "required": ["action"],
                }
            async def execute(self, **kwargs) -> ToolResult:
                return ToolResult(output={})

        registry = ToolRegistry()
        registry.register(_Tool())
        valid, error = registry.validate_arguments("test_tool", {"action": "d"})
        assert not valid
        assert "action" in error or "d" in error

    def test_validate_allows_extra_properties(self) -> None:
        """Plan §3.5 原则 4 必填最少化: LLM 友好, 允许额外字段."""
        from cognitiveplane.control.tool_registry import ToolRegistry
        from cognitiveplane.control.tools import BrainTool, ToolResult

        class _Tool(BrainTool):
            @property
            def name(self) -> str: return "test_tool"
            @property
            def description(self) -> str: return "test"
            @property
            def parameters_schema(self) -> dict:
                return {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                }
            async def execute(self, **kwargs) -> ToolResult:
                return ToolResult(output={})

        registry = ToolRegistry()
        registry.register(_Tool())
        # Extra 'context' field — should be allowed, not rejected
        valid, error = registry.validate_arguments("test_tool", {"query": "x", "context": "extra"})
        assert valid, f"extra properties should be allowed: {error}"

    @pytest.mark.asyncio
    async def test_execute_returns_schema_invalid_error_type(self) -> None:
        from cognitiveplane.control.tool_registry import ToolRegistry, SCHEMA_INVALID_ERROR_TYPE
        from cognitiveplane.control.tools import BrainTool, ToolResult

        class _Tool(BrainTool):
            @property
            def name(self) -> str: return "test_tool"
            @property
            def description(self) -> str: return "test"
            @property
            def parameters_schema(self) -> dict:
                return {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                }
            async def execute(self, **kwargs) -> ToolResult:
                return ToolResult(output={"never": "reached"})

        registry = ToolRegistry()
        registry.register(_Tool())
        # Missing required 'query' — execute must NOT call tool.execute
        result = await registry.execute("test_tool", {})
        assert result.error is not None
        assert result.error_type == SCHEMA_INVALID_ERROR_TYPE
        assert "query" in result.error


def test_unregister_removes_tool():
    """Spec §3.3 — list_changed 下线工具时调用 unregister 移除。"""
    from cognitiveplane.control.tools import BrainTool, ToolResult

    class FakeTool(BrainTool):
        @property
        def name(self):
            return "fake_tool"

        @property
        def description(self):
            return "d"

        @property
        def parameters_schema(self):
            return {}

        async def execute(self, **kwargs):
            return ToolResult()

    registry = ToolRegistry()
    registry.register(FakeTool())

    assert "fake_tool" in registry._tools
    registry.unregister("fake_tool")
    assert "fake_tool" not in registry._tools


def test_unregister_nonexistent_silent():
    """Spec §5 — unregister 不存在的工具静默返回，不抛异常。"""
    registry = ToolRegistry()
    # Should not raise
    registry.unregister("nonexistent_tool")
    assert "nonexistent_tool" not in registry._tools
