"""Knowledge plane — RAG query service (spec §骨架 line 943).

Thin façade over `RAGQueryPort`: the spec assigns this module the role
of orchestrating RAG retrieval over the rest of the Knowledge plane
(standards/cases/process/equipment). The concrete vector adapters are
introduced in Phase 2e (Milvus); for now this delegates to whatever
`RAGQueryPort` adapter is wired in by `_build_knowledge`.
"""

from __future__ import annotations

from cognitiveplane.shared.dto_knowledge import KnowledgeResult, RAGQuery

from .ports import RAGQueryInput, RAGQueryPort


class RAGService:
    def __init__(self, port: RAGQueryPort) -> None:
        self._port = port

    async def query(self, query: RAGQuery) -> list[KnowledgeResult]:
        out = await self._port.query(RAGQueryInput(query=query))
        return list(out.results)


__all__ = ["RAGService"]
