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

    def __init__(self, provider: WebSearchProvider) -> None:
        self._provider = provider

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web for up-to-date information. "
            "Use this when the internal knowledge base does not have the answer, "
            "or when you need the latest standards, research, or technical data. "
            "Returns a list of relevant web pages with titles, URLs, and snippets."
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
