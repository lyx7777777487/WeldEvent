"""SearchCasesTool — query case library for similar historical cases."""

from cognitiveplane.control.tools import BrainTool, ToolResult
from cognitiveplane.shared.ports.knowledge import CaseLibraryQueryPort


class SearchCasesTool(BrainTool):
    """Search historical case library for similar defect cases."""

    phase = 3

    def __init__(self, port: CaseLibraryQueryPort) -> None:
        self._port = port

    @property
    def name(self) -> str:
        return "search_cases"

    @property
    def description(self) -> str:
        return (
            "Search the case library for similar historical inspection cases. "
            "Returns case details, resolutions, and outcomes."
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
