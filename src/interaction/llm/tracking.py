"""LLM call tracking — token/cost/latency metrics.

Source: L1-Interaction-Layer-Business-Requirements.md §3.6.9 (Cost Tracking).
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class LLMCallRecord(BaseModel):
    """A single LLM call record for tracking."""

    caller: str
    purpose: str
    model_used: str
    tokens_prompt: int
    tokens_completion: int
    latency_ms: int
    cost_usd: float
    case_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LLMCallTracker:
    """Tracks LLM call metrics — token usage, cost, latency by caller/case/purpose."""

    def __init__(self, max_records: int = 10000) -> None:
        self._records: list[LLMCallRecord] = []
        self._max_records = max_records

    def record(self, call: LLMCallRecord) -> None:
        self._records.append(call)
        if len(self._records) > self._max_records:
            self._records = self._records[-self._max_records:]

    @property
    def total_calls(self) -> int:
        return len(self._records)

    @property
    def total_tokens(self) -> int:
        return sum(r.tokens_prompt + r.tokens_completion for r in self._records)

    @property
    def total_cost_usd(self) -> float:
        return sum(r.cost_usd for r in self._records)

    def query_by_caller(self, caller: str) -> list[LLMCallRecord]:
        return [r for r in self._records if r.caller == caller]

    def query_by_case(self, case_id: str) -> list[LLMCallRecord]:
        return [r for r in self._records if r.case_id == case_id]

    def query_by_purpose(self, purpose: str) -> list[LLMCallRecord]:
        return [r for r in self._records if r.purpose == purpose]

    def clear(self) -> None:
        self._records.clear()
