"""Tests for LLMCallTracker."""

from cognitiveplane.capability.tracking import LLMCallTracker, LLMCallRecord


class TestLLMCallTracker:
    def test_record_and_query(self):
        tracker = LLMCallTracker()
        tracker.record(LLMCallRecord(
            caller="IntentClassifier",
            purpose="intent_classification",
            model_used="gpt-4o",
            tokens_prompt=100,
            tokens_completion=50,
            latency_ms=300,
            cost_usd=0.003,
            case_id="CASE-001",
        ))
        assert tracker.total_calls == 1
        assert tracker.total_tokens == 150
        assert abs(tracker.total_cost_usd - 0.003) < 0.0001

    def test_query_by_caller(self):
        tracker = LLMCallTracker()
        tracker.record(LLMCallRecord(
            caller="IntentClassifier", purpose="intent_classification",
            model_used="gpt-4o", tokens_prompt=100, tokens_completion=50,
            latency_ms=300, cost_usd=0.003, case_id="CASE-001",
        ))
        tracker.record(LLMCallRecord(
            caller="DeepAgents.reasoning", purpose="reasoning",
            model_used="o1", tokens_prompt=500, tokens_completion=200,
            latency_ms=2000, cost_usd=0.05, case_id="CASE-001",
        ))
        records = tracker.query_by_caller("IntentClassifier")
        assert len(records) == 1

    def test_query_by_case(self):
        tracker = LLMCallTracker()
        tracker.record(LLMCallRecord(
            caller="test", purpose="test", model_used="mock",
            tokens_prompt=0, tokens_completion=0, latency_ms=0,
            cost_usd=0.0, case_id="CASE-001",
        ))
        records = tracker.query_by_case("CASE-001")
        assert len(records) == 1
        records = tracker.query_by_case("CASE-999")
        assert len(records) == 0

    def test_query_by_purpose(self):
        tracker = LLMCallTracker()
        tracker.record(LLMCallRecord(
            caller="test", purpose="reasoning", model_used="o1",
            tokens_prompt=100, tokens_completion=50, latency_ms=500,
            cost_usd=0.01, case_id="CASE-001",
        ))
        records = tracker.query_by_purpose("reasoning")
        assert len(records) == 1

    def test_clear(self):
        tracker = LLMCallTracker()
        tracker.record(LLMCallRecord(
            caller="test", purpose="test", model_used="mock",
            tokens_prompt=0, tokens_completion=0, latency_ms=0,
            cost_usd=0.0,
        ))
        tracker.clear()
        assert tracker.total_calls == 0
