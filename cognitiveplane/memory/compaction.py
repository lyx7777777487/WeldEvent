"""Context compaction — compress ReAct conversation context when approaching limits.

Source: 7-plane redesign spec §10 Context Compaction.
Fallback chain from Letta compact.py: expensive strategies degrade toward cheap ones.

Prompt design (2026-07): 参考 Anthropic Context Engineering 公开思路——
"让模型自己总结压缩"。保留架构决策、未解决的 bug、实现细节、用户反馈的学习信号；
丢弃冗余的工具输出元数据、已完成的中间推理链。压缩后带上最近几轮原文继续，
对应 Claude Code 的 compaction 实现方式。
"""

import json
import logging
from enum import Enum
from typing import Any

logger = logging.getLogger("compaction")


# ── Compaction prompts（参考 Anthropic Context Engineering）──
# 核心原则：让模型自己决定保留什么、丢弃什么，而非架构硬编码语义密度分级。
# 明确告诉模型"必须保留"和"可以丢弃"的边界，让模型在总结时有的放矢。
_SUMMARY_PROMPT = """You are compacting a conversation history for an industrial QA agent. \
Produce a concise summary that preserves information needed to continue the task.

MUST PRESERVE (lossy compression of these = task failure):
- Architecture decisions: which workflow was designed, which activity nodes chosen, and WHY
- Unresolved bugs / open questions: anything that failed, was rejected by user, or is pending
- Implementation details: workflow_id, dataset_id, job_id, image_refs — any IDs the agent needs
- User feedback signals: corrections the user made (e.g. "this is crack not slag"), and their reasoning
- The current task objective: what the user is trying to accomplish

CAN DROP (low semantic density, recoverable from EventLog):
- Full tool call arguments and raw JSON outputs — keep only the conclusion (e.g. "IQA passed, blur=34.6")
- Completed intermediate reasoning chains — keep only the final decision
- Redundant confirmations / status checks
- Verbose system prompt reminders

Format: a structured summary with sections [Decisions], [Open Issues], [Key IDs], [User Feedback], [Next Step]. \
Keep it under 500 words.

Conversation history to compact:
{history}"""

_SELF_COMPACT_ALL_PROMPT = """You are compacting your OWN full context to continue a long industrial QA task. \
This is a self-compaction — you are summarizing your own conversation so you can keep working \
without losing critical information.

MUST PRESERVE (if you drop these, you will fail the task):
- All architecture decisions you made: workflow designs, activity node choices, and the reasoning behind them
- Any unresolved bugs, rejected tool calls, or pending user confirmations
- All critical IDs: workflow_id, dataset_id, job_id, image_refs, version_ids
- User feedback and corrections — especially "why" the user changed something (this is the learning signal)
- The current task objective and what step you are on

CAN DROP (these are recoverable or low-value):
- Full tool call payloads and raw JSON outputs — keep only conclusions
- Your own completed thinking chains — keep only the decision reached
- Redundant status checks and confirmations

Format: structured summary with [Decisions Made], [Open Issues], [Critical IDs], [User Feedback], [Current Step]. \
Keep under 800 words. This summary replaces your full history — make it self-sufficient.

Full conversation to compact:
{history}"""

