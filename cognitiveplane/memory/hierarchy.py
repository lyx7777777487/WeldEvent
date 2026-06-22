"""Memory hierarchy — L0-L5 level definition + promotion rules.

Source: 7-plane redesign spec §10 Hierarchy.
"""

from enum import IntEnum


class MemoryLevel(IntEnum):
    """Memory hierarchy levels L0-L5."""
    L0_REALTIME = 0     # Redis, 5min TTL — current decision context
    L1_WORKING = 1      # Redis, 1h TTL — session working memory
    L2_CASE = 2         # PostgreSQL, permanent — case-level memory
    L3_EXPERIENCE = 3   # PostgreSQL, permanent — cross-case experience
    L4_KNOWLEDGE = 4    # PG + Milvus (Phase2), permanent — structured knowledge
    L5_AUDIT = 5        # PostgreSQL, permanent (immutable) — audit trail


PROMOTION_RULES = {
    (MemoryLevel.L0_REALTIME, MemoryLevel.L1_WORKING): {
        "condition": "Decision completed",
        "review": "Automatic",
    },
    (MemoryLevel.L1_WORKING, MemoryLevel.L2_CASE): {
        "condition": "Session closed",
        "review": "Automatic",
    },
    (MemoryLevel.L2_CASE, MemoryLevel.L3_EXPERIENCE): {
        "condition": "Confidence > 0.8 + 3+ validations",
        "review": "Human review",
    },
    (MemoryLevel.L3_EXPERIENCE, MemoryLevel.L4_KNOWLEDGE): {
        "condition": "Confidence > 0.95 + committee approval",
        "review": "Formal review",
    },
}


def can_promote(from_level: MemoryLevel, confidence: float, validation_count: int) -> bool:
    """Check if memory can be promoted based on rules."""
    if from_level == MemoryLevel.L0_REALTIME:
        return True  # L0→L1 automatic
    if from_level == MemoryLevel.L1_WORKING:
        return True  # L1→L2 automatic
    if from_level == MemoryLevel.L2_CASE:
        return confidence > 0.8 and validation_count >= 3
    if from_level == MemoryLevel.L3_EXPERIENCE:
        return confidence > 0.95  # Committee approval checked separately
    return False
