"""Context compaction — compress ReAct conversation context when approaching limits.

Source: 7-plane redesign spec §10 Context Compaction.
Fallback chain from Letta compact.py: expensive strategies degrade toward cheap ones.
"""

import json
from enum import Enum
from typing import Any


class CompactionStrategy(Enum):
    SLIDING_WINDOW = "sliding_window"
    FULL_SUMMARY = "full_summary"
    SELF_COMPACT_SLIDING = "self_compact_sliding"
    SELF_COMPACT_ALL = "self_compact_all"


class ContextCompactor:
    """Compacts ReAct conversation context when approaching LLM limits.

    Fallback chain direction (from Letta compact.py):
      self_compact_all → self_compact_sliding → full_summary
      self_compact_sliding → full_summary
      sliding_window → full_summary
      full_summary → no further fallback

    More expensive strategies degrade TOWARD cheaper ones on failure.
    """

    def __init__(
        self,
        llm_provider: Any = None,
        max_tokens: int = 8000,
        default_strategy: CompactionStrategy = CompactionStrategy.SLIDING_WINDOW,
    ) -> None:
        self._llm = llm_provider
        self._max_tokens = max_tokens
        self._default_strategy = default_strategy

    async def compact(self, messages: list[dict], event_log: Any = None) -> list[dict]:
        """Apply compaction with fallback chain. Direction: expensive → cheap on failure."""
        estimated = self._estimate_tokens(messages)
        if estimated <= self._max_tokens:
            return messages

        strategy = self._default_strategy
        while True:
            try:
                if strategy == CompactionStrategy.SLIDING_WINDOW:
                    result = self._sliding_window(messages)
                    if self._estimate_tokens(result) <= self._max_tokens:
                        return result
                    strategy = CompactionStrategy.FULL_SUMMARY
                    continue

                elif strategy == CompactionStrategy.FULL_SUMMARY:
                    result = await self._full_summary(messages, event_log)
                    return result

                elif strategy == CompactionStrategy.SELF_COMPACT_ALL:
                    result = await self._self_compact_all(messages, event_log)
                    if self._estimate_tokens(result) <= self._max_tokens:
                        return result
                    strategy = CompactionStrategy.SELF_COMPACT_SLIDING
                    continue

                elif strategy == CompactionStrategy.SELF_COMPACT_SLIDING:
                    result = await self._self_compact_sliding(messages, event_log)
                    if self._estimate_tokens(result) <= self._max_tokens:
                        return result
                    strategy = CompactionStrategy.FULL_SUMMARY
                    continue

            except Exception:
                strategy = self._next_cheaper(strategy)
                if strategy is None:
                    return self._sliding_window(messages)
                continue

    @staticmethod
    def _next_cheaper(current: CompactionStrategy) -> CompactionStrategy | None:
        """Fallback chain: expensive strategies degrade toward cheap ones."""
        FALLBACK = {
            CompactionStrategy.SELF_COMPACT_ALL: CompactionStrategy.SELF_COMPACT_SLIDING,
            CompactionStrategy.SELF_COMPACT_SLIDING: CompactionStrategy.FULL_SUMMARY,
            CompactionStrategy.FULL_SUMMARY: None,
            CompactionStrategy.SLIDING_WINDOW: CompactionStrategy.FULL_SUMMARY,
        }
        return FALLBACK.get(current)

    def _sliding_window(self, messages: list[dict], keep_recent: int = 6) -> list[dict]:
        """Keep system prompt + last N messages. Cheapest — no LLM call."""
        system = [m for m in messages if m.get("role") == "system"]
        recent = [m for m in messages if m.get("role") != "system"][-keep_recent:]
        return system + recent

    async def _full_summary(self, messages: list[dict], event_log: Any = None) -> list[dict]:
        """LLM summarizes all non-recent messages into a single summary block."""
        non_system = [m for m in messages if m.get("role") != "system"]
        older = non_system[:-6]
        recent = non_system[-6:]
        system = [m for m in messages if m.get("role") == "system"]

        if not older:
            return messages

        if self._llm is None:
            return self._sliding_window(messages)

        from cognitiveplane.capability.provider import LLMRequest
        response = await self._llm.complete(LLMRequest(
            messages=[{
                "role": "user",
                "content": f"Summarize the following conversation history, preserving key decisions and reasoning:\n{json.dumps(older, ensure_ascii=False)}"
            }],
            caller="compactor",
        ))
        return system + [{"role": "assistant", "content": f"[Previous context summary]: {response.content}"}] + recent

    async def _self_compact_all(self, messages: list[dict], event_log: Any = None) -> list[dict]:
        """LLM produces a compact version of its entire context (most expensive)."""
        if self._llm is None:
            return self._sliding_window(messages)

        from cognitiveplane.capability.provider import LLMRequest
        compacted = await self._llm.complete(LLMRequest(
            messages=[{
                "role": "user",
                "content": f"Produce a compact context preserving all critical decisions and tool results.\n\nFull messages:\n{json.dumps(messages, ensure_ascii=False)}"
            }],
            caller="compactor",
        ))
        system = [m for m in messages if m.get("role") == "system"]
        return system + [{"role": "assistant", "content": f"[Compacted context]: {compacted.content}"}]

    async def _self_compact_sliding(self, messages: list[dict], event_log: Any = None) -> list[dict]:
        """LLM compacts older messages, keeps recent messages as-is."""
        non_system = [m for m in messages if m.get("role") != "system"]
        older = non_system[:-6]
        recent = non_system[-6:]
        system = [m for m in messages if m.get("role") == "system"]

        if not older:
            return messages

        if self._llm is None:
            return self._sliding_window(messages)

        from cognitiveplane.capability.provider import LLMRequest
        compacted = await self._llm.complete(LLMRequest(
            messages=[{
                "role": "user",
                "content": f"Compact the following older conversation context, preserving key decisions:\n\nMessages:\n{json.dumps(older, ensure_ascii=False)}"
            }],
            caller="compactor",
        ))
        return system + [{"role": "assistant", "content": f"[Compacted older context]: {compacted.content}"}] + recent

    @staticmethod
    def _estimate_tokens(messages: list[dict]) -> int:
        """Rough token estimate: ~4 chars per token."""
        total = sum(len(m.get("content", "")) for m in messages)
        return total // 4
