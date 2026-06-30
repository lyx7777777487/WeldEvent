"""SearchStandardsTool — query welding standards and parameters.

Source: 7-plane redesign spec §7 tools.
"""

from cognitiveplane.control.tools import BrainTool, ToolResult
from cognitiveplane.shared.ports.knowledge import StandardsQueryPort


class SearchStandardsTool(BrainTool):
    """Query welding standards (NB/T47014, ISO 3834, etc.)."""

    phase = 3

    def __init__(self, port: StandardsQueryPort) -> None:
        self._port = port

    @property
    def name(self) -> str:
        return "search_standards"

    @property
    def description(self) -> str:
        return (
            "Search welding standards and specifications. "
            "Returns applicable standard clauses, parameter ranges, and requirements."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "standard_id": {
                    "type": "string",
                    "description": "Standard identifier (e.g. NB/T47014, ISO-3834)",
                },
                "section": {
                    "type": "string",
                    "description": "Section or clause number within the standard",
                },
                "query": {
                    "type": "string",
                    "description": "Natural language query about standard requirements",
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        from cognitiveplane.shared.ports.knowledge import StandardsQueryInput
        from cognitiveplane.shared.dto.knowledge import StandardsQuery

        query = StandardsQuery(
            standard_id=kwargs.get("standard_id"),
            keyword=kwargs.get("query"),
            section=kwargs.get("section"),
        )
        try:
            output = await self._port.query(StandardsQueryInput(query=query))
            results = [r.model_dump() for r in output.results]
            return ToolResult(output={"results": results})
        except Exception as e:
            return ToolResult(error=str(e))
