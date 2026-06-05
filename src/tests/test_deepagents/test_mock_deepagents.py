"""Tests for MockDeepAgentsAdapter.

Covers: reason() returns ReasoningOutput with confidence in [0,1],
plan() returns PlanningOutput with at least one step,
reflect() returns ReflectionOutput with non-empty reflection,
explain() returns ExplanationOutput with non-empty explanation,
utilize_memory() returns MemoryUtilizationOutput with empty lists.
"""

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from src.shared.dto_context import ContextSnapshot
from src.shared.dto_decision import BrainDecision, DecisionOutput
from src.shared.dto_memory import MemorySearchResult, MemoryContent
from src.shared.dto_deepagents import SituationDescription
from src.shared.dto_validation import ValidationResult, SafetyValidationResult
from src.shared.enums import (
    AggregatedValidationResult,
    BrainStateType,
    DecisionPointType,
    EventType,
    NoveltyLevel,
    PersonaType,
    ReasoningMode,
    SafetyStatus,
)
from src.shared.ports.deepagents import (
    ExplanationInput,
    MemoryUtilizationInput,
    PlanningInput,
    ReasoningInput,
    ReflectionInput,
)
from src.shared.types import CaseId, DecisionId, EventId, MemoryId, ValidationId
from src.deepagents.adapters.mock import MockDeepAgentsAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_case_id() -> CaseId:
    return CaseId(value="CASE-001")


def _make_decision_id() -> DecisionId:
    return DecisionId(value=uuid4())


def _make_context_snapshot() -> ContextSnapshot:
    return ContextSnapshot(
        case_id=_make_case_id(),
        event_type=EventType.IQA_COMPLETED,
        workflow_state={},
        case_data={},
        measurements=[],
        memory_match_confidence=0.5,
        knowledge_coverage=0.5,
        event_novelty=NoveltyLevel.KNOWN,
        validation_critical_count=0,
        timestamp=datetime.now(timezone.utc),
    )


def _make_brain_decision() -> BrainDecision:
    return BrainDecision(
        decision_id=_make_decision_id(),
        case_id=_make_case_id(),
        trigger_event_type=EventType.IQA_COMPLETED,
        decision_point=DecisionPointType.DP1,
        persona=PersonaType.COPILOT,
        reasoning_mode=ReasoningMode.ROUTINE,
        state=BrainStateType.DECISION_GENERATION,
        outputs=[],
        confidence=0.85,
        created_at=datetime.now(timezone.utc),
    )


def _make_validation_result() -> ValidationResult:
    return ValidationResult(
        validation_id=ValidationId(value=uuid4()),
        decision_id=_make_decision_id(),
        safety_result=SafetyValidationResult(
            result=SafetyStatus.PASS,
            checked_rules=[],
            timestamp=datetime.now(timezone.utc),
        ),
        aggregated_result=AggregatedValidationResult.APPROVED,
        stages=[],
        total_duration_ms=0,
        timestamp=datetime.now(timezone.utc),
    )


def _make_memory_search_result() -> MemorySearchResult:
    from src.shared.enums import PromotionStatus

    return MemorySearchResult(
        memory_id=MemoryId(value=uuid4()),
        content=MemoryContent(
            summary="test",
            details={},
            feature_vector=[0.1],
        ),
        similarity_score=0.8,
        promotion_status=PromotionStatus.PROMOTED,
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> MockDeepAgentsAdapter:
    return MockDeepAgentsAdapter()


# ===================================================================
# Test cases
# ===================================================================


class TestReason:
    """reason() returns ReasoningOutput with confidence in [0,1]."""

    @pytest.mark.asyncio
    async def test_reason_returns_valid_output(self, adapter: MockDeepAgentsAdapter):
        input_data = ReasoningInput(
            context=_make_context_snapshot(),
            question="What should we do?",
            knowledge_results=[],
            memory_results=[],
        )
        output = await adapter.reason(input_data)
        assert 0.0 <= output.confidence <= 1.0
        assert output.reasoning_trace == "mock_reasoning_trace"
        assert len(output.conclusions) == 1


class TestPlan:
    """plan() returns PlanningOutput with at least one step."""

    @pytest.mark.asyncio
    async def test_plan_returns_at_least_one_step(
        self, adapter: MockDeepAgentsAdapter
    ):
        from src.shared.dto_decision.outputs import Constraint

        input_data = PlanningInput(
            context=_make_context_snapshot(),
            objective="Optimise weld parameters",
            constraints=[Constraint(name="heat", value="1.5", source="standard")],
            available_strategies=[],
        )
        output = await adapter.plan(input_data)
        assert len(output.plan.steps) >= 1
        assert output.plan.expected_outcome == "mock_outcome"


class TestReflect:
    """reflect() returns ReflectionOutput with non-empty reflection."""

    @pytest.mark.asyncio
    async def test_reflect_returns_non_empty_reflection(
        self, adapter: MockDeepAgentsAdapter
    ):
        input_data = ReflectionInput(
            context=_make_context_snapshot(),
            decision=_make_brain_decision(),
            validation_result=_make_validation_result(),
        )
        output = await adapter.reflect(input_data)
        assert len(output.reflection) > 0
        assert output.identified_gaps == []
        assert output.improvement_suggestions == []


class TestExplain:
    """explain() returns ExplanationOutput with non-empty explanation."""

    @pytest.mark.asyncio
    async def test_explain_returns_non_empty_explanation(
        self, adapter: MockDeepAgentsAdapter
    ):
        from src.shared.enums import AudienceType

        input_data = ExplanationInput(
            context=_make_context_snapshot(),
            decision=_make_brain_decision(),
            audience=AudienceType.OPERATOR,
        )
        output = await adapter.explain(input_data)
        assert len(output.explanation) > 0
        assert output.key_factors == []


class TestUtilizeMemory:
    """utilize_memory() returns MemoryUtilizationOutput with empty lists."""

    @pytest.mark.asyncio
    async def test_utilize_memory_returns_empty_lists(
        self, adapter: MockDeepAgentsAdapter
    ):
        input_data = MemoryUtilizationInput(
            context=_make_context_snapshot(),
            memory_results=[_make_memory_search_result()],
            current_situation=SituationDescription(
                current_state={},
                relevant_factors=[],
                constraints=[],
            ),
        )
        output = await adapter.utilize_memory(input_data)
        assert output.relevant_experiences == []
        assert output.applicability_assessment == []
        assert output.adaptation_suggestions == []
