"""WebSearchTool — search the web for real-time information.

Wraps WebSearchProvider (Tavily by default). Enables the LLM to
search for up-to-date standards, papers, and technical information
beyond the internal knowledge base.
"""

from cognitiveplane.control.tools import BrainTool, ToolResult
from cognitiveplane.capability.web_search import WebSearchProvider


class WebSearchTool(BrainTool):
    """Search the web for current information on welding, standards, and technical topics."""

    phase = 1
    always_available = True  # 知识检索层 always-on：豁免 skill 白名单，任何场景下 LLM 都可联网查证

    def __init__(self, provider: WebSearchProvider) -> None:
        self._provider = provider

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "联网搜索最新信息。\n"
            "**何时使用**：内部知识库查不到时，需要最新标准、技术资料或行业动态。\n"
            "**用法**：传 query（搜索关键词）。\n"
            "**约束**：一次查询拿不到结果就基于已有信息回答，不要换 query 反复搜。"
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query in natural language",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 5, max 10)",
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        query = kwargs.get("query", "")
        max_results = min(kwargs.get("max_results", 5), 10)
        response = await self._provider.search(query, max_results)
        if response.error:
            return ToolResult(error=response.error)
        results = [
            {"title": r.title, "url": r.url, "snippet": r.snippet}
            for r in response.results
        ]
        return ToolResult(output={"results": results, "query": query})