_SELF_COMPACT_SLIDING_PROMPT = """You are compacting OLDER conversation history (the recent messages will stay as-is). \
Summarize the older context so the agent can continue without losing critical decisions.

MUST PRESERVE:
- Architecture decisions from earlier turns: workflow designs, node choices, and reasoning
- Unresolved issues from earlier: failures, rejections, pending items
- Critical IDs referenced earlier: workflow_id, dataset_id, job_id, image_refs
- User feedback / corrections from earlier turns and their reasoning
- The overall task objective established earlier

CAN DROP:
- Full tool call arguments and raw outputs — keep only conclusions
- Completed reasoning chains — keep only decisions
- Redundant checks and confirmations

Format: structured summary with [Earlier Decisions], [Open Issues], [Critical IDs], [Earlier User Feedback]. \
Keep under 500 words.

Older conversation to compact:
{history}"""


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


    # ── Op 12: Safe compaction enhancements ───────────────────
    # Source: Pydantic AI Harness compaction (调研报告 §5.5)
    #         + Anthropic Context Engineering

    @staticmethod
    def _find_safe_compaction_boundary(messages: list[dict]) -> int:
        """Op 12: Find the safe compaction boundary.

        Returns the index AFTER the last complete tool-call/result pair.
        Messages before this index can be safely compacted; messages from
        this index onward must be kept intact (they form an incomplete
        tool-call/return pair that would break the provider API if split).

        A "complete pair" = assistant msg with tool_calls followed by
        tool result msgs for ALL tool_call_ids.
        """
        if not messages:
            return 0

        # Walk backwards to find the last complete pair
        last_safe = 0
        pending_tool_call_ids: set[str] = set()

        for i, msg in enumerate(messages):
            role = msg.get("role", "")
            if role == "assistant":
                tool_calls = msg.get("tool_calls") or []
                if tool_calls:
                    # New tool calls - check if previous ones were resolved
                    if pending_tool_call_ids:
                        # Previous pair incomplete - boundary is before this msg
                        break
                    pending_tool_call_ids = {
                        tc.get("id", "") for tc in tool_calls if tc.get("id")
                    }
                    # If this is the last message, pair is incomplete
                    if i == len(messages) - 1:
                        break
                else:
                    # Assistant without tool_calls - safe to compact up to here
                    if not pending_tool_call_ids:
                        last_safe = i + 1
            elif role == "tool":
                tool_call_id = msg.get("tool_call_id", "")
                pending_tool_call_ids.discard(tool_call_id)
                if not pending_tool_call_ids:
                    # All tool results received - pair complete
                    last_safe = i + 1

        return last_safe

    def _warn_budget(self, messages: list[dict]) -> None:
        """Op 12: Warn when approaching token budget limits."""
        estimated = self._estimate_tokens(messages)
        ratio = estimated / self._max_tokens if self._max_tokens > 0 else 0
        if ratio >= 1.0:
            logger.warning(
                "[compaction] Token budget EXCEEDED: %d/%d tokens (%.0f%%) - compaction triggered",
                estimated, self._max_tokens, ratio * 100,
            )
        elif ratio >= 0.8:
            logger.warning(
                "[compaction] Token budget approaching limit: %d/%d tokens (%.0f%%) - compaction soon",
                estimated, self._max_tokens, ratio * 100,
            )

    @staticmethod
    def _strip_large_outputs(messages: list[dict], max_output_chars: int = 2000) -> list[dict]:
        """Op 12: Strip large tool outputs before compaction.

        Replaces oversized tool result content with truncated versions,
        keeping only the first/last portions and a truncation marker.
        This is a pre-compaction step that reduces token count cheaply
        (no LLM call) before the more expensive summarization strategies.
        """
        result = []
        for msg in messages:
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                if isinstance(content, str) and len(content) > max_output_chars:
                    # Keep first 30% and last 30%, mark middle as truncated
                    keep = max_output_chars // 3
                    truncated = (
                        content[:keep]
                        + f"\n... [truncated by compaction: original {len(content)} chars, "
                        + f"removed {len(content) - 2 * keep} chars] ...\n"
                        + content[-keep:]
                    )
                    msg_copy = dict(msg)
                    msg_copy["content"] = truncated
                    result.append(msg_copy)
                    continue
            result.append(msg)
        return result

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
                "content": _SUMMARY_PROMPT.format(history=json.dumps(older, ensure_ascii=False))
            }],
            caller="compactor",
            purpose="compaction",
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
                "content": _SELF_COMPACT_ALL_PROMPT.format(history=json.dumps(messages, ensure_ascii=False))
            }],
            caller="compactor",
            purpose="compaction",
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
                "content": _SELF_COMPACT_SLIDING_PROMPT.format(history=json.dumps(older, ensure_ascii=False))
            }],
            caller="compactor",
            purpose="compaction",
        ))
        return system + [{"role": "assistant", "content": f"[Compacted older context]: {compacted.content}"}] + recent

    @staticmethod
    def _estimate_tokens(messages: list[dict]) -> int:
        """Rough token estimate: ~4 chars per token."""
        total = sum(len(m.get("content", "")) for m in messages)
        return total // 4
