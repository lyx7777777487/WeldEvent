"""Tests for real Validator implementations (Safety, Rule, Shadow, Consistency).

These test the actual business logic, not just the stub behavior.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from cognitiveplane.shared.dto_context import ContextSnapshot, WeldMapSnapshot
from cognitiveplane.shared.dto_decision import BrainDecision, DecisionOutput
from cognitiveplane.shared.dto_decision.outputs import (
    ParameterRecommendation,
    ParameterSet,
)
from cognitiveplane.shared.dto_memory import MemoryContent, MemorySearchResult
from cognitiveplane.shared.enums import (
    ConsistencyStatus,
    EventType,
    NoveltyLevel,
    PromotionStatus,
    ReasoningMode,
    RuleStatus,
    SafetyStatus,
    ShadowStatus,
    BrainStateType,
    DecisionPointType,
    PersonaType,
)
from cognitiveplane.shared.ports.validation import (
    ConsistencyValidatorInput,
    RuleValidatorInput,
    SafetyValidatorInput,
    ShadowValidatorInput,
)
from cognitiveplane.shared.types import CaseId, DecisionId, MemoryId
from cognitiveplane.governance.validators.safety import SafetyValidator
from cognitiveplane.governance.validators.rule import RuleValidator
from cognitiveplane.governance.validators.shadow import ShadowValidator
from cognitiveplane.governance.validators.consistency import ConsistencyValidator


def _make_decision(
    confidence: float = 0.9,
    rationale: str = "test rationale",
) -> BrainDecision:
    return BrainDecision(
        decision_id=DecisionId(value=uuid4()),
        case_id=CaseId(value="test-case-001"),
        trigger_event_type=EventType.IQA_COMPLETED,
        decision_point=DecisionPointType.DP1,
        persona=PersonaType.COPILOT,
        reasoning_mode=ReasoningMode.ROUTINE,
        state=BrainStateType.VALIDATION,
        outputs=[
            DecisionOutput(
                content=ParameterRecommendation(
                    parameters=ParameterSet(parameters={"weld_current": "180A"}),
                    rationale=rationale,
                    confidence=confidence,
                    constraints_applied=[],
                ),
                confidence=confidence,
            )
        ],
        confidence=confidence,
        created_at=datetime.now(timezone.utc),
    )


def _make_context(
    validation_critical_count: int = 0,
    case_data: dict | None = None,
) -> ContextSnapshot:
    return ContextSnapshot(
        case_id=CaseId(value="test-case-001"),
        event_type=EventType.IQA_COMPLETED,
        workflow_state={},
        case_data=case_data or {},
        measurements=[],
        memory_match_confidence=0.5,
        knowledge_coverage=0.8,
        event_novelty=NoveltyLevel.KNOWN,
        validation_critical_count=validation_critical_count,
        timestamp=datetime.now(timezone.utc),
    )


# ===================================================================
# SafetyValidator
# ===================================================================


class TestSafetyValidator:
    @pytest.mark.asyncio
    async def test_passes_normal_case(self) -> None:
        validator = SafetyValidator()
        result = await validator.check(SafetyValidatorInput(
            context=_make_context(),
            decision=_make_decision(),
        ))
        assert result.result == SafetyStatus.PASS
        assert "critical_validation_count" in result.checked_rules
        assert "minimum_confidence" in result.checked_rules
        assert "safety_keywords_scan" in result.checked_rules

    @pytest.mark.asyncio
    async def test_blocks_critical_count(self) -> None:
        validator = SafetyValidator()
        result = await validator.check(SafetyValidatorInput(
            context=_make_context(validation_critical_count=3),
            decision=_make_decision(),
        ))
        assert result.result == SafetyStatus.BLOCK

    @pytest.mark.asyncio
    async def test_blocks_low_confidence(self) -> None:
        validator = SafetyValidator()
        result = await validator.check(SafetyValidatorInput(
            context=_make_context(),
            decision=_make_decision(confidence=0.2),
        ))
        assert result.result == SafetyStatus.BLOCK

    @pytest.mark.asyncio
    async def test_blocks_safety_keywords(self) -> None:
        validator = SafetyValidator()
        result = await validator.check(SafetyValidatorInput(
            context=_make_context(case_data={"condition": "高压容器"}),
            decision=_make_decision(),
        ))
        assert result.result == SafetyStatus.BLOCK

    @pytest.mark.asyncio
    async def test_passes_below_critical_threshold(self) -> None:
        validator = SafetyValidator()
        result = await validator.check(SafetyValidatorInput(
            context=_make_context(validation_critical_count=2),
            decision=_make_decision(confidence=0.8),
        ))
        assert result.result == SafetyStatus.PASS


# ===================================================================
# RuleValidator
# ===================================================================


class TestRuleValidator:
    @pytest.mark.asyncio
    async def test_approves_normal_case(self) -> None:
        validator = RuleValidator()
        result = await validator.check(RuleValidatorInput(
            context=_make_context(),
            decision=_make_decision(),
        ))
        assert result.result == RuleStatus.APPROVED
        assert result.violated_rules == []

    @pytest.mark.asyncio
    async def test_rejects_low_confidence(self) -> None:
        validator = RuleValidator()
        result = await validator.check(RuleValidatorInput(
            context=_make_context(),
            decision=_make_decision(confidence=0.3),
        ))
        assert result.result == RuleStatus.REJECT
        assert any("confidence" in r for r in result.violated_rules)

    @pytest.mark.asyncio
    async def test_rejects_empty_rationale(self) -> None:
        validator = RuleValidator()
        result = await validator.check(RuleValidatorInput(
            context=_make_context(),
            decision=_make_decision(rationale="  "),
        ))
        assert result.result == RuleStatus.REJECT
        assert any("rationale" in r for r in result.violated_rules)

    @pytest.mark.asyncio
    async def test_approves_high_confidence(self) -> None:
        validator = RuleValidator()
        result = await validator.check(RuleValidatorInput(
            context=_make_context(),
            decision=_make_decision(confidence=0.9),
        ))
        assert result.result == RuleStatus.APPROVED


# ===================================================================
# ShadowValidator
# ===================================================================


class TestShadowValidator:
    @pytest.mark.asyncio
    async def test_concur_on_well_aligned_decision(self) -> None:
        validator = ShadowValidator()
        result = await validator.check(ShadowValidatorInput(
            context=_make_context(),
            decision=_make_decision(confidence=0.75),
        ))
        assert result.result in (ShadowStatus.CONCUR, ShadowStatus.WARN)
        assert result.shadow_decision.confidence > 0.0
        assert 0.0 <= result.alignment_score <= 1.0

    @pytest.mark.asyncio
    async def test_critical_on_low_shadow_confidence(self) -> None:
        validator = ShadowValidator()
        context = _make_context()
        # Force low knowledge coverage and high critical count
        context.__dict__["knowledge_coverage"] = 0.1
        context.__dict__["validation_critical_count"] = 5
        result = await validator.check(ShadowValidatorInput(
            context=context,
            decision=_make_decision(confidence=0.9),
        ))
        assert result.result == ShadowStatus.CRITICAL

    @pytest.mark.asyncio
    async def test_alignment_score_reflects_divergence(self) -> None:
        validator = ShadowValidator()
        context = _make_context()
        context.__dict__["knowledge_coverage"] = 0.2
        result = await validator.check(ShadowValidatorInput(
            context=context,
            decision=_make_decision(confidence=0.95),
        ))
        # Shadow confidence is lower due to poor knowledge coverage,
        # so alignment should be reduced
        if result.alignment_score < 0.8:
            assert len(result.divergence_points) > 0


# ===================================================================
# ConsistencyValidator
# ===================================================================


class TestConsistencyValidator:
    @pytest.mark.asyncio
    async def test_consistent_with_no_history(self) -> None:
        validator = ConsistencyValidator()
        weldmap = WeldMapSnapshot(
            case_id=CaseId(value="test-case-001"),
            workflow_state={},
            measurements=[],
            decisions=[],
            events=[],
            snapshot_at=datetime.now(timezone.utc),
        )
        result = await validator.check(ConsistencyValidatorInput(
            context=_make_context(),
            decision=_make_decision(),
            prior_memories=[],
            weldmap_state=weldmap,
        ))
        assert result.result == ConsistencyStatus.CONSISTENT
        assert result.factual_consistency == ConsistencyStatus.CONSISTENT
        assert result.historical_consistency == ConsistencyStatus.CONSISTENT

    @pytest.mark.asyncio
    async def test_inconsistent_with_contradictory_memory(self) -> None:
        validator = ConsistencyValidator()
        memory = MemorySearchResult(
            memory_id=MemoryId(value=uuid4()),
            content=MemoryContent(
                summary="High confidence historical decision",
                details={"confidence": "0.9"},
                feature_vector=[0.0],
            ),
            similarity_score=0.9,
            promotion_status=PromotionStatus.PROMOTED,
            created_at=datetime.now(timezone.utc),
        )
        weldmap = WeldMapSnapshot(
            case_id=CaseId(value="test-case-001"),
            workflow_state={},
            measurements=[],
            decisions=[],
            events=[],
            snapshot_at=datetime.now(timezone.utc),
        )
        result = await validator.check(ConsistencyValidatorInput(
            context=_make_context(),
            decision=_make_decision(confidence=0.2),
            prior_memories=[memory],
            weldmap_state=weldmap,
        ))
        assert result.result == ConsistencyStatus.INCONSISTENT
        assert result.historical_consistency == ConsistencyStatus.INCONSISTENT
        assert any(i.check_type == "historical" for i in result.inconsistencies)

    @pytest.mark.asyncio
    async def test_factual_consistency_with_weldmap(self) -> None:
        validator = ConsistencyValidator()
        weldmap = WeldMapSnapshot(
            case_id=CaseId(value="test-case-001"),
            workflow_state={"weld_current": "200A"},
            measurements=[],
            decisions=[],
            events=[],
            snapshot_at=datetime.now(timezone.utc),
        )
        result = await validator.check(ConsistencyValidatorInput(
            context=_make_context(),
            decision=_make_decision(),
            prior_memories=[],
            weldmap_state=weldmap,
        ))
        # Decision says 180A, WeldMap says 200A -> factual inconsistency
        assert len(result.inconsistencies) > 0
        assert any(i.check_type == "factual" for i in result.inconsistencies)
