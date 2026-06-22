"""Knowledge plane — Case library service (spec §骨架 line 945)."""

from __future__ import annotations

from cognitiveplane.shared.dto_knowledge import CaseLibraryQuery, CaseLibraryResult

from .ports import CaseLibraryQueryInput, CaseLibraryQueryPort


class CaseLibraryService:
    def __init__(self, port: CaseLibraryQueryPort) -> None:
        self._port = port

    async def query(self, query: CaseLibraryQuery) -> list[CaseLibraryResult]:
        out = await self._port.query(CaseLibraryQueryInput(query=query))
        return list(out.results)


__all__ = ["CaseLibraryService"]
