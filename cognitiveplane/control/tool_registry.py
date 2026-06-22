"""ToolRegistry — central registry of Brain's available tools.

Source: 7-plane redesign spec §7 Tool Registry.
"""

from cognitiveplane.control.deps import CognitiveDependencies
from cognitiveplane.control.tools import BrainTool, ToolResult


class ToolRegistry:
    """Central registry of Brain's available tools."""

    def __init__(self, deps: CognitiveDependencies | None = None) -> None:
        self._tools: dict[str, BrainTool] = {}
        if deps is not None:
            self._register_tools(deps)

    def register(self, tool: BrainTool) -> None:
        self._tools[tool.name] = tool

    def _register_tools(self, deps: CognitiveDependencies) -> None:
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

        # Knowledge tools
        if deps.knowledge.standards_query is not None:
            self.register(SearchStandardsTool(deps.knowledge.standards_query))
        if deps.knowledge.case_library is not None:
            self.register(SearchCasesTool(deps.knowledge.case_library))
        if deps.knowledge.process_knowledge is not None:
            self.register(SearchProcessTool(deps.knowledge.process_knowledge))

        # Vision tool — multimodal image analysis
        if deps.capability.llm_provider is not None:
            self.register(AnalyzeImageTool(deps.capability.llm_provider))

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

    def get_llm_tool_definitions(self) -> list[dict]:
        """Return tool definitions in LLM Function Calling format."""
        return [tool.to_function_definition() for tool in self._tools.values()]

    async def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        tool = self._tools.get(tool_name)
        if not tool:
            return ToolResult(error=f"Unknown tool: {tool_name}")
        return await tool.execute(**arguments)

    def get_tool(self, tool_name: str) -> BrainTool | None:
        return self._tools.get(tool_name)

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())
