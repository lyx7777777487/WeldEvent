"""Advanced planner techniques - Op 31/32/33/35.

Op 31: ReWOO batch planning (arXiv:2310.18323)
Op 32: Reflexion self-reflection loop (NeurIPS 2023, arXiv:2303.11366)
Op 33: Self-Refine iterative improvement (NeurIPS 2023, arXiv:2303.17651)
Op 35: Parallel specialist (Anthropic orchestrator-workers)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from cognitiveplane.control.planner.ledger import (
    MagenticPlanner, ProgressLedger, SpecialistRole, TaskItem, TaskStatus,
)


# ── Op 31: ReWOO batch planning ──────────────────────────────

@dataclass
class ReWOOPlan:
    """Op 31: ReWOO batch plan - separate reasoning from observation.

    Source: ReWOO (Xu et al., 2023, arXiv:2310.18323).

    Instead of interleaving reasoning and tool calls (ReAct), ReWOO:
    1. Plans ALL tool calls upfront in a single reasoning step
    2. Executes them in batch (potentially in parallel)
    3. Combines results in a final reasoning step

    This reduces LLM call count significantly (~60% in paper benchmarks)
    for tasks where the tool sequence is predictable.
    """
    steps: list[dict[str, Any]] = field(default_factory=list)
    # Each step: {"step_id": str, "tool": str, "args": dict, "depends_on": list[str]}

    def add_step(
        self,
        step_id: str,
        tool: str,
        args: dict[str, Any],
        depends_on: list[str] | None = None,
    ) -> None:
        self.steps.append({
            "step_id": step_id,
            "tool": tool,
            "args": args,
            "depends_on": depends_on or [],
        })

    def get_execution_order(self) -> list[list[dict[str, Any]]]:
        """Return steps grouped by dependency level (for parallel execution).

        Steps in the same group have no dependencies on each other and can
        be executed in parallel.
        """
        if not self.steps:
            return []

        executed: set[str] = set()
        groups: list[list[dict[str, Any]]] = []

        remaining = list(self.steps)
        while remaining:
            group = []
            for step in remaining:
                deps = step["depends_on"]
                if all(d in executed for d in deps):
                    group.append(step)
            if not group:
                # Circular dependency or unresolvable
                break
            groups.append(group)
            for step in group:
                executed.add(step["step_id"])
            remaining = [s for s in remaining if s["step_id"] not in executed]

        return groups


# ── Op 32: Reflexion ─────────────────────────────────────────

@dataclass
class ReflexionMemory:
    """Op 32: Reflexion - text-based memory of failure lessons.

    Source: Reflexion (Shinn et al., NeurIPS 2023, arXiv:2303.11366).

    After a failure, generate a natural-language "reflexion note" that
    captures what went wrong and how to avoid it. This note is injected
    into the next attempt's context.

    Unlike ML-based learning, Reflexion uses the LLM's own ability to
    summarize and generalize from failures in natural language.
    """
    notes: list[dict[str, str]] = field(default_factory=list)
    # Each note: {"task": str, "failure": str, "lesson": str}

    def add_note(self, task: str, failure: str, lesson: str) -> None:
        self.notes.append({
            "task": task,
            "failure": failure[:500],
            "lesson": lesson[:500],
        })

    def get_notes_for_task(self, task: str) -> list[str]:
        """Get relevant reflexion notes for a task.

        Uses character-level bigram overlap for CJK text, plus
        substring matching for Latin text.
        """
        task_lower = task.lower()
        results = []
        for n in self.notes:
            note_task_lower = n["task"].lower()
            # Substring match in either direction
            if note_task_lower in task_lower or task_lower in note_task_lower:
                results.append(n["lesson"])
                continue
            # Character bigram overlap (works for CJK + mixed text)
            task_bigrams = {task_lower[i:i+2] for i in range(len(task_lower) - 1)}
            note_bigrams = {note_task_lower[i:i+2] for i in range(len(note_task_lower) - 1)}
            if task_bigrams & note_bigrams:
                results.append(n["lesson"])
        return results

    def to_prompt_text(self, max_notes: int = 3) -> str:
        """Render notes as text for prompt injection."""
        if not self.notes:
            return ""
        recent = self.notes[-max_notes:]
        lines = ["## 失败教训 (Reflexion)\n"]
        for n in recent:
            lines.append(f"- **任务**: {n['task']}")
            lines.append(f"  **失败**: {n['failure']}")
            lines.append(f"  **教训**: {n['lesson']}\n")
        return "\n".join(lines)


# ── Op 33: Self-Refine ───────────────────────────────────────

@dataclass
class SelfRefineCycle:
    """Op 33: Self-Refine - iterative plan improvement.

    Source: Self-Refine (Madaan et al., NeurIPS 2023, arXiv:2303.17651).

    Generate -> Feedback -> Refine loop:
    1. Generate initial draft
    2. LLM provides feedback (as Critic specialist)
    3. LLM refines based on feedback
    4. Repeat until quality threshold or max iterations

    Used for PlanDraft improvement before workflow execution.
    """
    draft: str = ""
    feedback: str = ""
    refined_draft: str = ""
    iteration: int = 0
    max_iterations: int = 3
    quality_scores: list[float] = field(default_factory=list)

    def should_continue(self) -> bool:
        """Check if refinement should continue."""
        if self.iteration >= self.max_iterations:
            return False
        if len(self.quality_scores) >= 2:
            # If no improvement in last 2 iterations, stop
            if self.quality_scores[-1] <= self.quality_scores[-2]:
                return False
        return True

    def record_iteration(self, quality_score: float) -> None:
        self.iteration += 1
        self.quality_scores.append(quality_score)

    @property
    def best_draft(self) -> str:
        """Return the best draft (highest quality score)."""
        if not self.quality_scores:
            return self.draft
        best_idx = self.quality_scores.index(max(self.quality_scores))
        if best_idx == len(self.quality_scores) - 1:
            return self.refined_draft
        return self.draft  # simplified: first draft if not improving


# ── Op 35: Parallel Specialist ───────────────────────────────

class ParallelSpecialistRunner:
    """Op 35: Run independent specialists in parallel.

    Source: Anthropic orchestrator-workers pattern (2024).

    Profiler + Strategist have no dependency on each other -> run parallel.
    Critic depends on both -> run serial after they complete.

    Usage:
        runner = ParallelSpecialistRunner()
        # Start parallel specialists
        await runner.run_parallel([
            ("profiler", profiler_task),
            ("strategist", strategist_task),
        ])
        # Wait for all, then run critic
        await runner.run_serial("critic", critic_task)
    """

    def __init__(self) -> None:
        self._parallel_results: dict[str, Any] = {}
        self._serial_results: dict[str, Any] = {}

    async def run_parallel(
        self,
        tasks: list[tuple[SpecialistRole, Any]],
    ) -> dict[str, Any]:
        """Run multiple specialists in parallel (asyncio.gather).

        tasks: list of (role, callable) pairs.
        Each callable should be async and return a result dict.
        """
        async def _exec(role: SpecialistRole, fn: Any) -> tuple[str, Any]:
            if asyncio.iscoroutine(fn):
                result = await fn
            elif callable(fn):
                result = await fn()
            else:
                result = fn
            return role.value, result

        coros = [_exec(role, fn) for role, fn in tasks]
        results = await asyncio.gather(*coros, return_exceptions=True)

        for r in results:
            if isinstance(r, Exception):
                continue
            role_str, result = r
            self._parallel_results[role_str] = result

        return dict(self._parallel_results)

    async def run_serial(
        self,
        role: SpecialistRole,
        fn: Any,
    ) -> Any:
        """Run a specialist serially (after parallel ones complete)."""
        if asyncio.iscoroutine(fn):
            result = await fn
        elif callable(fn):
            result = await fn()
        else:
            result = fn
        self._serial_results[role.value] = result
        return result

    @property
    def all_results(self) -> dict[str, Any]:
        return {**self._parallel_results, **self._serial_results}


__all__ = [
    "ReWOOPlan",
    "ReflexionMemory",
    "SelfRefineCycle",
    "ParallelSpecialistRunner",
]
