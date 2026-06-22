"""Tests for memory/hierarchy.py — MemoryLevel, promotion rules, can_promote."""

import pytest

from cognitiveplane.memory.hierarchy import MemoryLevel, PROMOTION_RULES, can_promote


class TestMemoryLevel:
    def test_level_ordering(self):
        assert MemoryLevel.L0_REALTIME < MemoryLevel.L1_WORKING
        assert MemoryLevel.L1_WORKING < MemoryLevel.L2_CASE
        assert MemoryLevel.L2_CASE < MemoryLevel.L3_EXPERIENCE
        assert MemoryLevel.L3_EXPERIENCE < MemoryLevel.L4_KNOWLEDGE
        assert MemoryLevel.L4_KNOWLEDGE < MemoryLevel.L5_AUDIT

    def test_level_values(self):
        assert MemoryLevel.L0_REALTIME == 0
        assert MemoryLevel.L1_WORKING == 1
        assert MemoryLevel.L2_CASE == 2
        assert MemoryLevel.L3_EXPERIENCE == 3
        assert MemoryLevel.L4_KNOWLEDGE == 4
        assert MemoryLevel.L5_AUDIT == 5


class TestPromotionRules:
    def test_l0_to_l1_exists(self):
        assert (MemoryLevel.L0_REALTIME, MemoryLevel.L1_WORKING) in PROMOTION_RULES

    def test_l1_to_l2_exists(self):
        assert (MemoryLevel.L1_WORKING, MemoryLevel.L2_CASE) in PROMOTION_RULES

    def test_l2_to_l3_exists(self):
        assert (MemoryLevel.L2_CASE, MemoryLevel.L3_EXPERIENCE) in PROMOTION_RULES

    def test_l3_to_l4_exists(self):
        assert (MemoryLevel.L3_EXPERIENCE, MemoryLevel.L4_KNOWLEDGE) in PROMOTION_RULES

    def test_l2_to_l3_requires_human_review(self):
        rule = PROMOTION_RULES[(MemoryLevel.L2_CASE, MemoryLevel.L3_EXPERIENCE)]
        assert rule["review"] == "Human review"

    def test_l3_to_l4_requires_formal_review(self):
        rule = PROMOTION_RULES[(MemoryLevel.L3_EXPERIENCE, MemoryLevel.L4_KNOWLEDGE)]
        assert rule["review"] == "Formal review"


class TestCanPromote:
    def test_l0_always_promotes(self):
        assert can_promote(MemoryLevel.L0_REALTIME, confidence=0.1, validation_count=0) is True

    def test_l1_always_promotes(self):
        assert can_promote(MemoryLevel.L1_WORKING, confidence=0.1, validation_count=0) is True

    def test_l2_requires_confidence_and_validations(self):
        assert can_promote(MemoryLevel.L2_CASE, confidence=0.9, validation_count=5) is True
        assert can_promote(MemoryLevel.L2_CASE, confidence=0.7, validation_count=5) is False
        assert can_promote(MemoryLevel.L2_CASE, confidence=0.9, validation_count=2) is False

    def test_l3_requires_high_confidence(self):
        assert can_promote(MemoryLevel.L3_EXPERIENCE, confidence=0.96, validation_count=0) is True
        assert can_promote(MemoryLevel.L3_EXPERIENCE, confidence=0.94, validation_count=0) is False

    def test_l4_cannot_promote(self):
        assert can_promote(MemoryLevel.L4_KNOWLEDGE, confidence=1.0, validation_count=100) is False

    def test_l5_cannot_promote(self):
        assert can_promote(MemoryLevel.L5_AUDIT, confidence=1.0, validation_count=100) is False
