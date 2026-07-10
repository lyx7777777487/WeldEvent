"""SearchProcessTool — query process knowledge for welding parameters."""

from cognitiveplane.control.tools import BrainTool, ToolResult
from cognitiveplane.shared.ports.knowledge import ProcessKnowledgePort


class SearchProcessTool(BrainTool):
    """Search process knowledge for recommended welding parameters."""

    phase = 4
    always_available = True  # 基础工具层：工艺知识检索豁免 skill 白名单

    def __init__(self, port: ProcessKnowledgePort) -> None:
        self._port = port

    @property
    def name(self) -> str:
        return "search_process"

    @property
    def description(self) -> str:
        return (
            "查询工艺知识库，获取推荐参数和质量标准。\n"
            "**何时使用**：用户询问工艺参数（电流/电压/速度/预热等）、"
            "质量判据、或常见缺陷模式。\n"
            "**用法**：传 process_type（工艺类型如 GMAW/GTAW/SMAW）和可选的 material。"
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "process_type": {
                    "type": "string",
                    "description": "Welding process type (GMAW, GTAW, SMAW, etc.)",
                },
                "material": {
                    "type": "string",
                    "description": "Material type (e.g. Q345R, 304SS)",
                },
                "joint_type": {
                    "type": "string",
                    "description": "Joint type (butt, fillet, T-joint, etc.)",
                },
                "query": {
                    "type": "string",
                    "description": "Natural language query about process parameters",
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        from cognitiveplane.shared.ports.knowledge import ProcessKnowledgeInput
        from cognitiveplane.shared.dto.knowledge import ProcessKnowledgeQuery

        query = ProcessKnowledgeQuery(
            process_type=kwargs.get("process_type"),
            material=kwargs.get("material"),
            joint_type=kwargs.get("joint_type"),
        )
        try:
            output = await self._port.query(ProcessKnowledgeInput(query=query))
            results = [r.model_dump() for r in output.results]
            return ToolResult(output={"results": results})
        except Exception as e:
            return ToolResult(error=str(e))
