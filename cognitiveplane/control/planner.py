"""Planner — self-built task decomposition (spec §5 line 1006).

Produces a Plan (steps + expected_outcome + confidence) from an objective +
context. Memory matches and knowledge results are mixed in as constraint
hints. This is a deterministic, LLM-free baseline planner — Phase 2 may swap
in DeepAgents/LangGraph Planner.
"""

from __future__ import annotations

from dataclasses import dataclass

from cognitiveplane.shared.dto.context import ContextSnapshot
from cognitiveplane.shared.dto.deepagents import Plan, Strategy
from cognitiveplane.shared.dto_decision.outputs import Constraint
from cognitiveplane.shared.dto.knowledge import KnowledgeResult
from cognitiveplane.shared.dto.memory import MemorySearchResult


@dataclass
class PlannerInput:
    context: ContextSnapshot
    objective: str
    constraints: list[Constraint]
    available_strategies: list[Strategy]
    knowledge_results: list[KnowledgeResult]
    memory_results: list[MemorySearchResult]


@dataclass
class PlannerOutput:
    plan: Plan
    alternative_plans: list[Plan]
    confidence: float


class Planner:
    """Default deterministic planner.

    Strategy:
    1. Decompose objective into 3-5 steps (analyze → propose → validate → publish).
    2. If memory matches exist, prepend "review prior cases" step.
    3. If knowledge results exist, append "verify against standards" step.
    4. Confidence scales with available evidence.
    """

    @staticmethod
    def plan(inp: PlannerInput) -> PlannerOutput:
        steps: list[str] = []
        if inp.memory_results:
            steps.append(
                f"Review {min(len(inp.memory_results), 3)} similar prior cases"
            )
        steps.append(f"Analyze current case context: {inp.context.case_id.value}")
        steps.append(f"Propose initial solution for objective: {inp.objective[:100]}")
        if inp.knowledge_results:
            steps.append("Verify against retrieved standards")
        if inp.constraints:
            steps.append(
                f"Apply {len(inp.constraints)} active constraints"
            )
        steps.append("Validate via governance pipeline")
        steps.append("Publish decision and notify operator")

        outcome = (
            f"Decision package addressing: {inp.objective[:140]}"
            if inp.objective
            else "Decision package"
        )

        evidence_score = min(
            1.0,
            0.4
            + 0.1 * min(len(inp.memory_results), 3)
            + 0.1 * min(len(inp.knowledge_results), 3),
        )

        primary = Plan(
            steps=steps,
            expected_outcome=outcome,
            confidence=evidence_score,
        )

        alternatives: list[Plan] = []
        if inp.available_strategies:
            for strategy in inp.available_strategies[:2]:
                alternatives.append(
                    Plan(
                        steps=[
                            f"Apply strategy: {strategy.name}",
                            f"Execute under: {', '.join(strategy.applicable_conditions[:3]) or 'default'}",
                            "Validate and publish",
                        ],
                        expected_outcome=outcome,
                        confidence=max(0.4, evidence_score - 0.1),
                    )
                )

        return PlannerOutput(
            plan=primary,
            alternative_plans=alternatives,
            confidence=evidence_score,
        )
