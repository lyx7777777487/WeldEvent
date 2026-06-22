"""Sub-agent delegation (spec §5 line 1008).

Lightweight in-process delegator: dispatches tasks to named specialist
sub-agents (e.g., StandardsExpert, ProcessExpert) registered against the
delegator. Each sub-agent is a callable returning a structured result.

This is a deterministic baseline; Phase 2 may swap to LangGraph subgraphs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

SubAgentFn = Callable[["SubAgentTask"], Awaitable["SubAgentResult"]]


@dataclass
class SubAgentTask:
    name: str                # logical task name e.g. "verify_standard"
    payload: dict[str, Any] = field(default_factory=dict)
    target_agent: str | None = None   # explicit target name; if None, name is used


@dataclass
class SubAgentResult:
    agent: str
    success: bool
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class SubAgentDelegator:
    """Register and dispatch sub-agent tasks."""

    def __init__(self) -> None:
        self._agents: dict[str, SubAgentFn] = {}

    def register(self, name: str, agent: SubAgentFn) -> None:
        self._agents[name] = agent

    def is_registered(self, name: str) -> bool:
        return name in self._agents

    async def delegate(self, task: SubAgentTask) -> SubAgentResult:
        target = task.target_agent or task.name
        agent = self._agents.get(target)
        if agent is None:
            return SubAgentResult(
                agent=target, success=False, error=f"No sub-agent registered: {target}"
            )
        try:
            return await agent(task)
        except Exception as e:
            return SubAgentResult(agent=target, success=False, error=str(e))
