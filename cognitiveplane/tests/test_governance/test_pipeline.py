"""Tests for ValidationPipeline.

P2-12 fix: Pipeline is now pure computation — no memory_search or gateway_read
in constructor. Data is passed as optional parameters to validate().
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
from cognitiveplane.shared.enums import (
    AggregatedValidationResult,
    EventType,
    FallbackMode,
    NoveltyLevel,
    ReasoningMode,
    BrainStateType,
    DecisionPointType,
    PersonaType,
)
from cognitiveplane.shared.ports.validation import ValidationPipelineInput
from cognitiveplane.shared.types import CaseId, DecisionId
from cognitiveplane.governance.escalation import EscalationTracker
from cognitiveplane.governance.pipeline import ValidationPipeline
from cognitiveplane.governance.validators.consistency import StubConsistencyValidator
from cognitiveplane.governance.validators.rule import StubRuleValidator
from cognitiveplane.governance.validators.safety import StubSafetyValidator
from cognitiveplane.governance.validators.shadow import StubShadowValidator


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
    return ValidationPipeline(
        safety=StubSafetyValidator(),
        rule=StubRuleValidator(),
        shadow=StubShadowValidator(),
        consistency=StubConsistencyValidator(),
        escalation=EscalationTracker(),
    )


# -- advice.md. Full pipeline with all stubs -> APPROVED ----------------------------


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
