"""Tests for dto_decision/directive.py — SkipRecommendation and EscalationRecommendation."""

from cognitiveplane.shared.dto_decision import (
    SkipRecommendation,
    EscalationRecommendation,
)
from cognitiveplane.shared.dto_decision.outputs import EscalationTarget, EvidenceReference, MitigationSuggestion
from cognitiveplane.shared.dto_decision.assessment import RiskAssessment
from cognitiveplane.shared.enums import RiskLevel, UrgencyLevel


def _make_risk_assessment(risk_level: RiskLevel = RiskLevel.LOW) -> RiskAssessment:
    return RiskAssessment(
        risk_level=risk_level,
        risk_factors=[],
        confidence=0.8,
        mitigation_suggestions=[
            MitigationSuggestion(suggestion="Monitor", effectiveness=0.9)
        ],
    )


class TestSkipRecommendation:
    def test_construction(self):
        rec = SkipRecommendation(
            skippable=True,
            justification="Low risk area",
            risk_if_skipped=_make_risk_assessment(RiskLevel.LOW),
            confidence=0.8,
        )
        assert rec.type == "skip_recommendation"
        assert rec.skippable is True
        assert rec.confidence == 0.8

    def test_serialization_roundtrip(self):
        rec = SkipRecommendation(
            skippable=False,
            justification="Critical weld",
            risk_if_skipped=_make_risk_assessment(RiskLevel.HIGH),
            confidence=0.3,
        )
        data = rec.model_dump()
        restored = SkipRecommendation.model_validate(data)
        assert restored.skippable is False
        assert restored.justification == "Critical weld"


class TestEscalationRecommendation:
    def test_construction(self):
        rec = EscalationRecommendation(
            escalate=True,
            escalation_reason="Safety concern detected",
            escalation_target=EscalationTarget(
                target_type="human",
                target_id="senior-engineer-001",
            ),
            urgency=UrgencyLevel.URGENT,
            supporting_evidence=[
                EvidenceReference(
                    source="validation",
                    reference_id="val-001",
                    description="Safety block triggered",
                )
            ],
        )
        assert rec.type == "escalation_recommendation"
        assert rec.escalate is True
        assert rec.urgency == UrgencyLevel.URGENT

    def test_serialization_roundtrip(self):
        rec = EscalationRecommendation(
            escalate=False,
            escalation_reason="No escalation needed",
            escalation_target=EscalationTarget(
                target_type="system",
                target_id="auto-handler",
            ),
            urgency=UrgencyLevel.ROUTINE,
            supporting_evidence=[],
        )
        data = rec.model_dump()
        restored = EscalationRecommendation.model_validate(data)
        assert restored.escalate is False
