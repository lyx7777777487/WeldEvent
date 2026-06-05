"""Integration smoke test — wires up all skeleton components and validates
that they can be instantiated, their port methods are callable, and the
BrainStateMachine ROUTINE transition path works end-to-end.

This test does NOT exercise BrainCore (not yet implemented).  It only
validates component wiring and state machine transitions.
"""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# Adapters & repositories
# ---------------------------------------------------------------------------
from src.gateway.adapters.in_memory import InMemoryGatewayAdapter
from src.deepagents.adapters.mock import MockDeepAgentsAdapter
from src.knowledge.adapters.stub import StubKnowledgeAdapter
from src.knowledge.repositories.in_memory import InMemoryKnowledgeRepository
from src.memory.repositories.in_memory import InMemoryMemoryRepository
from src.memory.adapters.port_adapters import MemorySearchAdapter
from src.validation.pipeline import ValidationPipeline
from src.validation.escalation import EscalationTracker
from src.validation.validators.safety import StubSafetyValidator
from src.validation.validators.rule import StubRuleValidator
from src.validation.validators.shadow import StubShadowValidator
from src.validation.validators.consistency import StubConsistencyValidator
from src.validation.repositories import InMemoryValidationResultRepository
from src.brain.repositories.in_memory import InMemoryBrainDecisionRepository
from src.human_collaboration.repositories.in_memory import (
    InMemoryHumanReviewRequestRepository,
)
from src.learning.repositories.in_memory import InMemoryLearningEventRepository

# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
from src.brain.state_machine import BrainStateMachine

# ---------------------------------------------------------------------------
# Enums & types
# ---------------------------------------------------------------------------
from src.shared.enums import (
    BrainStateType,
    BrainTrigger,
    EventType,
    NoveltyLevel,
    ReasoningMode,
    DecisionPointType,
    PersonaType,
    AggregatedValidationResult,
)

from src.shared.types import CaseId, DecisionId, EventId

# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------
from src.shared.dto_context import DomainEvent, ContextSnapshot
from src.shared.dto_decision import BrainDecision
from src.shared.ports.validation import ValidationPipelineInput
from src.shared.ports.deepagents import ReasoningInput
from src.shared.ports.knowledge import RAGQueryInput
from src.shared.dto_knowledge import RAGQuery


# =====================================================================
# Helpers
# =====================================================================


def _make_domain_event() -> DomainEvent:
    """Construct a minimal WORKFLOW_ENTERED DomainEvent."""
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
    """Construct a minimal ContextSnapshot for validation pipeline input."""
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
    """Construct a minimal BrainDecision for validation pipeline input."""
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
        deep_agents = MockDeepAgentsAdapter()
        knowledge_adapter = StubKnowledgeAdapter()
        memory_repo = InMemoryMemoryRepository()
        memory_search = MemorySearchAdapter(memory_repo)
        decision_repo = InMemoryBrainDecisionRepository()
        validation_result_repo = InMemoryValidationResultRepository()
        human_review_repo = InMemoryHumanReviewRequestRepository()
        learning_event_repo = InMemoryLearningEventRepository()
        knowledge_repo = InMemoryKnowledgeRepository()

        # Stub validators
        safety_validator = StubSafetyValidator()
        rule_validator = StubRuleValidator()
        shadow_validator = StubShadowValidator()
        consistency_validator = StubConsistencyValidator()

        # Escalation tracker
        escalation_tracker = EscalationTracker()

        # Validation pipeline — wiring all dependencies
        pipeline = ValidationPipeline(
            safety=safety_validator,
            rule=rule_validator,
            shadow=shadow_validator,
            consistency=consistency_validator,
            escalation=escalation_tracker,
            memory_search=memory_search,
            gateway_read=gateway,
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

        # ---- 5. Call MockDeepAgentsAdapter.reason() and assert valid output ----
        context = _make_context_snapshot()
        reasoning_input = ReasoningInput(
            context=context,
            question="smoke test question",
            knowledge_results=[],
            memory_results=[],
        )
        reasoning_output = await deep_agents.reason(reasoning_input)
        assert reasoning_output.reasoning_trace == "mock_reasoning_trace"
        assert len(reasoning_output.conclusions) == 1
        assert reasoning_output.confidence == 0.8

        # ---- 6. Call StubKnowledgeAdapter.rag_query() and assert it returns [] ----
        rag_input = RAGQueryInput(
            query=RAGQuery(query_text="smoke test query")
        )
        rag_output = await knowledge_adapter.query(rag_input)
        assert rag_output.results == []

        # ---- 7. Assert InMemoryGatewayAdapter has a brain_writes entry
        #       from publish_decision() ----
        decision = _make_brain_decision()
        publish_result = await gateway.publish_decision(decision)
        assert publish_result.success is True
        # The gateway stores the decision at /decisions/{decision_id}
        assert len(gateway._brain_writes) == 1
        expected_path = f"/decisions/{decision.decision_id.value}"
        assert expected_path in gateway._brain_writes

        # ---- 8. Call pipeline.validate() with minimal input
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
