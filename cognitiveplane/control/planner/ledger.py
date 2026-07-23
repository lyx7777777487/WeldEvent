"""Magentic-One dual-ledger planner - Op 13/14/15.

Source: Magentic-One (arXiv:2411.04468) - dual ledger architecture:
  - Task Ledger: outer-loop planning / replanning
  - Progress Ledger: inner-loop specialist delegation / progress tracking

This module implements the planning layer that sits between the ReAct engine
and the workflow execution. It does NOT replace the ReAct loop; it wraps it
with a structured planning capability for complex, multi-step tasks.

Design principles (from the paper):
  1. Outer loop: Manager updates Task Ledger with facts/constraints, assigns
     subtasks to specialists
  2. Inner loop: Specialist executes subtask, updates Progress Ledger
  3. Stagnation detection: if no progress for N turns, return to outer loop
  4. Facts ledger: shared context that all specialists can read
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    """Task lifecycle states (Magentic-One §3.1)."""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class SpecialistRole(str, Enum):
    """Specialist roles for delegation (Op 14).

    Source: Magentic-One dynamic delegation + Anthropic orchestrator-workers.
    Profiler + Strategist can run in parallel (Op 35); Critic is serial.
    """
    PROFILER = "profiler"       # 数据/上下文探索
    STRATEGIST = "strategist"   # 方案设计
    CRITIC = "critic"           # 方案审查（串行，依赖前两者）
    EXECUTOR = "executor"       # 执行


@dataclass
class TaskItem:
    """A single task in the Task Ledger."""
    task_id: str
    description: str
    status: TaskStatus = TaskStatus.NOT_STARTED
    assigned_to: SpecialistRole | None = None
    result: dict[str, Any] | None = None
    created_at: float = field(default_factory=lambda: _time.time())
    updated_at: float = field(default_factory=lambda: _time.time())
    # Op 32: Reflexion note from previous failures
    reflexion_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "description": self.description,
            "status": self.status.value,
            "assigned_to": self.assigned_to.value if self.assigned_to else None,
            "result": self.result,
            "reflexion_note": self.reflexion_note,
        }


@dataclass
class ProgressItem:
    """A single progress entry in the Progress Ledger."""
    step: int
    specialist: SpecialistRole
    task_id: str
    action: str
    outcome: str  # "progress" | "no_progress" | "error"
    detail: str = ""
    timestamp: float = field(default_factory=lambda: _time.time())


class TaskLedger:
    """Op 13: Task Ledger - outer-loop planning state.

    Source: Magentic-One §3.2 - outer loop updates facts/constraints,
    assigns subtasks to specialists. Already-executed tasks are NOT
    rolled back (Magentic-One constraint).

    The Task Ledger holds:
      - objective: the overall goal
      - facts: confirmed facts/constraints (updated as execution proceeds)
      - tasks: ordered list of subtasks
      - hypotheses: unconfirmed assumptions to verify
    """

    def __init__(self, objective: str) -> None:
        self.objective: str = objective
        self.facts: list[str] = []
        self.tasks: list[TaskItem] = []
        self.hypotheses: list[str] = []
        self._task_counter: int = 0

    def add_fact(self, fact: str) -> None:
        """Add a confirmed fact to the ledger."""
        if fact and fact not in self.facts:
            self.facts.append(fact)

    def add_task(self, description: str) -> TaskItem:
        """Add a new subtask to the ledger."""
        self._task_counter += 1
        task = TaskItem(
            task_id=f"task_{self._task_counter}",
            description=description,
        )
        self.tasks.append(task)
        return task

    def assign_task(self, task_id: str, specialist: SpecialistRole) -> None:
        """Assign a task to a specialist."""
        for t in self.tasks:
            if t.task_id == task_id:
                t.assigned_to = specialist
                t.status = TaskStatus.IN_PROGRESS
                t.updated_at = _time.time()
                return

    def complete_task(self, task_id: str, result: dict[str, Any]) -> None:
        """Mark a task as completed with its result."""
        for t in self.tasks:
            if t.task_id == task_id:
                t.status = TaskStatus.COMPLETED
                t.result = result
                t.updated_at = _time.time()
                return

    def fail_task(self, task_id: str, error: str) -> None:
        """Mark a task as failed."""
        for t in self.tasks:
            if t.task_id == task_id:
                t.status = TaskStatus.FAILED
                t.result = {"error": error}
                t.updated_at = _time.time()
                return

    def get_next_task(self) -> TaskItem | None:
        """Get the next not-started task."""
        for t in self.tasks:
            if t.status == TaskStatus.NOT_STARTED:
                return t
        return None

    @property
    def is_complete(self) -> bool:
        """All tasks are either completed or failed."""
        return all(
            t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
            for t in self.tasks
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "facts": self.facts,
            "tasks": [t.to_dict() for t in self.tasks],
            "hypotheses": self.hypotheses,
        }


class ProgressLedger:
    """Op 13: Progress Ledger - inner-loop execution tracking.

    Source: Magentic-One §3.3 - inner loop tracks specialist progress,
    detects stagnation, and decides when to return to the outer loop.
    """

    # Op 15: Stagnation threshold - N turns with no progress -> replan
    STAGNATION_THRESHOLD: int = 3

    def __init__(self) -> None:
        self.entries: list[ProgressItem] = []
        self._step_counter: int = 0

    def record(
        self,
        specialist: SpecialistRole,
        task_id: str,
        action: str,
        outcome: str,
        detail: str = "",
    ) -> ProgressItem:
        """Record a progress entry."""
        self._step_counter += 1
        entry = ProgressItem(
            step=self._step_counter,
            specialist=specialist,
            task_id=task_id,
            action=action,
            outcome=outcome,
            detail=detail,
        )
        self.entries.append(entry)
        return entry

    def check_stagnation(self) -> bool:
        """Op 15: Detect stagnation - consecutive no_progress turns.

        Source: Magentic-One stagnation detection.
        If the last STAGNATION_THRESHOLD entries are all "no_progress"
        or "error", return True (need replanning).
        """
        if len(self.entries) < self.STAGNATION_THRESHOLD:
            return False
        recent = self.entries[-self.STAGNATION_THRESHOLD:]
        return all(e.outcome in ("no_progress", "error") for e in recent)

    @property
    def total_steps(self) -> int:
        return len(self.entries)

    @property
    def progress_steps(self) -> int:
        return sum(1 for e in self.entries if e.outcome == "progress")

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_steps": self.total_steps,
            "progress_steps": self.progress_steps,
            "entries": [
                {
                    "step": e.step,
                    "specialist": e.specialist.value,
                    "task_id": e.task_id,
                    "action": e.action,
                    "outcome": e.outcome,
                    "detail": e.detail,
                }
                for e in self.entries
            ],
        }


class MagenticPlanner:
    """Op 13/14/15: Magentic-One dual-ledger planner.

    Wraps the planning lifecycle:
      1. Create Task Ledger with objective + initial facts
      2. For each task: assign specialist, execute, record progress
      3. Check stagnation -> replan if needed
      4. Aggregate results when all tasks complete

    This is a lightweight coordinator - it does NOT replace the ReAct engine.
    It structures complex multi-step planning that the ReAct engine would
    otherwise handle ad-hoc.
    """

    def __init__(self, objective: str) -> None:
        self.task_ledger = TaskLedger(objective)
        self.progress_ledger = ProgressLedger()

    def plan(self, task_descriptions: list[str]) -> None:
        """Create the initial plan with subtasks."""
        for desc in task_descriptions:
            self.task_ledger.add_task(desc)

    def assign_next(self) -> tuple[TaskItem, SpecialistRole] | None:
        """Get the next task and assign it to the best specialist.

        Op 14: Specialist delegation.
        Source: Magentic-One dynamic delegation + Anthropic orchestrator-workers.
        """
        task = self.task_ledger.get_next_task()
        if task is None:
            return None

        # Simple heuristic: assign based on task description keywords
        desc_lower = task.description.lower()
        if any(k in desc_lower for k in ["分析", "探索", "查询", "了解", "profiler"]):
            specialist = SpecialistRole.PROFILER
        elif any(k in desc_lower for k in ["设计", "方案", "规划", "strategist"]):
            specialist = SpecialistRole.STRATEGIST
        elif any(k in desc_lower for k in ["审查", "检查", "验证", "critic"]):
            specialist = SpecialistRole.CRITIC
        else:
            specialist = SpecialistRole.EXECUTOR

        self.task_ledger.assign_task(task.task_id, specialist)
        return task, specialist

    def record_progress(
        self,
        task_id: str,
        specialist: SpecialistRole,
        action: str,
        outcome: str,
        detail: str = "",
    ) -> None:
        """Record progress for a task."""
        self.progress_ledger.record(specialist, task_id, action, outcome, detail)

    def should_replan(self) -> bool:
        """Op 15: Check if replanning is needed (stagnation detection)."""
        return self.progress_ledger.check_stagnation()

    def replan(self) -> None:
        """Op 15: Trigger replanning.

        Mark blocked tasks as not_started, add new facts from
        progress entries, and let the outer loop re-assign.
        """
        # Find blocked/failed tasks and reset them
        for t in self.task_ledger.tasks:
            if t.status in (TaskStatus.FAILED, TaskStatus.BLOCKED):
                t.status = TaskStatus.NOT_STARTED
                t.assigned_to = None
                t.updated_at = _time.time()
                # Op 32: Generate reflexion note from failures
                if t.result and t.result.get("error"):
                    t.reflexion_note = f"Previous attempt failed: {t.result['error']}"

    def aggregate(self) -> dict[str, Any]:
        """Aggregate all task results when complete."""
        return {
            "objective": self.task_ledger.objective,
            "facts": self.task_ledger.facts,
            "tasks": [t.to_dict() for t in self.task_ledger.tasks],
            "progress": self.progress_ledger.to_dict(),
            "complete": self.task_ledger.is_complete,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_ledger": self.task_ledger.to_dict(),
            "progress_ledger": self.progress_ledger.to_dict(),
        }


__all__ = [
    "TaskStatus",
    "SpecialistRole",
    "TaskItem",
    "ProgressItem",
    "TaskLedger",
    "ProgressLedger",
    "MagenticPlanner",
]
