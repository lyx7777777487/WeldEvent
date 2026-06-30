"""CopilotInvestigate — Anomaly root cause analysis (spec §11).

⚠️ HISTORICAL LEGACY — not wired into running app (2026-06-26 audit).
Phase 4/5 rewrite as /chat preset, do not extend. See copilot/__init__.py.

Given an anomaly description (defect type / symptoms / case context), runs a
coordinated retrieval over Knowledge (similar cases + process knowledge) and
Memory (prior matched cases) to surface candidate root causes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from cognitiveplane.capability.provider import LLMProvider, LLMRequest
from cognitiveplane.shared.dto.knowledge import (
    CaseLibraryQuery,
    CaseLibraryResult,
    ProcessKnowledgeQuery,
    ProcessKnowledgeResult,
)
from cognitiveplane.shared.dto.memory import MemorySearchQuery, MemorySearchResult
from cognitiveplane.shared.ports.knowledge import (
    CaseLibraryQueryInput,
    CaseLibraryQueryPort,
    ProcessKnowledgeInput,
    ProcessKnowledgePort,
)
from cognitiveplane.shared.ports.memory import (
    MemorySearchInput,
    MemorySearchPort,
)


@dataclass
class CandidateCause:
    description: str
    likelihood: float
    evidence: list[str] = field(default_factory=list)


@dataclass
class InvestigationResult:
    summary: str
    candidate_causes: list[CandidateCause]
    similar_cases: list[CaseLibraryResult]
    process_matches: list[ProcessKnowledgeResult]
    memory_matches: list[MemorySearchResult]


class CopilotInvestigate:
    def __init__(
        self,
        cases: CaseLibraryQueryPort,
        process: ProcessKnowledgePort,
        memory: MemorySearchPort,
        llm: Optional[LLMProvider] = None,
    ) -> None:
        self._cases = cases
        self._process = process
        self._memory = memory
        self._llm = llm

    async def investigate(
        self,
        defect_description: str,
        defect_type: Optional[str] = None,
        max_results: int = 5,
    ) -> InvestigationResult:
        case_out = await self._cases.query(
            CaseLibraryQueryInput(
                query=CaseLibraryQuery(
                    defect_type=defect_type, max_results=max_results
                )
            )
        )
        proc_out = await self._process.query(
            ProcessKnowledgeInput(query=ProcessKnowledgeQuery())
        )
        mem_out = await self._memory.search(
            MemorySearchInput(
                query=MemorySearchQuery(
                    case_features={"defect_description": defect_description},
                    feature_vector=[],
                    max_results=max_results,
                )
            )
        )

        candidates = self._derive_candidates(
            case_out.results, proc_out.results, mem_out.results
        )
        summary = await self._summarize(
            defect_description,
            defect_type,
            candidates,
            case_out.results,
            mem_out.results,
        )
        return InvestigationResult(
            summary=summary,
            candidate_causes=candidates,
            similar_cases=case_out.results,
            process_matches=proc_out.results,
            memory_matches=mem_out.results,
        )

    @staticmethod
    def _derive_candidates(
        cases: list[CaseLibraryResult],
        processes: list[ProcessKnowledgeResult],
        memories: list[MemorySearchResult],
    ) -> list[CandidateCause]:
        out: list[CandidateCause] = []
        for c in cases[:3]:
            out.append(
                CandidateCause(
                    description=c.defect_description,
                    likelihood=max(0.0, min(1.0, c.similarity_score)),
                    evidence=[f"case:{c.case_id}", f"resolution:{c.resolution[:80]}"],
                )
            )
        for p in processes[:2]:
            for defect in (p.common_defects or [])[:2]:
                out.append(
                    CandidateCause(
                        description=defect,
                        likelihood=0.4,
                        evidence=[f"process:{p.process_id}"],
                    )
                )
        for m in memories[:2]:
            out.append(
                CandidateCause(
                    description=f"prior memory match (id={m.memory_id.value})",
                    likelihood=max(0.0, min(1.0, m.similarity_score)),
                    evidence=[f"memory:{m.memory_id.value}"],
                )
            )
        out.sort(key=lambda x: x.likelihood, reverse=True)
        return out[:5]

    async def _summarize(
        self,
        defect_description: str,
        defect_type: Optional[str],
        candidates: list[CandidateCause],
        cases: list[CaseLibraryResult],
        memories: list[MemorySearchResult],
    ) -> str:
        baseline = self._baseline_summary(
            defect_description, defect_type, candidates, cases, memories
        )
        if self._llm is None:
            return baseline
        try:
            req = LLMRequest(
                messages=[
                    {
                        "role": "system",
                        "content": "You are an industrial weld investigation copilot. Rephrase the structured findings into a concise root-cause hypothesis.",
                    },
                    {"role": "user", "content": baseline},
                ],
                caller="CopilotInvestigate",
                purpose="root_cause",
            )
            resp = await self._llm.complete(req)
            return resp.content or baseline
        except Exception:
            return baseline

    @staticmethod
    def _baseline_summary(
        defect_description: str,
        defect_type: Optional[str],
        candidates: list[CandidateCause],
        cases: list[CaseLibraryResult],
        memories: list[MemorySearchResult],
    ) -> str:
        parts: list[str] = [
            f"Anomaly: {defect_description[:200]}"
            + (f" (type={defect_type})" if defect_type else "")
        ]
        if candidates:
            top = candidates[0]
            parts.append(
                f"Top candidate: {top.description[:200]} (likelihood={top.likelihood:.2f})"
            )
        parts.append(
            f"Similar cases={len(cases)}, memory matches={len(memories)}"
        )
        return " | ".join(parts)
