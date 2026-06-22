"""Tests for memory/promotion.py — promotion path and auto-promotion rules."""

import pytest

from cognitiveplane.memory.promotion import can_auto_promote, next_promotion_status
from cognitiveplane.shared.enums import PromotionStatus


class TestNextPromotionStatus:
    def test_raw_to_validated(self):
        assert next_promotion_status(PromotionStatus.RAW) == PromotionStatus.VALIDATED

    def test_validated_to_promoted(self):
        assert next_promotion_status(PromotionStatus.VALIDATED) == PromotionStatus.PROMOTED

    def test_promoted_is_top(self):
        assert next_promotion_status(PromotionStatus.PROMOTED) is None

    def test_archived_is_top(self):
        assert next_promotion_status(PromotionStatus.ARCHIVED) is None

    def test_under_review_returns_none(self):
        # UNDER_REVIEW is not in the promotion path
        assert next_promotion_status(PromotionStatus.UNDER_REVIEW) is None


class TestCanAutoPromote:
    def test_raw_auto_promotes(self):
        assert can_auto_promote(PromotionStatus.RAW) is True

    def test_validated_not_auto(self):
        assert can_auto_promote(PromotionStatus.VALIDATED) is False

    def test_promoted_not_auto(self):
        assert can_auto_promote(PromotionStatus.PROMOTED) is False
