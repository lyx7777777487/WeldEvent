"""Tests for memory/confidence.py — MemoryConfidenceService (P2-14 fix)."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from cognitiveplane.memory.confidence import MemoryConfidenceService, _SOURCE_WEIGHTS
from cognitiveplane.shared.enums import MemoryType
from cognitiveplane.shared.types import MemoryId


class TestMemoryConfidenceService:
    def setup_method(self):
        self.svc = MemoryConfidenceService()

    def _make_record(self, memory_type=None, feature_vector=None, created_at=None, validation_count=0):
        fv = feature_vector if feature_vector is not None else [1.0, 0.0]
        ca = created_at if created_at is not None else datetime.now(timezone.utc)

        class Content:
            pass

        Content.feature_vector = fv

        class Record:
            pass

        Record.memory_id = MemoryId(value=uuid4())
        Record.memory_type = memory_type
        Record.content = Content()
        Record.created_at = ca
        Record.validation_count = validation_count

        return Record()

    def _make_query(self, feature_vector=None):
        fv = feature_vector if feature_vector is not None else [1.0, 0.0]

        class Query:
            pass

        Query.feature_vector = fv
        return Query()

    def test_identical_vectors_high_confidence(self):
        record = self._make_record(
            memory_type=MemoryType.APPROVED_DECISION,
            feature_vector=[1.0, 0.0],
            validation_count=10,
        )
        query = self._make_query(feature_vector=[1.0, 0.0])
        score = self.svc.calculate(record, query)
        assert score > 0.5

    def test_orthogonal_vectors_lower_confidence(self):
        record = self._make_record(
            memory_type=MemoryType.APPROVED_DECISION,
            feature_vector=[0.0, 1.0],
        )
        query = self._make_query(feature_vector=[1.0, 0.0])
        score = self.svc.calculate(record, query)
        # Cosine similarity of orthogonal = 0, so score should be low
        assert score < 0.3

    def test_source_weight_approved_decision_highest(self):
        assert _SOURCE_WEIGHTS[MemoryType.APPROVED_DECISION] == 0.9

    def test_source_weight_operator_feedback_lower(self):
        assert _SOURCE_WEIGHTS[MemoryType.OPERATOR_FEEDBACK] == 0.6

    def test_source_weight_default_for_none(self):
        record = self._make_record(memory_type=None)
        query = self._make_query()
        # Should still compute, not crash
        score = self.svc.calculate(record, query)
        assert 0.0 <= score <= 1.0

    def test_recency_factor_fresh_is_higher(self):
        fresh = self._make_record(
            memory_type=MemoryType.APPROVED_DECISION,
            created_at=datetime.now(timezone.utc),
            validation_count=5,
        )
        old = self._make_record(
            memory_type=MemoryType.APPROVED_DECISION,
            created_at=datetime.now(timezone.utc) - timedelta(days=60),
            validation_count=5,
        )
        query = self._make_query()
        fresh_score = self.svc.calculate(fresh, query)
        old_score = self.svc.calculate(old, query)
        assert fresh_score > old_score

    def test_validation_count_increases_confidence(self):
        low_validation = self._make_record(
            memory_type=MemoryType.APPROVED_DECISION,
            validation_count=1,
        )
        high_validation = self._make_record(
            memory_type=MemoryType.APPROVED_DECISION,
            validation_count=10,
        )
        query = self._make_query()
        low_score = self.svc.calculate(low_validation, query)
        high_score = self.svc.calculate(high_validation, query)
        assert high_score > low_score

    def test_score_bounded_0_to_1(self):
        record = self._make_record(
            memory_type=MemoryType.APPROVED_DECISION,
            feature_vector=[1.0, 0.0],
            validation_count=100,
        )
        query = self._make_query(feature_vector=[1.0, 0.0])
        score = self.svc.calculate(record, query)
        assert 0.0 <= score <= 1.0

    def test_no_more_constant_05(self):
        """P2-14 fix: confidence is NOT always 0.5."""
        record1 = self._make_record(
            memory_type=MemoryType.APPROVED_DECISION,
            feature_vector=[1.0, 0.0],
            validation_count=10,
        )
        record2 = self._make_record(
            memory_type=MemoryType.OPERATOR_FEEDBACK,
            feature_vector=[0.0, 1.0],
            validation_count=0,
        )
        query = self._make_query(feature_vector=[1.0, 0.0])
        score1 = self.svc.calculate(record1, query)
        score2 = self.svc.calculate(record2, query)
        # Scores must differ (not constant 0.5)
        assert score1 != score2


class TestCosineSimilarity:
    def test_identical_vectors(self):
        score = MemoryConfidenceService._cosine_similarity([1.0, 0.0], [1.0, 0.0])
        assert abs(score - 1.0) < 0.001

    def test_opposite_vectors(self):
        score = MemoryConfidenceService._cosine_similarity([1.0, 0.0], [-1.0, 0.0])
        assert score == 0.0  # Clamped to 0

    def test_orthogonal_vectors(self):
        score = MemoryConfidenceService._cosine_similarity([1.0, 0.0], [0.0, 1.0])
        assert abs(score) < 0.001

    def test_empty_vectors(self):
        score = MemoryConfidenceService._cosine_similarity([], [])
        assert score == 0.5

    def test_mismatched_lengths(self):
        score = MemoryConfidenceService._cosine_similarity([1.0], [1.0, 2.0])
        assert score == 0.5


class TestRecencyFactor:
    def test_fresh_is_near_1(self):
        now = datetime.now(timezone.utc)
        factor = MemoryConfidenceService._recency_factor(now)
        assert factor > 0.99

    def test_30_days_old_is_near_05(self):
        old = datetime.now(timezone.utc) - timedelta(days=30)
        factor = MemoryConfidenceService._recency_factor(old, half_life_days=30.0)
        assert abs(factor - 0.5) < 0.01

    def test_very_old_decays(self):
        old = datetime.now(timezone.utc) - timedelta(days=365)
        factor = MemoryConfidenceService._recency_factor(old)
        assert factor < 0.01
