"""SearchVisionKnowledgeTool — 视觉理解 RAG 检索工具。

检索工业图像数据集元信息（MVTec AD / RIAWELC / SWRD 等），
让 LLM 在规划视觉任务时能推荐合适的数据集。
"""

from __future__ import annotations

from typing import Any

from cognitiveplane.control.tools import BrainTool, ToolResult
from cognitiveplane.shared.dto.knowledge import VisionKnowledgeQuery
from cognitiveplane.shared.ports.knowledge import (
    VisionKnowledgeInput,
    VisionKnowledgePort,
)


class SearchVisionKnowledgeTool(BrainTool):
    """视觉理解知识库检索 — 工业图像数据集元信息 RAG。"""

    phase = 3
    always_available = True  # 基础工具层：RAG 知识检索豁免 skill 白名单

    def __init__(self, port: VisionKnowledgePort) -> None:
        self._port = port

    @property
    def name(self) -> str:
        return "search_vision_knowledge"

    @property
    def description(self) -> str:
        return (
            "检索工业图像数据集知识库（视觉理解 RAG）— 查询公开工业图像数据集的元信息。\n"
            "**何时使用**：当用户想找数据集、了解有哪些公开数据集、查询数据集规模/缺陷类型/"
            "适用场景/下载链接时，**必须优先使用此工具**，不要用 delegate 或 web_search。\n"
            "**用法**：传 query（自然语言描述需求），可选传 defect_type/industry/modality 过滤。\n"
            "**覆盖范围**：14 个工业图像数据集 — MVTec AD/VisA/Real-IAD/GC10-DET/NEU/"
            "RIAWELC(24407张X光焊缝)/SWRD(3600+张T型接头焊缝)/WDXI(13766张)/GDXray 等。\n"
            "**返回**：数据集名称/描述/下载URL/规模/缺陷类型/成像方式/行业/适用场景/许可证。\n"
            "**示例**：search_vision_knowledge(query='焊缝X光缺陷检测数据集')"
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "自然语言描述你的需求或场景",
                },
                "defect_type": {
                    "type": "string",
                    "description": "缺陷类型过滤（如 气孔/未熔合/裂纹）",
                },
                "industry": {
                    "type": "string",
                    "description": "行业过滤（如 焊接/钢材/电子）",
                },
                "modality": {
                    "type": "string",
                    "description": "成像方式过滤（如 RGB/X-ray）",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大返回条数（默认5）",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        query = VisionKnowledgeQuery(
            query_text=kwargs["query"],
            defect_type=kwargs.get("defect_type"),
            industry=kwargs.get("industry"),
            modality=kwargs.get("modality"),
            max_results=kwargs.get("max_results", 5),
        )
        try:
            output = await self._port.query(VisionKnowledgeInput(query=query))
            results = [r.model_dump() for r in output.results]
            return ToolResult(output={
                "results": results,
                "count": len(results),
                "message": f"检索到 {len(results)} 个相关数据集" if results else "未找到匹配数据集",
            })
        except Exception as e:
            return ToolResult(
                output={},
                error=f"视觉知识库检索失败: {e}",
                error_type="state",
            )
