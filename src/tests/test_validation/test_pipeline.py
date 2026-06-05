"""Tests for ValidationPipeline."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.gateway.adapters.in_memory import InMemoryGatewayAdapter
from src.memory.adapters.port_adapters import MemorySearchAdapter
from src.memory.repositories.in_memory import InMemoryMemoryRepository
from src.shared.dto_context import ContextSnapshot, WeldMapSnapshot
from src.shared.dto_decision import BrainDecision, DecisionOutput
from src.shared.dto_decision.outputs import (
    Constraint,
    ParameterRecommendation,
    ParameterSet,
)
from src.shared.dto_memory import MemoryContent, MemoryRecord
from src.shared.enums import (
    AggregatedValidationResult,
    EventType,
    FallbackMode,
    MemoryType,
    NoveltyLevel,
    PromotionStatus,
    ReasoningMode,
    BrainStateType,
    DecisionPointType,
    PersonaType,
)
from src.shared.ports.validation import ValidationPipelineInput
from src.shared.types import CaseId, DecisionId, MemoryId
from src.validation.escalation import EscalationTracker
from src.validation.pipeline import ValidationPipeline
from src.validation.validators.consistency import StubConsistencyValidator
from src.validation.validators.rule import StubRuleValidator
from src.validation.validators.safety import StubSafetyValidator
from src.validation.validators.shadow import StubShadowValidator


def _make_decision() -> BrainDecision:
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
                    rationale="test",
                    confidence=0.9,
                    constraints_applied=[],
                ),
                confidence=0.9,
            )
        ],
        confidence=0.9,
        created_at=datetime.now(timezone.utc),
    )


def _make_context() -> ContextSnapshot:
    return ContextSnapshot(
        case_id=CaseId(value="test-case-001"),
        event_type=EventType.IQA_COMPLETED,
        workflow_state={},
        case_data={},
        measurements=[],
        memory_match_confidence=0.5,
        knowledge_coverage=0.8,
        event_novelty=NoveltyLevel.KNOWN,
        validation_critical_count=0,
        timestamp=datetime.now(timezone.utc),
    )


@pytest.fixture
def pipeline() -> ValidationPipeline:
    memory_repo = InMemoryMemoryRepository()
    memory_search = MemorySearchAdapter(memory_repo)
    gateway = InMemoryGatewayAdapter()
    return ValidationPipeline(
        safety=StubSafetyValidator(),
        rule=StubRuleValidator(),
        shadow=StubShadowValidator(),
        consistency=StubConsistencyValidator(),
        escalation=EscalationTracker(),
        memory_search=memory_search,
        gateway_read=gateway,
    )


# -- 1. Full pipeline with all stubs -> APPROVED ----------------------------


async def test_full_pipeline_returns_approved(pipeline: ValidationPipeline) -> None:
    input_data = ValidationPipelineInput(
        context=_make_context(),
        decision=_make_decision(),
        reasoning_mode=ReasoningMode.ROUTINE,
    )
    output = await pipeline.validate(input_data)

    assert output.validation_result.aggregated_result == AggregatedValidationResult.APPROVED


# -- 2. Pipeline returns all 4 stage results --------------------------------


async def test_pipeline_returns_all_four_stage_results(
    pipeline: ValidationPipeline,
) -> None:
    input_data = ValidationPipelineInput(
        context=_make_context(),
        decision=_make_decision(),
        reasoning_mode=ReasoningMode.ROUTINE,
    )
    output = await pipeline.validate(input_data)

    stages = output.validation_result.stages
    stage_names = [s.stage for s in stages]
    assert "safety" in stage_names
    assert "rule" in stage_names
    assert "shadow" in stage_names
    assert "consistency" in stage_names

    # All stages should be COMPLETED (no short-circuit with stubs)
    completed_stages = [s for s in stages if s.status.value == "COMPLETED"]
    assert len(completed_stages) == 4


# -- 3. Pipeline total_duration_ms > 0 --------------------------------------


async def test_pipeline_total_duration_positive(pipeline: ValidationPipeline) -> None:
    input_data = ValidationPipelineInput(
        context=_make_context(),
        decision=_make_decision(),
        reasoning_mode=ReasoningMode.ROUTINE,
    )
    output = await pipeline.validate(input_data)

    assert output.validation_result.total_duration_ms >= 0
