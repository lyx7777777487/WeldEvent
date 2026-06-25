"""ToolRegistry — central registry of Brain's available tools.

Source: 7-plane redesign spec §7 Tool Registry.

Plan §3.5 原则 5 (line 630): 工具调用前 ToolRegistry 用 JSON Schema 校验参数.
Plan §3.6 line 653: schema 校验失败 → 自动 retry 1 次 (Tier-A) 或直接拒绝 (Tier-B).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import jsonschema

from cognitiveplane.control.deps import CognitiveDependencies
from cognitiveplane.control.tools import BrainTool, ToolResult

if TYPE_CHECKING:
    from cognitiveplane.interaction.image_store import ImageStore


# Schema validation error type — consumed by ReActEngine to drive Tier-A retry / Tier-B reject
SCHEMA_INVALID_ERROR_TYPE = "schema_invalid"


class ToolRegistry:
    """Central registry of Brain's available tools."""

    def __init__(
        self,
        deps: CognitiveDependencies | None = None,
        image_store: "ImageStore | None" = None,
    ) -> None:
        """Construct registry. If deps given, auto-register tools from deps.

        image_store: session-scoped image storage (plan §A.3). Passed explicitly
            rather than via CapabilityDeps because it's runtime state, not a
            Provider. When None, AnalyzeImageTool is not registered.
        """
        self._tools: dict[str, BrainTool] = {}
        if deps is not None:
            self._register_tools(deps, image_store)

    def register(self, tool: BrainTool) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """移除工具 — list_changed 下线时调用。

        Spec §5 — 不存在的工具静默返回，不抛异常（list_changed diff
        可能与当前注册状态有偏差）。
        """
        self._tools.pop(name, None)

    def _register_tools(
        self,
        deps: CognitiveDependencies,
        image_store: "ImageStore | None" = None,
    ) -> None:
        from cognitiveplane.control.tools.search_standards import SearchStandardsTool
        from cognitiveplane.control.tools.search_cases import SearchCasesTool
        from cognitiveplane.control.tools.search_process import SearchProcessTool
        from cognitiveplane.control.tools.read_weldmap import ReadWeldMapTool
        from cognitiveplane.control.tools.design_workflow import DesignWorkflowTool
        from cognitiveplane.control.tools.adjust_parameter import AdjustParameterTool
        from cognitiveplane.control.tools.request_confirmation import RequestConfirmationTool
        from cognitiveplane.control.tools.escalate import EscalateTool
        from cognitiveplane.control.tools.explain_decision import ExplainDecisionTool
        from cognitiveplane.control.tools.archive_memory import ArchiveMemoryTool
        from cognitiveplane.control.tools.web_search import WebSearchTool
        from cognitiveplane.control.tools.analyze_image import AnalyzeImageTool
        from cognitiveplane.control.tools.manage_plan import ManagePlanTool

        # Knowledge tools
        if deps.knowledge.standards_query is not None:
            self.register(SearchStandardsTool(deps.knowledge.standards_query))
        if deps.knowledge.case_library is not None:
            self.register(SearchCasesTool(deps.knowledge.case_library))
        if deps.knowledge.process_knowledge is not None:
            self.register(SearchProcessTool(deps.knowledge.process_knowledge))

        # Vision tool — multimodal image analysis (plan §2.3: uses image_id, fetches original from ImageStore)
        # ImageStore passed explicitly (not in CapabilityDeps — §7 contract).
        if deps.capability.llm_provider is not None and image_store is not None:
            self.register(AnalyzeImageTool(
                deps.capability.llm_provider,
                image_store,
            ))

        # Gateway tools
        if deps.gateway.read is not None:
            self.register(ReadWeldMapTool(deps.gateway.read))
        if deps.gateway.write is not None:
            self.register(AdjustParameterTool(deps.gateway.write))
        if deps.control.orchestrator is not None:
            self.register(DesignWorkflowTool(deps.control.orchestrator, deps))

        # Human interaction tools
        if deps.gateway.write is not None:
            self.register(RequestConfirmationTool(deps.gateway.write))
        else:
            self.register(RequestConfirmationTool())
        if deps.governance.validation is not None and deps.gateway.write is not None:
            self.register(EscalateTool(deps.governance.validation, deps.gateway.write))

        # Copilot tools
        if deps.control.decision_repo is not None:
            self.register(ExplainDecisionTool(deps.control.decision_repo))
        else:
            self.register(ExplainDecisionTool())

        # Memory tools
        if deps.memory.write is not None:
            self.register(ArchiveMemoryTool(deps.memory.write))

        # Web search tool
        if deps.capability.web_search is not None:
            self.register(WebSearchTool(deps.capability.web_search))

        # Self-management tools (plan §3.1 line 452, §7 line 1193)
        # manage_plan: phase 2 — LLM 自主任务拆解 (Claude Code TodoWrite 借鉴)
        # Always registered — LLM decides whether to use it per conversation.
        self.register(ManagePlanTool())

    def get_llm_tool_definitions(self) -> list[dict]:
        """Return tool definitions in LLM Function Calling format."""
        return [tool.to_function_definition() for tool in self._tools.values()]

    def validate_arguments(self, tool_name: str, arguments: dict) -> tuple[bool, str | None]:
        """Plan §3.5 原则 5: validate arguments against tool's JSON Schema before execution.

        Returns (is_valid, error_message). error_message is None when valid.
        Schema errors covered: missing required fields, wrong types, unknown enum values.
        Extra properties allowed by default (LLM-friendly — don't punish verbose LLMs).
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            return False, f"Unknown tool: {tool_name}"
        schema = tool.parameters_schema
        if not schema:
            return True, None
        try:
            # Allow extra properties — LLMs often pass helpful context that tools can ignore.
            # Plan §3.5 原则 4 "必填项最少化" + "schema 可校验" 一起读: 校验的是必填+类型, 不是字段闭集.
            jsonschema.validate(
                instance=arguments,
                schema=schema,
                cls=jsonschema.Draft7Validator,
            )
            return True, None
        except jsonschema.ValidationError as e:
            # Surface the most actionable path: e.json_path is like "$.parameter_name"
            path = e.json_path if e.json_path != "$" else "(root)"
            return False, f"Schema validation failed at {path}: {e.message}"

    async def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        tool = self._tools.get(tool_name)
        if not tool:
            return ToolResult(error=f"Unknown tool: {tool_name}")
        # Plan §3.5 原则 5: schema 校验失败 → 不进 execute, 返回结构化错误
        is_valid, schema_error = self.validate_arguments(tool_name, arguments)
        if not is_valid:
            return ToolResult(
                error=schema_error,
                error_type=SCHEMA_INVALID_ERROR_TYPE,
            )
        return await tool.execute(**arguments)

    def get_tool(self, tool_name: str) -> BrainTool | None:
        return self._tools.get(tool_name)

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())
