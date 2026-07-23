"""Op 30: Pattern extraction + Reflexion note persistence + CBR retrieval.

Source: Reflexion (Shinn et al., NeurIPS 2023) + Case-Based Reasoning
        (Aamodt & Plaza, AI Communications 1994).

- PatternStore: accumulates recurring patterns from execution records
- CBRStore: case-based reasoning - retrieve similar past cases for new tasks
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("pattern_learning")


@dataclass
class Pattern:
    """Op 30: A recurring pattern extracted from execution history."""
    pattern_id: str
    description: str       # human-readable pattern description
    condition: str         # when this pattern applies
    action: str           # what to do
    occurrence_count: int = 1
    confidence: float = 0.0
    source_workflows: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=lambda: _time.time())

    def to_prompt_text(self) -> str:
        return (
            f"- 模式: {self.description}\n"
            f"  条件: {self.condition}\n"
            f"  动作: {self.action}\n"
            f"  置信度: {self.confidence:.0%} (出现 {self.occurrence_count} 次)"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "description": self.description,
            "condition": self.condition,
            "action": self.action,
            "occurrence_count": self.occurrence_count,
            "confidence": self.confidence,
            "source_workflows": self.source_workflows,
        }


@dataclass
class PatternMatch:
    """Result of matching a query against stored patterns."""
    pattern: Pattern
    score: float  # 0.0 - 1.0


class PatternStore:
    """Op 30: Accumulate and retrieve execution patterns.

    Patterns are extracted from WorkflowExecutionRecords (Op 29) and
    persisted as natural-language rules for future workflow design.
    """

    def __init__(self, min_occurrence: int = 2) -> None:
        self._patterns: dict[str, Pattern] = {}
        self._min_occurrence = min_occurrence
        self._counter = 0

    def add_or_reinforce(
        self,
        description: str,
        condition: str,
        action: str,
        workflow_id: str = "",
    ) -> Pattern:
        """Add a new pattern or reinforce an existing one."""
        # Check if similar pattern exists
        key = self._make_key(description, condition, action)
        if key in self._patterns:
            p = self._patterns[key]
            p.occurrence_count += 1
            if workflow_id and workflow_id not in p.source_workflows:
                p.source_workflows.append(workflow_id)
            p.confidence = min(1.0, p.occurrence_count / 10.0)
            return p
        else:
            self._counter += 1
            p = Pattern(
                pattern_id=f"pat_{self._counter}",
                description=description,
                condition=condition,
                action=action,
                occurrence_count=1,
                confidence=0.1,
                source_workflows=[workflow_id] if workflow_id else [],
            )
            self._patterns[key] = p
            return p

    def match(self, query: str, top_k: int = 3) -> list[PatternMatch]:
        """Match query against stored patterns using keyword overlap."""
        query_lower = query.lower()
        query_bigrams = {query_lower[i:i+2] for i in range(len(query_lower) - 1)}
        results: list[PatternMatch] = []

        for p in self._patterns.values():
            if p.occurrence_count < self._min_occurrence:
                continue
            # Bigram overlap
            desc_lower = (p.description + p.condition).lower()
            desc_bigrams = {desc_lower[i:i+2] for i in range(len(desc_lower) - 1)}
            overlap = len(query_bigrams & desc_bigrams)
            if overlap == 0:
                continue
            score = overlap / min(len(query_bigrams), len(desc_bigrams))
            # Boost by confidence
            score = score * (0.5 + 0.5 * p.confidence)
            results.append(PatternMatch(pattern=p, score=score))

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]

    def get_confident_patterns(self, min_confidence: float = 0.2) -> list[Pattern]:
        """Get all patterns above confidence threshold."""
        return [p for p in self._patterns.values() if p.confidence >= min_confidence]

    def to_prompt_text(self, max_patterns: int = 5) -> str:
        """Render confident patterns as prompt text for workflow design."""
        confident = self.get_confident_patterns()
        if not confident:
            return ""
        confident.sort(key=lambda p: p.confidence, reverse=True)
        lines = [f"## 基于历史经验的模式 ({len(confident)} 个)\n"]
        for p in confident[:max_patterns]:
            lines.append(p.to_prompt_text())
        return "\n".join(lines)

    @staticmethod
    def _make_key(desc: str, cond: str, action: str) -> str:
        return f"{desc[:50]}|{cond[:50]}|{action[:50]}".lower()

    @property
    def pattern_count(self) -> int:
        return len(self._patterns)


@dataclass
class CBRCase:
    """Op 30: A case for Case-Based Reasoning."""
    case_id: str
    problem: str             # original goal/problem
    solution: str            # workflow design that solved it
    outcome: str             # "success" | "partial" | "failure"
    quality_score: float = 0.0
    spec_summary: dict[str, Any] = field(default_factory=dict)
    reflexion_note: str = ""
    timestamp: float = field(default_factory=lambda: _time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "problem": self.problem,
            "solution": self.solution,
            "outcome": self.outcome,
            "quality_score": self.quality_score,
            "reflexion_note": self.reflexion_note,
        }


class CBRStore:
    """Op 30: Case-Based Reasoning store.

    Source: Case-Based Reasoning (Aamodt & Plaza, 1994).

    Stores past cases and retrieves similar ones for new problems.
    When designing a new workflow, the system can retrieve past similar
    cases and their solutions as reference.
    """

    def __init__(self) -> None:
        self._cases: list[CBRCase] = []
        self._counter = 0

    def add_case(self, problem: str, solution: str, outcome: str,
                 quality_score: float = 0.0, spec_summary: dict | None = None,
                 reflexion_note: str = "") -> CBRCase:
        self._counter += 1
        case = CBRCase(
            case_id=f"case_{self._counter}",
            problem=problem,
            solution=solution,
            outcome=outcome,
            quality_score=quality_score,
            spec_summary=spec_summary or {},
            reflexion_note=reflexion_note,
        )
        self._cases.append(case)
        return case

    def retrieve_similar(self, problem: str, top_k: int = 3) -> list[CBRCase]:
        """Retrieve similar cases using character bigram overlap."""
        if not self._cases:
            return []

        problem_lower = problem.lower()
        problem_bigrams = {problem_lower[i:i+2] for i in range(len(problem_lower) - 1)}

        scored: list[tuple[float, CBRCase]] = []
        for case in self._cases:
            case_lower = case.problem.lower()
            case_bigrams = {case_lower[i:i+2] for i in range(len(case_lower) - 1)}
            overlap = len(problem_bigrams & case_bigrams)
            if overlap > 0:
                score = overlap / min(len(problem_bigrams), len(case_bigrams))
                # Prefer successful cases
                if case.outcome == "success":
                    score += 0.1
                if case.quality_score > 0.7:
                    score += 0.05
                scored.append((score, case))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:top_k]]

    def to_prompt_text(self, problem: str, max_cases: int = 2) -> str:
        """Render similar cases as prompt text for workflow design."""
        similar = self.retrieve_similar(problem, top_k=max_cases)
        if not similar:
            return ""
        lines = [f"## 相似历史案例 ({len(similar)} 个)\n"]
        for case in similar:
            lines.append(f"### 案例: {case.problem}")
            lines.append(f"**方案**: {case.solution}")
            lines.append(f"**结果**: {case.outcome} (质量: {case.quality_score:.0%})")
            if case.reflexion_note:
                lines.append(f"**教训**: {case.reflexion_note}")
            lines.append("")
        return "\n".join(lines)

    @property
    def case_count(self) -> int:
        return len(self._cases)


__all__ = ["Pattern", "PatternMatch", "PatternStore", "CBRCase", "CBRStore"]
