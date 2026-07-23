"""Op 8.2: Structured trajectory store + embedding RAG.

Source: Generative Agents reflection + retrieval (Park et al., UIST 2023)
        + Cursor codebase indexing + Letta archival memory search.

Stores workflow execution records as structured episodic memory with
embedding vectors for semantic retrieval. Extracts semantic rules from
repeated episodic patterns.

Depends on:
  - Op 8.0: LLMProvider.embed() (already implemented in openai_provider.py)
  - Op 8.1: MemoryType.EPISODIC / SEMANTIC (already added to enums.py)
  - existing search.py RRF hybrid search
"""

from __future__ import annotations

import json
import logging
import time as _time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from cognitiveplane.control.planner.advanced import ReflexionMemory

if TYPE_CHECKING:
    from cognitiveplane.capability.provider import LLMProvider

logger = logging.getLogger("trajectory_store")


@dataclass
class EpisodicMemory:
    """Structured execution record for semantic retrieval.

    Mapped to MemoryType.EPISODIC (L2_CASE + L3_EXPERIENCE in hierarchy).
    """
    record_id: str
    goal: str
    goal_embedding: list[float] = field(default_factory=list)
    spec_summary: dict[str, Any] = field(default_factory=dict)
    node_outcomes: list[dict[str, Any]] = field(default_factory=list)
    quality_score: float = 0.0
    outcome: str = ""  # "completed" | "failed" | "partial"
    timestamp: float = field(default_factory=lambda: _time.time())
    session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "goal": self.goal,
            "goal_embedding_len": len(self.goal_embedding),
            "spec_summary": self.spec_summary,
            "node_outcomes": self.node_outcomes,
            "quality_score": self.quality_score,
            "outcome": self.outcome,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
        }


@dataclass
class SemanticRule:
    """Extracted rule from multiple episodic memories.

    Mapped to MemoryType.SEMANTIC (L4_KNOWLEDGE).
    Format: IF <condition> THEN <action> BECAUSE <reason>
    """
    rule_id: str
    condition: str
    action: str
    reason: str
    confidence: float = 0.0  # occurrence_count / total_episodes
    occurrence_count: int = 0
    rule_embedding: list[float] = field(default_factory=list)
    created_at: float = field(default_factory=lambda: _time.time())

    def to_prompt_text(self) -> str:
        return f"IF {self.condition} THEN {self.action} BECAUSE {self.reason} (confidence: {self.confidence:.0%})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "condition": self.condition,
            "action": self.action,
            "reason": self.reason,
            "confidence": self.confidence,
            "occurrence_count": self.occurrence_count,
        }


