"""Real confidence scoring — fix P2-14 (no more constant 0.5).

Source: 7-plane redesign spec §10 Memory Confidence Fix.
"""

import math
from datetime import datetime, timezone
from typing import Any

from cognitiveplane.shared.enums import MemoryType


# Source reliability weights by memory type
_SOURCE_WEIGHTS: dict[MemoryType, float] = {
    MemoryType.APPROVED_DECISION: 0.9,
    MemoryType.EXPERIENCE: 0.7,
    MemoryType.STANDARD_PARAMETER: 0.85,
    MemoryType.DEFECT_PATTERN: 0.75,
    MemoryType.OPERATOR_FEEDBACK: 0.6,
}

_DEFAULT_WEIGHT = 0.5


class MemoryConfidenceService:
    """Calculates memory confidence based on match quality.

    Fix P2-14: replaces constant 0.5 with real calculation:
    similarity × source_weight × recency × validation_count
    """

    async def compute(self, context: Any) -> float:
        """Port-compatible entry point — computes confidence from context snapshot."""
        memory_confidence = getattr(context, "memory_match_confidence", None)
        if memory_confidence is not None:
            return float(memory_confidence)
        knowledge_coverage = getattr(context, "knowledge_coverage", 0.5)
        novelty = getattr(context, "event_novelty", None)
        novelty_factor = 0.3 if str(novelty) == "NOVEL" else 0.7 if str(novelty) == "PARTIAL" else 1.0
        score = knowledge_coverage * novelty_factor
        return max(0.0, min(1.0, score))

    def calculate(self, memory_record: Any, query: Any) -> float:
        """Calculate confidence score for a memory record against a query."""
        similarity = self._cosine_similarity(
            getattr(query, 'feature_vector', [0.0]),
            getattr(getattr(memory_record, 'content', None), 'feature_vector', [0.0]),
        )
        source_weight = self._source_weight(getattr(memory_record, 'memory_type', None))
        recency = self._recency_factor(getattr(memory_record, 'created_at', datetime.now(timezone.utc)))
        validation_count = min(getattr(memory_record, 'validation_count', 0) / 5.0, 1.0) \
            if hasattr(memory_record, 'validation_count') and memory_record.validation_count else 0.2

        score = similarity * source_weight * recency * max(validation_count, 0.2)
        return max(0.0, min(1.0, score))

    def _source_weight(self, memory_type: MemoryType | None) -> float:
        if memory_type is None:
            return _DEFAULT_WEIGHT
        return _SOURCE_WEIGHTS.get(memory_type, _DEFAULT_WEIGHT)

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not a or not b or len(a) != len(b):
            return 0.5
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.5
        return max(0.0, min(1.0, dot / (norm_a * norm_b)))

    @staticmethod
    def _recency_factor(created_at: datetime, half_life_days: float = 30.0) -> float:
        """Decay factor: 1.0 when fresh, 0.5 after half_life_days."""
        now = datetime.now(timezone.utc)
        age_days = max(0.0, (now - created_at).total_seconds() / 86400)
        return math.exp(-0.693 * age_days / half_life_days)
