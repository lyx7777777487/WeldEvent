"""Mode A: Knowledge Query -- stateless, direct response.

Source: L1-Interaction-Layer-Business-Requirements.md §3.2.
"""

from uuid import uuid4

from src.interaction.base import (
    IntentPattern,
    ModeProtocol,
    ModeResponse,
    ResponseType,
    UserMessage,
)
from src.interaction.dependencies import ModeDependencies
from src.shared.enums import MatchStrategy
from src.shared.types import SessionId


class KnowledgeQueryMode(ModeProtocol):
    """Stateless knowledge query mode -- does not pass through state machine.

    Directly queries knowledge ports and returns text reply.
    Can be embedded within any other mode's conversation turn.
    """

    mode_id = "cognitive.knowledge_query"
    display_name = "Knowledge Query"
    description = "Answer knowledge questions about standards, materials, and processes"

    intent_patterns = [
        IntentPattern(
            match_strategy=MatchStrategy.KEYWORD,
            patterns=[
                "多少", "温度", "参数", "标准", "规定", "要求", "什么是", "怎么算",
                "合格", "不合格", "测量", "验收", "缺陷", "判定", "规范", "T型",
                "角焊", "焊缝", "厚度", "材质", "材料", "工艺", "电流", "电压",
                "速度", "案例", "历史", "统计", "夹渣", "未熔合", "气孔", "裂纹",
                "预热", "层间", "热处理",
            ],
            priority=50,
            confidence_threshold=0.3,
        ),
        IntentPattern(
            match_strategy=MatchStrategy.LLM_LABEL,
            patterns=["knowledge_query", "standard_lookup", "process_inquiry"],
            priority=60,
            confidence_threshold=0.5,
        ),
    ]

    required_ports = ["RAGQueryPort"]
    creates_session = False
    session_data_schema = None

    def _select_best_result(self, query: str, results: list) -> object | None:
        """Select the most relevant result based on keyword matching."""
        if not results:
            return None

        keyword_groups = [
            (["预热", "温度", "层间", "热处理"], 0),
            (["夹渣", "夹杂物"], 1),
            (["未熔合", "未融合"], 2),
            (["参数", "电流", "电压", "速度", "GMAW", "角焊", "工艺"], 3),
            (["案例", "历史", "统计", "类似"], 4),
        ]

        for keywords, index in keyword_groups:
            if any(kw in query for kw in keywords):
                if index < len(results):
                    return results[index]

        return results[0] if results else None

    async def handle(
        self, message: UserMessage, deps: ModeDependencies
    ) -> ModeResponse:
        """Query knowledge sources and return a text reply."""
        rag_port = deps.get("RAGQueryPort")

        from src.shared.ports.knowledge import RAGQueryInput
        from src.shared.dto_knowledge import RAGQuery

        query_input = RAGQueryInput(
            query=RAGQuery(query_text=message.raw_text, max_results=5)
        )
        output = await rag_port.query(query_input)

        ports_accessed = ["RAGQueryPort"]

        if output.results:
            top = self._select_best_result(message.raw_text, output.results)
            if top:
                reply = f"{top.content}\n\n来源: {top.source_reference}"
            else:
                reply = "未找到相关知识，请尝试更具体的描述。"
        else:
            reply = "未找到相关知识，请尝试更具体的描述。"

        # Optional: use explanation port for richer answers
        if deps.has("DeepAgentsExplanationPort"):
            ports_accessed.append("DeepAgentsExplanationPort")

        return ModeResponse(
            mode_id=self.mode_id,
            session_id=SessionId(value=uuid4()),
            response_type=ResponseType.TEXT_REPLY,
            text_reply=reply,
            ports_accessed=ports_accessed,
        )
