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
