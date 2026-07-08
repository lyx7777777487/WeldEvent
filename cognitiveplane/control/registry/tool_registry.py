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
    from cognitiveplane.control.react import ApprovalStore
    from cognitiveplane.interaction.image_store import ImageStore


# Schema validation error type — consumed by ReActEngine to drive Tier-A retry / Tier-B reject
SCHEMA_INVALID_ERROR_TYPE = "schema_invalid"


class ToolRegistry:
    """Central registry of Brain's available tools."""

    def __init__(
        self,
        deps: CognitiveDependencies | None = None,
        image_store: "ImageStore | None" = None,
        current_phase: int = 3,
        approval_store: "ApprovalStore | None" = None,
    ) -> None:
        """Construct registry. If deps given, auto-register tools from deps.

        image_store: session-scoped image storage (plan §A.3). Passed explicitly
            rather than via CapabilityDeps because it's runtime state, not a
            Provider. When None, AnalyzeImageTool is not registered.
        current_phase: 系统当前阶段 (boundary-pinning 2026-06-25). 工具的
            BrainTool.phase > current_phase 时仍注册 (execute() 可显式调用) 但
            get_llm_tool_definitions() 不暴露 — LLM 看不到也调不到. 默认 3 = 当前
            Phase 3 (2026-06-26 从 2 上调). 推进到 Phase 4+ 时调高即可隐藏未就绪工具.
        approval_store: 架构级 approval gate 共享存储。传入后 RequestConfirmationTool
            会用它阻塞等待用户在弹窗里的选项响应。None 时退化为非阻塞模式
            （LLM 拿不到用户的选择）。
        """
        self._tools: dict[str, BrainTool] = {}
        self._current_phase = current_phase
        self._approval_store = approval_store
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
        from cognitiveplane.control.tools.launch_workflow import LaunchWorkflowTool
        from cognitiveplane.control.tools.request_confirmation import RequestConfirmationTool
        from cognitiveplane.control.tools.escalate import EscalateTool
        from cognitiveplane.control.tools.explain_decision import ExplainDecisionTool
        from cognitiveplane.control.tools.archive_memory import ArchiveMemoryTool
        from cognitiveplane.control.tools.web_search import WebSearchTool
        from cognitiveplane.control.tools.analyze_image import AnalyzeImageTool
        from cognitiveplane.control.tools.upload_image_to_dataset import UploadImageToDatasetTool

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

        # L1 包装工具 — image_ref → Label Studio 数据集上传。
        # 解决 LLM 有 image_ref 但 MCP upload_images 需要 base64 的断层。
        # tool_registry 引用延迟注入 (MCP upload_images 注册后才能调)。
        if image_store is not None:
            self.register(UploadImageToDatasetTool(
                image_store=image_store,
                tool_registry=self,
            ))

        # Gateway tools
        if deps.gateway.read is not None:
            self.register(ReadWeldMapTool(deps.gateway.read))
        # AdjustParameterTool removed 2026-06-26 — industrial verb "adjust" belongs to
        # executionplane ToolPool (boundary-pinning §2.1). Phase 5 will re-implement there.
        # Governance policy (tool_policy.py / hooks.py / permissions.py) retained as no-op.
        if deps.gateway.write is not None:
            self.register(DesignWorkflowTool(deps))

        # L1→L2 Bridge tool — 提交 WorkflowSpec 到 Temporal（boundary-pinning §6.2）
        # 需要 bridge.event_connector 已装配（app.py 注入 TemporalWorkflowLaunchPort）
        # P4 fix: 注入 image_store 让 launch_workflow 能解析 design_workflow 产出的
        # image_refs（PENDING:session_id:index）→ 磁盘 image_path，供 L3 IQA/PPA 读取
        if deps.bridge.event_connector is not None:
            self.register(LaunchWorkflowTool(deps, image_store=image_store))
            # P1-6: 工作流控制工具 — query/pause/resume/cancel 正在执行的 workflow
            # 让 LLM 能通过自然语言介入工作流执行过程
            from cognitiveplane.control.tools.workflow_control import WorkflowControlTool
            self.register(WorkflowControlTool(deps))

        # Human interaction tools
        if deps.gateway.write is not None:
            self.register(RequestConfirmationTool(deps.gateway.write, approval_store=self._approval_store))
        else:
            self.register(RequestConfirmationTool(approval_store=self._approval_store))
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
        # manage_plan 暂不默认注册 — 边界钉死 (2026-06-25): LLM 可见工具集过宽会把
        # 未来带向 "LLM 直接执行一切". manage_plan 留作 phase 3+ AgentLoop 批量任务
        # 场景按需 opt-in (见 boundary-pinning spec §BrainToolRegistry profile).
        # 工具类本身保留, 测试可直接构造 ManagePlanTool() 验证功能.

    def get_llm_tool_definitions(
        self, allowed_tools: list[str] | None = None
    ) -> list[dict]:
        """Return tool definitions in LLM Function Calling format.

        Boundary-pinning 2026-06-25: 过滤 phase > current_phase 的工具 — 它们仍
        在注册表里 (execute() 可显式调用), 但不出现在 LLM 工具表里, 防止未就绪
        工具被 LLM 误调用.

        Phase 5 Agent Skills: 传入 allowed_tools 可进一步按 skill 白名单过滤。
        """
        allowed = set(allowed_tools) if allowed_tools is not None else None
        defs = [
            tool.to_function_definition()
            for tool in self._tools.values()
            if self.is_llm_visible(tool.name)
            and (allowed is None or tool.name in allowed)
        ]
        return defs

    def is_llm_visible(self, tool_name: str) -> bool:
        """Whether a tool is visible/callable from the Brain LLM at this phase."""
        tool = self._tools.get(tool_name)
        return tool is not None and getattr(tool, "phase", 1) <= self._current_phase

    def list_llm_tools(self, allowed_tools: list[str] | None = None) -> list[str]:
        """List tools visible to the Brain LLM at this phase."""
        allowed = set(allowed_tools) if allowed_tools is not None else None
        return [
            name
            for name in self._tools
            if self.is_llm_visible(name)
            and (allowed is None or name in allowed)
        ]

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
