"""Knowledge plane — Process knowledge service (spec §骨架 line 946)."""

from __future__ import annotations

from cognitiveplane.shared.dto_knowledge import (
    ProcessKnowledgeQuery,
    ProcessKnowledgeResult,
)

from .ports import ProcessKnowledgeInput, ProcessKnowledgePort


class ProcessKnowledgeService:
    def __init__(self, port: ProcessKnowledgePort) -> None:
        self._port = port

    async def query(
        self, query: ProcessKnowledgeQuery
    ) -> list[ProcessKnowledgeResult]:
        out = await self._port.query(ProcessKnowledgeInput(query=query))
        return list(out.results)


__all__ = ["ProcessKnowledgeService"]
