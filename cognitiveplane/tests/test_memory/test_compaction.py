"""Tests for memory/compaction.py — ContextCompactor with fallback chain."""

import pytest

from cognitiveplane.memory.compaction import CompactionStrategy, ContextCompactor


class MockLLM:
    """Mock LLM provider for compaction tests."""

    def __init__(self, response: str = "Summary of conversation"):
        self._response = response
        self.calls = 0

    async def complete(self, request):
        self.calls += 1

        class Response:
            content = self._response

        return Response()


class TestContextCompactor:
    def _make_messages(self, count: int, content_len: int = 200) -> list[dict]:
        msgs = [{"role": "system", "content": "You are an assistant."}]
        for i in range(count):
            msgs.append({"role": "user", "content": f"Message {i}: " + "x" * content_len})
            msgs.append({"role": "assistant", "content": f"Reply {i}: " + "y" * content_len})
        return msgs

    @pytest.mark.asyncio
    async def test_no_compaction_needed(self):
        compactor = ContextCompactor(max_tokens=100000)
        messages = self._make_messages(2)
        result = await compactor.compact(messages)
        assert result == messages

    @pytest.mark.asyncio
    async def test_sliding_window(self):
        compactor = ContextCompactor(max_tokens=50, default_strategy=CompactionStrategy.SLIDING_WINDOW)
        messages = self._make_messages(10)
        result = await compactor.compact(messages)
        # Should have system + last 6 non-system messages
        system = [m for m in result if m["role"] == "system"]
        assert len(system) == 1
        non_system = [m for m in result if m["role"] != "system"]
        assert len(non_system) <= 6

    @pytest.mark.asyncio
    async def test_full_summary_with_llm(self):
        llm = MockLLM()
        compactor = ContextCompactor(llm_provider=llm, max_tokens=50, default_strategy=CompactionStrategy.FULL_SUMMARY)
        messages = self._make_messages(10)
        result = await compactor.compact(messages)
        assert llm.calls == 1
        # Should contain summary block
        summaries = [m for m in result if "Previous context summary" in m.get("content", "")]
        assert len(summaries) == 1

    @pytest.mark.asyncio
    async def test_full_summary_without_llm_falls_back_to_sliding(self):
        compactor = ContextCompactor(llm_provider=None, max_tokens=50, default_strategy=CompactionStrategy.FULL_SUMMARY)
        messages = self._make_messages(10)
        result = await compactor.compact(messages)
        # Should fall back to sliding window
        non_system = [m for m in result if m["role"] != "system"]
        assert len(non_system) <= 6

    @pytest.mark.asyncio
    async def test_self_compact_all_with_llm(self):
        llm = MockLLM("Compact context here")
        compactor = ContextCompactor(llm_provider=llm, max_tokens=50, default_strategy=CompactionStrategy.SELF_COMPACT_ALL)
        messages = self._make_messages(10)
        result = await compactor.compact(messages)
        assert llm.calls >= 1
        compacted = [m for m in result if "Compacted context" in m.get("content", "")]
        assert len(compacted) == 1

    @pytest.mark.asyncio
    async def test_self_compact_all_without_llm_falls_back(self):
        compactor = ContextCompactor(llm_provider=None, max_tokens=50, default_strategy=CompactionStrategy.SELF_COMPACT_ALL)
        messages = self._make_messages(10)
        result = await compactor.compact(messages)
        # Falls back to sliding window
        non_system = [m for m in result if m["role"] != "system"]
        assert len(non_system) <= 6

    @pytest.mark.asyncio
    async def test_self_compact_sliding_with_llm(self):
        llm = MockLLM("Compacted older messages")
        compactor = ContextCompactor(llm_provider=llm, max_tokens=200, default_strategy=CompactionStrategy.SELF_COMPACT_SLIDING)
        messages = self._make_messages(10, content_len=300)
        result = await compactor.compact(messages)
        assert llm.calls >= 1
        # self_compact_sliding may fall back to full_summary if still over limit
        assert any(
            "Compacted older context" in m.get("content", "") or "Previous context summary" in m.get("content", "")
            for m in result
        )


class TestFallbackChain:
    def test_next_cheaper_self_compact_all(self):
        result = ContextCompactor._next_cheaper(CompactionStrategy.SELF_COMPACT_ALL)
        assert result == CompactionStrategy.SELF_COMPACT_SLIDING

    def test_next_cheaper_self_compact_sliding(self):
        result = ContextCompactor._next_cheaper(CompactionStrategy.SELF_COMPACT_SLIDING)
        assert result == CompactionStrategy.FULL_SUMMARY

    def test_next_cheaper_full_summary(self):
        result = ContextCompactor._next_cheaper(CompactionStrategy.FULL_SUMMARY)
        assert result is None

    def test_next_cheaper_sliding_window(self):
        result = ContextCompactor._next_cheaper(CompactionStrategy.SLIDING_WINDOW)
        assert result == CompactionStrategy.FULL_SUMMARY


class TestEstimateTokens:
    def test_estimate_tokens(self):
        messages = [{"role": "user", "content": "a" * 100}]
        assert ContextCompactor._estimate_tokens(messages) == 25  # 100 / 4

    def test_estimate_tokens_empty(self):
        assert ContextCompactor._estimate_tokens([]) == 0
