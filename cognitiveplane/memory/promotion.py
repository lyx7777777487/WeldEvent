"""Memory promotion — RAW → VALIDATED → PROMOTED.

Source: 7-plane redesign spec §10 Promotion Rules.
"""

from cognitiveplane.shared.enums import PromotionStatus


PROMOTION_PATH = [
    PromotionStatus.RAW,
    PromotionStatus.VALIDATED,
    PromotionStatus.PROMOTED,
]


def next_promotion_status(current: PromotionStatus) -> PromotionStatus | None:
    """Get next promotion status, or None if already at top."""
    try:
        idx = PROMOTION_PATH.index(current)
        if idx < len(PROMOTION_PATH) - 1:
            return PROMOTION_PATH[idx + 1]
    except ValueError:
        pass
    return None


def can_auto_promote(current: PromotionStatus) -> bool:
    """Check if promotion from current status is automatic."""
    return current == PromotionStatus.RAW  # RAW→VALIDATED is automatic
