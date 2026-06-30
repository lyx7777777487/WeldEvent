"""Tests for control/{planner,reflector,sub_agent,fallback}.py."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from cognitiveplane.control.fallback import (
    FallbackSelector,
    InteractionTier,
    TierCapabilities,
    degrade,
)
from cognitiveplane.control.planner import Planner, PlannerInput
from cognitiveplane.control.reflector import ReflectionInput, Reflector
from cognitiveplane.control.sub_agent import (
    SubAgentDelegator,
    SubAgentResult,
    SubAgentTask,
)
from cognitiveplane.shared.dto.collaboration import FeedbackContent
from cognitiveplane.shared.dto.context import ContextSnapshot
from cognitiveplane.shared.dto_decision import BrainDecision, DecisionOutput
from cognitiveplane.shared.dto_decision.outputs import (
    ParameterRecommendation,
    ParameterSet,
)
from cognitiveplane.shared.dto.validation import (
    SafetyValidationResult,
    ValidationResult,
)
from cognitiveplane.shared.enums import (
    AggregatedValidationResult,
    BrainStateType,
    DecisionPointType,
    EventType,
    NoveltyLevel,
    PersonaType,
    ReasoningMode,
    SafetyStatus,
)
from cognitiveplane.shared.types import CaseId, DecisionId, ValidationId


def _ctx() -> ContextSnapshot:
    return ContextSnapshot(
        case_id=CaseId(value="case-1"),
        event_type=EventType.WORKFLOW_ENTERED,
        workflow_state={},
        case_data={},
        measurements=[],
        memory_match_confidence=0.0,
        knowledge_coverage=0.0,
        event_novelty=NoveltyLevel.PARTIAL,
        validation_critical_count=0,
        timestamp=datetime.now(timezone.utc),
    )


def _decision(confidence: float = 0.8) -> BrainDecision:
    return BrainDecision(
        decision_id=DecisionId(value=uuid4()),
        case_id=CaseId(value="case-1"),
        trigger_event_type=EventType.WORKFLOW_ENTERED,
        decision_point=DecisionPointType.DP0,
        persona=PersonaType.COPILOT,
        reasoning_mode=ReasoningMode.ROUTINE,
        state=BrainStateType.VALIDATION,
        outputs=[
            DecisionOutput(
                content=ParameterRecommendation(
                    parameters=ParameterSet(parameters={"current": "120A"}),
                    rationale="baseline",
                    confidence=confidence,
                    constraints_applied=[],
                ),
                confidence=confidence,
            )
        ],
        confidence=confidence,
        created_at=datetime.now(timezone.utc),
    )


def _validation(
    agg: AggregatedValidationResult, safety: SafetyStatus = SafetyStatus.PASS
) -> ValidationResult:
    now = datetime.now(timezone.utc)
    return ValidationResult(
        validation_id=ValidationId(value=uuid4()),
        decision_id=DecisionId(value=uuid4()),
        safety_result=SafetyValidationResult(
            result=safety, checked_rules=[], timestamp=now
        ),
        aggregated_result=agg,
        stages=[],
        total_duration_ms=0,
        timestamp=now,
    )


class TestPlanner:
    def test_basic_plan(self):
        out = Planner.plan(
            PlannerInput(
                context=_ctx(),
                objective="Inspect weld seam",
                constraints=[],
                available_strategies=[],
                knowledge_results=[],
                memory_results=[],
            )
        )
        assert len(out.plan.steps) >= 3
        assert out.plan.expected_outcome
        assert 0.0 <= out.plan.confidence <= 1.0

    def test_evidence_increases_confidence(self):
        ctx = _ctx()

        class MR:
            confidence = 0.9
            content = "match"

        out_no_evidence = Planner.plan(
            PlannerInput(ctx, "obj", [], [], [], [])
        )
        out_with_evidence = Planner.plan(
            PlannerInput(ctx, "obj", [], [], [None, None, None], [MR(), MR(), MR()])
        )
        assert out_with_evidence.confidence > out_no_evidence.confidence


class TestReflector:
    def test_rejected_produces_critical_gap(self):
        out = Reflector.reflect(
            ReflectionInput(
                context=_ctx(),
                decision=_decision(),
                validation_result=_validation(AggregatedValidationResult.REJECTED),
            )
        )
        assert len(out.identified_gaps) >= 1
        assert any("rejected" in g.description.lower() for g in out.identified_gaps)
        assert len(out.improvement_suggestions) >= 1

    def test_safety_block_flags_critical(self):
        out = Reflector.reflect(
            ReflectionInput(
                context=_ctx(),
                decision=_decision(),
                validation_result=_validation(
                    AggregatedValidationResult.ESCALATED, safety=SafetyStatus.BLOCK
                ),
            )
        )
        assert any("Safety BLOCK" in g.description for g in out.identified_gaps)

    def test_low_confidence_flagged(self):
        out = Reflector.reflect(
            ReflectionInput(
                context=_ctx(),
                decision=_decision(confidence=0.3),
                validation_result=_validation(AggregatedValidationResult.APPROVED),
            )
        )
        assert any("Low decision confidence" in g.description for g in out.identified_gaps)


class TestSubAgent:
    @pytest.mark.asyncio
    async def test_delegate_to_registered(self):
        delegator = SubAgentDelegator()

        async def standard_expert(task: SubAgentTask) -> SubAgentResult:
            return SubAgentResult(
                agent="standards", success=True, output={"echo": task.payload}
            )

        delegator.register("standards", standard_expert)
        assert delegator.is_registered("standards")

        result = await delegator.delegate(
            SubAgentTask(name="standards", payload={"q": "ISO 5817"})
        )
        assert result.success
        assert result.output["echo"]["q"] == "ISO 5817"

    @pytest.mark.asyncio
    async def test_delegate_to_unknown_returns_failure(self):
        delegator = SubAgentDelegator()
        result = await delegator.delegate(SubAgentTask(name="missing"))
        assert not result.success
        assert "No sub-agent" in (result.error or "")

    @pytest.mark.asyncio
    async def test_exception_caught(self):
        delegator = SubAgentDelegator()

        async def boom(task: SubAgentTask) -> SubAgentResult:
            raise RuntimeError("kaboom")

        delegator.register("boom", boom)
        result = await delegator.delegate(SubAgentTask(name="boom"))
        assert not result.success
        assert "kaboom" in (result.error or "")


class TestFallback:
    def test_select_react_when_function_calling(self):
        tier = FallbackSelector.select(
            TierCapabilities(has_function_calling_llm=True, has_structured_llm=True)
        )
        assert tier == InteractionTier.REACT_FUNCTION_CALLING

    def test_select_structured_when_no_function_calling(self):
        tier = FallbackSelector.select(
            TierCapabilities(has_function_calling_llm=False, has_structured_llm=True)
        )
        assert tier == InteractionTier.STRUCTURED_OUTPUT

    def test_select_embedding_rules_when_nothing(self):
        tier = FallbackSelector.select(TierCapabilities())
        assert tier == InteractionTier.EMBEDDING_RULES

    def test_force_tier(self):
        tier = FallbackSelector.select(
            TierCapabilities(
                has_function_calling_llm=True,
                force_tier=InteractionTier.EMBEDDING_RULES,
            )
        )
        assert tier == InteractionTier.EMBEDDING_RULES

    def test_degrade(self):
        assert degrade(InteractionTier.REACT_FUNCTION_CALLING) == InteractionTier.STRUCTURED_OUTPUT
        assert degrade(InteractionTier.STRUCTURED_OUTPUT) == InteractionTier.EMBEDDING_RULES
        assert degrade(InteractionTier.EMBEDDING_RULES) == InteractionTier.EMBEDDING_RULES
