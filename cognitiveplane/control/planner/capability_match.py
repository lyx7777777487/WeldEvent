"""Op 8.6: Capability advertising + dynamic matching.

Source: AutoGen v4 agent capability discovery + CrewAI role-based assignment
        + Magentic-One dynamic delegation.

Instead of fixed Specialist roles (Profiler/Strategist/Critic/Executor),
match agents by semantic similarity between task description and agent
capability description. Uses embedding (Op 8.0) for matching.

Depends on:
  - Op 8.0: LLMProvider.embed() (already implemented)
  - .weldevent/agents/ directory (existing subagent definitions)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from cognitiveplane.capability.provider import LLMProvider

logger = logging.getLogger("capability_match")


@dataclass
class AgentCapability:
    """Op 8.6: Advertised capability of an agent.

    Each agent in .weldevent/agents/ has a description that can be used
    for semantic matching against task descriptions.
    """
    agent_name: str
    description: str
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    description_embedding: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "description": self.description,
            "skills": self.skills,
            "tools": self.tools,
            "tags": self.tags,
        }


class CapabilityMatcher:
    """Op 8.6: Match agents to tasks by semantic similarity.

    Uses embedding cosine similarity (Op 8.0) for matching.
    Falls back to character bigram overlap when no embedding available.
    """

    def __init__(self, llm_provider: "LLMProvider | None" = None) -> None:
        self._llm = llm_provider
        self._agents: dict[str, AgentCapability] = {}

    def register(self, agent: AgentCapability) -> None:
        self._agents[agent.agent_name] = agent

    def register_from_dict(self, data: dict[str, Any]) -> AgentCapability:
        """Register an agent from a dict (e.g. parsed from .weldevent/agents/*.md)."""
        agent = AgentCapability(
            agent_name=data.get("name", "unknown"),
            description=data.get("description", ""),
            skills=data.get("skills", []),
            tools=data.get("tools", []),
            tags=data.get("tags", []),
        )
        self.register(agent)
        return agent

    async def match(
        self, task_description: str, threshold: float = 0.3, top_k: int = 3
    ) -> list[tuple[AgentCapability, float]]:
        """Match task description to agent capabilities.

        Returns list of (agent, score) pairs sorted by score descending.
        Score is cosine similarity (0.0-1.0) or bigram overlap ratio.
        """
        if not self._agents:
            return []

        # Generate query embedding
        query_embedding: list[float] = []
        if self._llm is not None:
            try:
                embeddings = await self._llm.embed([task_description])
                if embeddings:
                    query_embedding = embeddings[0]
            except Exception:
                pass

        scored: list[tuple[AgentCapability, float]] = []

        for agent in self._agents.values():
            if query_embedding and agent.description_embedding:
                # Cosine similarity
                score = _cosine_sim(query_embedding, agent.description_embedding)
            else:
                # Fallback: character bigram overlap
                score = _bigram_overlap(task_description, agent.description)

            # Boost by tag/keyword matching
            task_lower = task_description.lower()
            for tag in agent.tags:
                if tag.lower() in task_lower:
                    score += 0.1
            for skill in agent.skills:
                if skill.lower() in task_lower:
                    score += 0.05

            scored.append((agent, min(1.0, score)))

        # Filter by threshold and sort
        scored = [(a, s) for a, s in scored if s >= threshold]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    async def generate_embeddings(self) -> None:
        """Generate embeddings for all registered agents' descriptions."""
        if self._llm is None:
            return
        for agent in self._agents.values():
            if not agent.description_embedding and agent.description:
                try:
                    embeddings = await self._llm.embed([agent.description])
                    if embeddings:
                        agent.description_embedding = embeddings[0]
                except Exception as e:
                    logger.warning("embed() failed for agent %s: %s", agent.agent_name, e)

    def list_agents(self) -> list[AgentCapability]:
        return list(self._agents.values())


def _cosine_sim(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _bigram_overlap(a: str, b: str) -> float:
    """Character bigram overlap ratio (works for CJK)."""
    a_lower = a.lower()
    b_lower = b.lower()
    a_bigrams = {a_lower[i:i+2] for i in range(len(a_lower) - 1)}
    b_bigrams = {b_lower[i:i+2] for i in range(len(b_lower) - 1)}
    if not a_bigrams or not b_bigrams:
        return 0.0
    overlap = len(a_bigrams & b_bigrams)
    return overlap / min(len(a_bigrams), len(b_bigrams))


__all__ = ["AgentCapability", "CapabilityMatcher"]
