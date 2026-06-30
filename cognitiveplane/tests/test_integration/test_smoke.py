"""Integration smoke test — wires up all skeleton components and validates
that they can be instantiated, their port methods are callable, and the
BrainStateMachine ROUTINE transition path works end-to-end.
"""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# Adapters & repositories
# ---------------------------------------------------------------------------
from cognitiveplane.gateway.adapters.in_memory import InMemoryGatewayAdapter
from cognitiveplane.knowledge.adapters.stub import StubKnowledgeAdapter
from cognitiveplane.knowledge.repositories.in_memory import InMemoryKnowledgeRepository
from cognitiveplane.memory.repositories.in_memory import InMemoryMemoryRepository
from cognitiveplane.memory.adapters.port_adapters import MemorySearchAdapter
from cognitiveplane.gateway.pipeline import ValidationPipeline
from cognitiveplane.governance.escalation import EscalationTracker
from cognitiveplane.governance.validators.safety import SafetyValidator
from cognitiveplane.governance.validators.rule import RuleValidator
from cognitiveplane.governance.validators.shadow import ShadowValidator
from cognitiveplane.governance.validators.consistency import ConsistencyValidator
from cognitiveplane.governance.repositories import InMemoryValidationResultRepository
from cognitiveplane.control.repositories.in_memory import InMemoryBrainDecisionRepository
from cognitiveplane.governance.collaboration.repositories.in_memory import (
    InMemoryHumanReviewRequestRepository,
)
from cognitiveplane.memory.learning.repositories.in_memory import InMemoryLearningEventRepository

# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
from cognitiveplane.control.state_machine import BrainStateMachine

# ---------------------------------------------------------------------------
# Enums & types
# ---------------------------------------------------------------------------
from cognitiveplane.shared.enums import (
    BrainStateType,
    BrainTrigger,
    EventType,
    NoveltyLevel,
    ReasoningMode,
    DecisionPointType,
    PersonaType,
    AggregatedValidationResult,
)

from cognitiveplane.shared.types import CaseId, DecisionId, EventId

# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------
from cognitiveplane.shared.dto.context import DomainEvent, ContextSnapshot
from cognitiveplane.shared.dto_decision import BrainDecision
from cognitiveplane.shared.ports.validation import ValidationPipelineInput
from cognitiveplane.shared.ports.knowledge import RAGQueryInput
from cognitiveplane.shared.dto.knowledge import RAGQuery


# =====================================================================
# Helpers
# =====================================================================


def _make_domain_event() -> DomainEvent:
    return DomainEvent(
        event_id=EventId(value=uuid4()),
        event_type=EventType.WORKFLOW_ENTERED,
        case_id=CaseId(value="smoke-test-case-001"),
        timestamp=datetime.now(timezone.utc),
        source="smoke_test",
        payload={
            "workflow_type": "FULL",
            "case_type": "weld_inspection",
            "parameters": {"param_a": 1.0},
            "operator_id": "op-001",
        },
    )


def _make_context_snapshot() -> ContextSnapshot:
    return ContextSnapshot(
        case_id=CaseId(value="smoke-test-case-001"),
        event_type=EventType.WORKFLOW_ENTERED,
        workflow_state={},
        case_data={},
        measurements=[],
        memory_match_confidence=0.9,
        knowledge_coverage=0.8,
        event_novelty=NoveltyLevel.KNOWN,
        validation_critical_count=0,
        timestamp=datetime.now(timezone.utc),
    )


def _make_brain_decision() -> BrainDecision:
    return BrainDecision(
        decision_id=DecisionId(value=uuid4()),
        case_id=CaseId(value="smoke-test-case-001"),
        trigger_event_type=EventType.WORKFLOW_ENTERED,
        decision_point=DecisionPointType.DP0,
        persona=PersonaType.PLANNER,
        reasoning_mode=ReasoningMode.ROUTINE,
        state=BrainStateType.VALIDATION,
        outputs=[],
        confidence=0.9,
        created_at=datetime.now(timezone.utc),
    )


