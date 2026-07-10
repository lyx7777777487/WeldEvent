"""SearchCasesTool — query case library for similar historical cases."""

from cognitiveplane.control.tools import BrainTool, ToolResult
from cognitiveplane.shared.ports.knowledge import CaseLibraryQueryPort


class SearchCasesTool(BrainTool):
    """Search historical case library for similar defect cases."""

    phase = 3
    always_available = True  # 基础工具层：知识检索豁免 skill 白名单

    def __init__(self, port: CaseLibraryQueryPort) -> None:
        self._port = port

    @property
    def name(self) -> str:
        return "search_cases"

    @property
    def description(self) -> str:
        return (
            "搜索历史案例库，查找相似案例及处理方案。\n"
            "**何时使用**：需要参考历史经验、了解类似缺陷的处理方式、"
            "或在设计工作流前了解常见做法。\n"
            "**用法**：传 defect_type（缺陷类型）和可选的 material/thickness 等筛选条件。"
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "defect_type": {
                    "type": "string",
                    "description": "Type of defect (porosity, slag, crack, etc.)",
                },
                "material": {
                    "type": "string",
                    "description": "Material type (e.g. Q345R)",
                },
                "similarity_context": {
                    "type": "object",
                    "description": "Context dict for similarity matching",
                },
                "query": {
                    "type": "string",
                    "description": "Natural language query about historical cases",
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        from cognitiveplane.shared.ports.knowledge import CaseLibraryQueryInput
        from cognitiveplane.shared.dto.knowledge import CaseLibraryQuery

        query = CaseLibraryQuery(
            defect_type=kwargs.get("defect_type") or kwargs.get("query"),
            similarity_context=kwargs.get("similarity_context"),
        )
        try:
            output = await self._port.query(CaseLibraryQueryInput(query=query))
            results = [r.model_dump() for r in output.results]
            return ToolResult(output={"results": results})
        except Exception as e:
            return ToolResult(error=str(e))
