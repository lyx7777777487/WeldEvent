"""SearchReasoningKnowledgeTool — 文本推理 RAG 检索工具。

检索工业流程/标准条款/推理模式/视觉问答模板，
让 LLM 在推理决策时能引用权威标准和经验模式。
"""

from __future__ import annotations

from typing import Any

from cognitiveplane.control.tools import BrainTool, ToolResult
from cognitiveplane.shared.dto.knowledge import ReasoningKnowledgeQuery
from cognitiveplane.shared.ports.knowledge import (
    ReasoningKnowledgeInput,
    ReasoningKnowledgePort,
)


class SearchReasoningKnowledgeTool(BrainTool):
    """文本推理知识库检索 — 工业流程/标准/推理模式 RAG。"""

    phase = 3
    always_available = True  # 基础工具层：RAG 知识检索豁免 skill 白名单

    def __init__(self, port: ReasoningKnowledgePort) -> None:
        self._port = port

    @property
    def name(self) -> str:
        return "search_reasoning_knowledge"

    @property
    def description(self) -> str:
        return (
            "检索工业推理知识库（文本推理 RAG）。\n"
            "**何时使用**：需要查焊接标准条款（ISO 5817/AWS D1.1/GB/T 19418等）、"
            "了解技术路线推理模式（异常检测/监督学习/传统CV/多模态融合）、"
            "参考视觉问答模板、或查阅工艺流程文档时。\n"
            "**用法**：传 query（自然语言描述问题），可选传 knowledge_type 过滤"
            "（standard/reasoning_pattern/vqa_template/process_doc）。"
            "返回匹配的知识条目列表。\n"
            "**示例**：search_reasoning_knowledge(query='焊缝气孔缺陷验收标准', knowledge_type='standard')"
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "自然语言描述的问题或场景",
                },
                "knowledge_type": {
                    "type": "string",
                    "enum": ["standard", "reasoning_pattern", "vqa_template", "process_doc"],
                    "description": "知识类型过滤（标准条款/推理模式/视觉问答模板/工艺流程文档）",
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
        query = ReasoningKnowledgeQuery(
            query_text=kwargs["query"],
            knowledge_type=kwargs.get("knowledge_type"),
            max_results=kwargs.get("max_results", 5),
        )
        try:
            output = await self._port.query(ReasoningKnowledgeInput(query=query))
            results = [r.model_dump() for r in output.results]
            return ToolResult(output={
                "results": results,
                "count": len(results),
                "message": f"检索到 {len(results)} 条相关知识" if results else "未找到匹配知识",
            })
        except Exception as e:
            return ToolResult(
                output={},
                error=f"推理知识库检索失败: {e}",
                error_type="state",
            )