# =====================================================================
# Test
# =====================================================================


class TestIntegrationSmoke:
    """Smoke test: instantiate all components, drive state machine through
    the ROUTINE path, and verify port methods are callable."""

    @pytest.mark.asyncio
    async def test_full_smoke(self) -> None:
        # ---- 1. Instantiate all adapters and repositories ----
        gateway = InMemoryGatewayAdapter()
        knowledge_adapter = StubKnowledgeAdapter()
        memory_repo = InMemoryMemoryRepository()
        memory_search = MemorySearchAdapter(memory_repo)
        decision_repo = InMemoryBrainDecisionRepository()
        validation_result_repo = InMemoryValidationResultRepository()
        human_review_repo = InMemoryHumanReviewRequestRepository()
        learning_event_repo = InMemoryLearningEventRepository()
        knowledge_repo = InMemoryKnowledgeRepository()

        # Real validators
        safety_validator = SafetyValidator()
        rule_validator = RuleValidator()
        shadow_validator = ShadowValidator()
        consistency_validator = ConsistencyValidator()

        # Escalation tracker
        escalation_tracker = EscalationTracker()

        # Validation pipeline — pure computation (P2-12 fix: no memory/gateway deps)
        pipeline = ValidationPipeline(
            safety=safety_validator,
            rule=rule_validator,
            shadow=shadow_validator,
            consistency=consistency_validator,
            escalation=escalation_tracker,
        )

        # ---- 2. Construct a minimal DomainEvent ----
        event = _make_domain_event()
        assert event.event_type == EventType.WORKFLOW_ENTERED

        # ---- 3. Drive BrainStateMachine through the ROUTINE path ----
        state = BrainStateMachine.transition(
            BrainStateType.IDLE, BrainTrigger.EVENT_DEQUEUED
        )
        assert state == BrainStateType.OBSERVING

        state = BrainStateMachine.transition(
            state, BrainTrigger.CONTEXT_LOADED_ROUTINE
        )
        assert state == BrainStateType.MEMORY_RETRIEVAL

        state = BrainStateMachine.transition(
            state, BrainTrigger.MEMORY_RECEIVED_ROUTINE
        )
        assert state == BrainStateType.MEMORY_MATCHING

        state = BrainStateMachine.transition(state, BrainTrigger.MATCH_PRODUCED)
        assert state == BrainStateType.VALIDATION

        state = BrainStateMachine.transition(state, BrainTrigger.APPROVED)
        assert state == BrainStateType.PUBLICATION

        state = BrainStateMachine.transition(
            state, BrainTrigger.PUBLISHED_ROUTINE_APPROVED
        )

        # ---- 4. Assert final returned state is IDLE ----
        assert state == BrainStateType.IDLE

        # ---- 5. Call StubKnowledgeAdapter.rag_query() and assert it returns [] ----
        rag_input = RAGQueryInput(
            query=RAGQuery(query_text="smoke test query")
        )
        rag_output = await knowledge_adapter.query(rag_input)
        assert rag_output.results == []

        # ---- 6. Assert InMemoryGatewayAdapter has a brain_writes entry
        #       from publish_decision() ----
        decision = _make_brain_decision()
        publish_result = await gateway.publish_decision(decision)
        assert publish_result.success is True
        expected_path = f"/decisions/{decision.decision_id.value}"
        assert expected_path in gateway._brain_writes

        # ---- 7. Call pipeline.validate() with minimal input
        #       and assert it returns APPROVED ----
        pipeline_input = ValidationPipelineInput(
            context=_make_context_snapshot(),
            decision=_make_brain_decision(),
            reasoning_mode=ReasoningMode.ROUTINE,
        )
        pipeline_output = await pipeline.validate(pipeline_input)
        assert (
            pipeline_output.validation_result.aggregated_result
            == AggregatedValidationResult.APPROVED
        )
