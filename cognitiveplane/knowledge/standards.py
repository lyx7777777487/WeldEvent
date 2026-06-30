"""Knowledge plane — Standards query service (spec §骨架 line 944)."""

from __future__ import annotations

from cognitiveplane.shared.dto.knowledge import StandardsQuery, StandardsResult

from .ports import StandardsQueryInput, StandardsQueryPort


class StandardsService:
    def __init__(self, port: StandardsQueryPort) -> None:
        self._port = port

    async def query(self, query: StandardsQuery) -> list[StandardsResult]:
        out = await self._port.query(StandardsQueryInput(query=query))
        return list(out.results)


__all__ = ["StandardsService"]
