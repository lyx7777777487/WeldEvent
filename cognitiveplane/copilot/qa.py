"""CopilotQA — Standard / process / case Q&A (spec §11).

Routes a natural-language query through the Knowledge ports (RAG, standards,
process, cases) and synthesizes a single textual answer using LLMProvider.
LLM is optional — if not provided, a deterministic baseline summary is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from cognitiveplane.capability.provider import LLMProvider, LLMRequest
from cognitiveplane.shared.dto_knowledge import (
    CaseLibraryQuery,
    CaseLibraryResult,
    KnowledgeResult,
    ProcessKnowledgeQuery,
    ProcessKnowledgeResult,
    RAGQuery,
    StandardsQuery,
    StandardsResult,
)
from cognitiveplane.shared.ports.knowledge import (
    CaseLibraryQueryInput,
    CaseLibraryQueryPort,
    ProcessKnowledgeInput,
    ProcessKnowledgePort,
    RAGQueryInput,
    RAGQueryPort,
    StandardsQueryInput,
    StandardsQueryPort,
)


@dataclass
class QAQuery:
    text: str
    include_standards: bool = True
    include_process: bool = True
    include_cases: bool = True
    max_per_source: int = 3


@dataclass
class QAResponse:
    answer: str
    rag_results: list[KnowledgeResult] = field(default_factory=list)
    standards: list[StandardsResult] = field(default_factory=list)
    processes: list[ProcessKnowledgeResult] = field(default_factory=list)
    cases: list[CaseLibraryResult] = field(default_factory=list)
    confidence: float = 0.0


class CopilotQA:
    def __init__(
        self,
        rag: RAGQueryPort,
        standards: StandardsQueryPort,
        process: ProcessKnowledgePort,
        cases: CaseLibraryQueryPort,
        llm: Optional[LLMProvider] = None,
    ) -> None:
        self._rag = rag
        self._standards = standards
        self._process = process
        self._cases = cases
        self._llm = llm

    async def ask(self, query: QAQuery) -> QAResponse:
        rag_out = await self._rag.query(
            RAGQueryInput(
                query=RAGQuery(query_text=query.text, max_results=query.max_per_source)
            )
        )
        standards: list[StandardsResult] = []
        if query.include_standards:
            std_out = await self._standards.query(
                StandardsQueryInput(query=StandardsQuery(keyword=query.text))
            )
            standards = std_out.results[: query.max_per_source]
        processes: list[ProcessKnowledgeResult] = []
        if query.include_process:
            proc_out = await self._process.query(
                ProcessKnowledgeInput(query=ProcessKnowledgeQuery())
            )
            processes = proc_out.results[: query.max_per_source]
        cases: list[CaseLibraryResult] = []
        if query.include_cases:
            case_out = await self._cases.query(
                CaseLibraryQueryInput(
                    query=CaseLibraryQuery(max_results=query.max_per_source)
                )
            )
            cases = case_out.results

        rag_results = rag_out.results
        confidence = self._compute_confidence(rag_results, standards, cases)
        answer = await self._synthesize(
            query.text, rag_results, standards, processes, cases
        )
        return QAResponse(
            answer=answer,
            rag_results=rag_results,
            standards=standards,
            processes=processes,
            cases=cases,
            confidence=confidence,
        )

    @staticmethod
    def _compute_confidence(
        rag: list[KnowledgeResult],
        standards: list[StandardsResult],
        cases: list[CaseLibraryResult],
    ) -> float:
        scores: list[float] = []
        scores.extend(r.relevance_score for r in rag)
        scores.extend(s.relevance for s in standards)
        scores.extend(c.similarity_score for c in cases)
        if not scores:
            return 0.0
        return min(1.0, sum(scores) / len(scores))

    async def _synthesize(
        self,
        question: str,
        rag: list[KnowledgeResult],
        standards: list[StandardsResult],
        processes: list[ProcessKnowledgeResult],
        cases: list[CaseLibraryResult],
    ) -> str:
        if self._llm is not None:
            try:
                bullets = self._format_evidence(rag, standards, processes, cases)
                req = LLMRequest(
                    messages=[
                        {
                            "role": "system",
                            "content": "You are an industrial weld-inspection copilot. Answer concisely using only the supplied evidence.",
                        },
                        {
                            "role": "user",
                            "content": f"Question: {question}\n\nEvidence:\n{bullets}",
                        },
                    ],
                    caller="CopilotQA",
                    purpose="qa_synthesis",
                )
                resp = await self._llm.complete(req)
                if resp.content:
                    return resp.content
            except Exception:
                # fall back to deterministic baseline
                pass
        return self._baseline_answer(question, rag, standards, processes, cases)

    @staticmethod
    def _format_evidence(
        rag: list[KnowledgeResult],
        standards: list[StandardsResult],
        processes: list[ProcessKnowledgeResult],
        cases: list[CaseLibraryResult],
    ) -> str:
        lines: list[str] = []
        for r in rag:
            lines.append(f"- [RAG] {r.content} (score={r.relevance_score:.2f})")
        for s in standards:
            lines.append(f"- [STD {s.standard_id} §{s.section}] {s.text}")
        for p in processes:
            lines.append(f"- [PROC {p.process_id}] criteria={p.quality_criteria}")
        for c in cases:
            lines.append(
                f"- [CASE {c.case_id}] {c.defect_description} → {c.resolution}"
            )
        return "\n".join(lines) or "(no evidence)"

    @staticmethod
    def _baseline_answer(
        question: str,
        rag: list[KnowledgeResult],
        standards: list[StandardsResult],
        processes: list[ProcessKnowledgeResult],
        cases: list[CaseLibraryResult],
    ) -> str:
        parts: list[str] = [f"Q: {question[:200]}"]
        if rag:
            parts.append(f"RAG matches: {len(rag)} (top={rag[0].content[:120]})")
        if standards:
            parts.append(
                f"Standards: {standards[0].standard_id} §{standards[0].section}"
            )
        if processes:
            parts.append(f"Processes: {processes[0].process_id}")
        if cases:
            parts.append(
                f"Cases: {cases[0].case_id} → {cases[0].resolution[:120]}"
            )
        if len(parts) == 1:
            parts.append("No evidence retrieved; please rephrase or expand the query.")
        return " | ".join(parts)