class TrajectoryMemoryStore:
    """Op 8.2: In-memory store for episodic memories + semantic rules.

    Uses embedding similarity for semantic retrieval.
    In production, this would be backed by a vector DB (Milvus/Pinecone).
    """

    def __init__(self, llm_provider: "LLMProvider | None" = None) -> None:
        self._llm = llm_provider
        self._episodic: list[EpisodicMemory] = []
        self._rules: list[SemanticRule] = []
        self._rule_counter = 0
        self._episodic_counter = 0
        # Op 32: Reflexion memory for failure lessons
        self._reflexion = ReflexionMemory()

    async def store_episode(
        self,
        goal: str,
        spec_summary: dict[str, Any],
        node_outcomes: list[dict[str, Any]],
        outcome: str,
        quality_score: float = 0.0,
        session_id: str = "",
    ) -> EpisodicMemory:
        """Store an episodic memory with goal embedding."""
        self._episodic_counter += 1
        record = EpisodicMemory(
            record_id=f"ep_{self._episodic_counter}",
            goal=goal,
            spec_summary=spec_summary,
            node_outcomes=node_outcomes,
            outcome=outcome,
            quality_score=quality_score,
            session_id=session_id,
        )

        # Generate embedding for the goal
        if self._llm is not None:
            try:
                embeddings = await self._llm.embed([goal])
                if embeddings:
                    record.goal_embedding = embeddings[0]
            except Exception as e:
                logger.warning("embed() failed for goal: %s", e)

        self._episodic.append(record)
        logger.info("Stored episode %s: goal=%s outcome=%s", record.record_id, goal[:80], outcome)
        return record

    async def retrieve_similar(
        self, query: str, top_k: int = 3
    ) -> list[EpisodicMemory]:
        """Retrieve similar episodic memories via embedding similarity.

        If no embedding available, falls back to keyword matching.
        """
        if not self._episodic:
            return []

        # Generate query embedding
        query_embedding: list[float] = []
        if self._llm is not None:
            try:
                embeddings = await self._llm.embed([query])
                if embeddings:
                    query_embedding = embeddings[0]
            except Exception:
                pass

        if query_embedding:
            # Cosine similarity
            scored = []
            for ep in self._episodic:
                if ep.goal_embedding:
                    sim = _cosine_similarity(query_embedding, ep.goal_embedding)
                    scored.append((sim, ep))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [ep for _, ep in scored[:top_k]]
        else:
            # Fallback: character bigram overlap (works for CJK text)
            query_lower = query.lower()
            query_bigrams = {query_lower[i:i+2] for i in range(len(query_lower) - 1)}
            scored = []
            for ep in self._episodic:
                goal_lower = ep.goal.lower()
                goal_bigrams = {goal_lower[i:i+2] for i in range(len(goal_lower) - 1)}
                overlap = len(query_bigrams & goal_bigrams)
                scored.append((overlap, ep))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [ep for score, ep in scored[:top_k] if score > 0]

    async def extract_rules(self, min_occurrence: int = 2) -> list[SemanticRule]:
        """Extract semantic rules from episodic memories.

        Uses LLM to find repeated patterns in episodes and synthesize
        IF-THEN-BECAUSE rules. Only patterns appearing >= min_occurrence
        times are kept.
        """
        if len(self._episodic) < min_occurrence or self._llm is None:
            return []

        # Build LLM prompt for rule extraction
        episodes_text = "\n".join(
            f"- Goal: {ep.goal}\n  Outcome: {ep.outcome}\n  Nodes: {ep.node_outcomes}"
            for ep in self._episodic[-10:]  # last 10 episodes
        )

        prompt = (
            "从以下工业焊缝检测执行记录中提取可复用的规则。\n"
            "格式: IF <condition> THEN <action> BECAUSE <reason>\n"
            "只提取出现 >=2 次的模式。每行一条规则。\n\n"
            f"执行记录:\n{episodes_text}\n\n"
            "规则（JSON 数组格式，每条含 condition/action/reason 字段）:"
        )

        try:
            from cognitiveplane.capability.provider import LLMRequest
            resp = await self._llm.complete(LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                caller="trajectory_store",
                purpose="rule_extraction",
                max_tokens=2048,
                temperature=0.1,
            ))
            # Parse rules from LLM response
            rules_data = _parse_rules_response(resp.content)
        except Exception as e:
            logger.warning("Rule extraction failed: %s", e)
            return []

        new_rules: list[SemanticRule] = []
        for rd in rules_data:
            self._rule_counter += 1
            rule = SemanticRule(
                rule_id=f"rule_{self._rule_counter}",
                condition=rd.get("condition", ""),
                action=rd.get("action", ""),
                reason=rd.get("reason", ""),
                confidence=min(1.0, min_occurrence / len(self._episodic)),
                occurrence_count=min_occurrence,
            )
            # Generate embedding for the rule
            rule_text = rule.to_prompt_text()
            if self._llm is not None:
                try:
                    embeddings = await self._llm.embed([rule_text])
                    if embeddings:
                        rule.rule_embedding = embeddings[0]
                except Exception:
                    pass
            self._rules.append(rule)
            new_rules.append(rule)

        logger.info("Extracted %d rules from %d episodes", len(new_rules), len(self._episodic))
        return new_rules

    def get_rules_for_prompt(self, query: str, max_rules: int = 3) -> str:
        """Get relevant semantic rules as prompt text."""
        if not self._rules:
            return ""
        # Simple keyword matching (embedding matching would be better)
        query_lower = query.lower()
        relevant = []
        for rule in self._rules:
            if any(w in query_lower for w in rule.condition.lower().split()):
                relevant.append(rule)
        if not relevant:
            relevant = self._rules[-max_rules:]
        else:
            relevant = relevant[:max_rules]

        lines = ["## 历史经验规则\n"]
        for r in relevant:
            lines.append(f"- {r.to_prompt_text()}")
        return "\n".join(lines)

    @property
    def episode_count(self) -> int:
        return len(self._episodic)

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    # Op 32: Reflexion integration
    def add_reflexion_note(self, task: str, failure: str, lesson: str) -> None:
        """Op 32: Store a failure lesson from execution."""
        self._reflexion.add_note(task, failure, lesson)

    def get_reflexion_prompt(self, task: str, max_notes: int = 3) -> str:
        """Op 32: Get reflexion notes for prompt injection."""
        notes = self._reflexion.get_notes_for_task(task)
        if not notes:
            return ""
        lines = ["## 失败教训 (Reflexion)\n"]
        for n in self._reflexion.notes[-max_notes:]:
            if task.lower() in n["task"].lower() or any(
                w in n["task"].lower() for w in task.lower().split()
            ):
                lines.append(f"- **任务**: {n['task']}")
                lines.append(f"  **教训**: {n['lesson']}\n")
        return "\n".join(lines) if len(lines) > 1 else ""


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _parse_rules_response(text: str) -> list[dict[str, str]]:
    """Parse LLM response into rule dicts."""
    import re
    # Try JSON array first
    try:
        # Find JSON array in response
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except (json.JSONDecodeError, AttributeError):
        pass

    # Fallback: parse line-by-line IF-THEN-BECAUSE format
    rules = []
    for line in text.strip().split("\n"):
        m = re.match(r'IF\s+(.+?)\s+THEN\s+(.+?)\s+BECAUSE\s+(.+)', line, re.IGNORECASE)
        if m:
            rules.append({
                "condition": m.group(1).strip(),
                "action": m.group(2).strip(),
                "reason": m.group(3).strip(),
            })
    return rules


__all__ = [
    "EpisodicMemory",
    "SemanticRule",
    "TrajectoryMemoryStore",
]
