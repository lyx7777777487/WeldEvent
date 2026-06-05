"""Tests for BrainOrchestrator -- full decision pipeline execution.

Covers ROUTINE, ADAPTIVE, and EXPLORATORY paths through the state machine,
validation approval/rejection, publishing, and graceful handling of missing
ports.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.brain.orchestrator import BrainOrchestrator, OrchestrationResult
from src.deepagents.adapters.mock import MockDeepAgentsAdapter
from src.gateway.adapters.in_memory import InMemoryGatewayAdapter
from src.knowledge.adapters.stub import StubKnowledgeAdapter
from src.memory.adapters.port_adapters import MemorySearchAdapter
from src.memory.repositories.in_memory import InMemoryMemoryRepository
from src.shared.dto_context import ContextSnapshot
from src.shared.dto_decision import BrainDecision
from src.shared.dto_validation import ValidationResult
from src.shared.enums import (
    AggregatedValidationResult,
    BrainStateType,
    BrainTrigger,
    EventType,
    NoveltyLevel,
    PersonaType,
    ReasoningMode,
)
from src.shared.types import CaseId
from src.validation.escalation import EscalationTracker
from src.validation.pipeline import ValidationPipeline
from src.validation.validators.consistency import StubConsistencyValidator
from src.validation.validators.rule import StubRuleValidator
from src.validation.validators.safety import StubSafetyValidator
from src.validation.validators.shadow import StubShadowValidator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(
    novelty: NoveltyLevel = NoveltyLevel.KNOWN,
    critical_count: int = 0,
) -> ContextSnapshot:
    return ContextSnapshot(
        case_id=CaseId(value="test-case-001"),
        event_type=EventType.IQA_COMPLETED,
        workflow_state={},
        case_data={"defect": "porosity"},
        measurements=[],
        memory_match_confidence=0.5,
        knowledge_coverage=0.8,
        event_novelty=novelty,
        validation_critical_count=critical_count,
        timestamp=datetime.now(timezone.utc),
    )


def _make_validation_pipeline() -> ValidationPipeline:
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


def _default_ports() -> dict:
    """Ports that satisfy every path of the orchestrator."""
    memory_repo = InMemoryMemoryRepository()
    return {
        "RAGQueryPort": StubKnowledgeAdapter(),
        "MemorySearchPort": MemorySearchAdapter(memory_repo),
        "DeepAgentsReasoningPort": MockDeepAgentsAdapter(),
        "DeepAgentsPlanningPort": MockDeepAgentsAdapter(),
        "ValidationPipelinePort": _make_validation_pipeline(),
        "GatewayWritePort": InMemoryGatewayAdapter(),
    }


# ===================================================================
# ROUTINE path
# ===================================================================


class TestRoutinePath:
    """ROUTINE novelty -> memory-driven path (no knowledge/reasoning)."""

    @pytest.mark.asyncio
    async def test_orchestrator_routine_path(self) -> None:
        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.KNOWN)
        result = await orchestrator.execute_workflow_design(
            objective="Inspect weld joint",
            requirements=["ISO 5817"],
            context=context,
            ports=_default_ports(),
        )

        assert result.success is True
        assert result.decision is not None
        assert result.decision.reasoning_mode == ReasoningMode.ROUTINE
        assert result.decision.persona == PersonaType.COPILOT
        assert result.validation_result is not None
        assert result.published is True

        # Verify ROUTINE state-machine transitions
        state_trigger_pairs = result.state_transitions
        triggers = [t for _, t in state_trigger_pairs]
        assert BrainTrigger.EVENT_DEQUEUED in triggers
        assert BrainTrigger.CONTEXT_LOADED_ROUTINE in triggers
        assert BrainTrigger.MEMORY_RECEIVED_ROUTINE in triggers
        assert BrainTrigger.MATCH_PRODUCED in triggers
        # ROUTINE path should NOT go through UNDERSTANDING / KNOWLEDGE
        assert BrainTrigger.CONTEXT_UNDERSTOOD not in triggers
        assert BrainTrigger.KNOWLEDGE_RECEIVED not in triggers

    @pytest.mark.asyncio
    async def test_routine_path_ends_in_idle(self) -> None:
        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.KNOWN)
        result = await orchestrator.execute_workflow_design(
            objective="Routine check",
            requirements=[],
            context=context,
            ports=_default_ports(),
        )
        # Last transition should end in IDLE (PUBLISHED_ROUTINE_APPROVED -> IDLE)
        last_from, last_trigger = result.state_transitions[-1]
        assert last_trigger == BrainTrigger.PUBLISHED_ROUTINE_APPROVED


# ===================================================================
# ADAPTIVE path
# ===================================================================


class TestAdaptivePath:
    """ADAPTIVE novelty -> knowledge + reasoning path."""

    @pytest.mark.asyncio
    async def test_orchestrator_adaptive_path(self) -> None:
        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.PARTIAL)
        result = await orchestrator.execute_workflow_design(
            objective="Investigate defect pattern",
            requirements=["Check standards"],
            context=context,
            ports=_default_ports(),
        )

        assert result.success is True
        assert result.decision is not None
        assert result.decision.reasoning_mode == ReasoningMode.ADAPTIVE
        assert result.decision.persona == PersonaType.PLANNER

        triggers = [t for _, t in result.state_transitions]
        assert BrainTrigger.CONTEXT_LOADED_ADAPTIVE in triggers
        assert BrainTrigger.CONTEXT_UNDERSTOOD in triggers
        assert BrainTrigger.KNOWLEDGE_RECEIVED in triggers
        assert BrainTrigger.REASONING_COMPLETED in triggers
        assert BrainTrigger.DECISION_GENERATED in triggers

    @pytest.mark.asyncio
    async def test_adaptive_path_ends_in_idle(self) -> None:
        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.PARTIAL)
        result = await orchestrator.execute_workflow_design(
            objective="Adaptive analysis",
            requirements=[],
            context=context,
            ports=_default_ports(),
        )
        last_from, last_trigger = result.state_transitions[-1]
        assert last_trigger == BrainTrigger.PUBLISHED_ADAPTIVE_APPROVED


# ===================================================================
# EXPLORATORY path
# ===================================================================


class TestExploratoryPath:
    """EXPLORATORY novelty -> full deep reasoning path."""

    @pytest.mark.asyncio
    async def test_orchestrator_exploratory_path(self) -> None:
        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.UNKNOWN)
        result = await orchestrator.execute_workflow_design(
            objective="Investigate unknown anomaly",
            requirements=["Safety first"],
            context=context,
            ports=_default_ports(),
        )

        assert result.success is True
        assert result.decision is not None
        assert result.decision.reasoning_mode == ReasoningMode.EXPLORATORY
        assert result.decision.persona == PersonaType.CAA

        triggers = [t for _, t in result.state_transitions]
        assert BrainTrigger.CONTEXT_LOADED_EXPLORATORY in triggers
        assert BrainTrigger.MEMORY_RECEIVED_EXPLORATORY in triggers
        assert BrainTrigger.PUBLISHED_EXPLORATORY in triggers


# ===================================================================
# Validation outcomes
# ===================================================================


class TestValidationRejected:
    """Validation REJECTED -> no publish, state returns to IDLE."""

    @pytest.mark.asyncio
    async def test_orchestrator_validation_rejected(self) -> None:
        """When validation returns REJECTED, decision is not published."""
        from src.shared.dto_validation import (
            RuleValidationResult,
            SafetyValidationResult,
            StageResult,
            ValidationResult,
        )
        from src.shared.enums import RuleStatus, ValidationStageStatus
        from src.shared.ports.validation import (
            ValidationPipelineInput,
            ValidationPipelineOutput,
            ValidationPipelinePort,
        )
        from src.shared.types import ValidationId

        class RejectingValidationPipeline(ValidationPipelinePort):
            async def validate(
                self, input_data: ValidationPipelineInput
            ) -> ValidationPipelineOutput:
                now = datetime.now(timezone.utc)
                val_result = ValidationResult(
                    validation_id=ValidationId(value=uuid4()),
                    decision_id=input_data.decision.decision_id,
                    safety_result=SafetyValidationResult(
                        result="PASS",
                        checked_rules=[],
                        timestamp=now,
                    ),
                    rule_result=RuleValidationResult(
                        result=RuleStatus.REJECT,
                        violated_rules=["rule_1"],
                        timestamp=now,
                    ),
                    aggregated_result=AggregatedValidationResult.REJECTED,
                    stages=[
                        StageResult(
                            stage="safety",
                            status=ValidationStageStatus.COMPLETED,
                            duration_ms=1,
                        ),
                        StageResult(
                            stage="rule",
                            status=ValidationStageStatus.COMPLETED,
                            duration_ms=1,
                        ),
                    ],
                    total_duration_ms=2,
                    timestamp=now,
                )
                return ValidationPipelineOutput(
                    validation_result=val_result,
                    escalation_counter_updated=False,
                )

        ports = _default_ports()
        ports["ValidationPipelinePort"] = RejectingValidationPipeline()

        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.KNOWN)
        result = await orchestrator.execute_workflow_design(
            objective="Test rejection",
            requirements=[],
            context=context,
            ports=ports,
        )

        assert result.success is True
        assert result.validation_result is not None
        assert (
            result.validation_result.aggregated_result
            == AggregatedValidationResult.REJECTED
        )
        assert result.published is False

        triggers = [t for _, t in result.state_transitions]
        assert BrainTrigger.REJECTED in triggers
        assert BrainTrigger.APPROVED not in triggers


# ===================================================================
# Publishing
# ===================================================================


class TestPublishesDecision:
    """APPROVED validation -> publish via gateway."""

    @pytest.mark.asyncio
    async def test_orchestrator_publishes_decision(self) -> None:
        gateway = InMemoryGatewayAdapter()
        ports = _default_ports()
        ports["GatewayWritePort"] = gateway

        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.KNOWN)
        result = await orchestrator.execute_workflow_design(
            objective="Publish test",
            requirements=[],
            context=context,
            ports=ports,
        )

        assert result.success is True
        assert result.published is True
        assert result.decision is not None
        # Verify the gateway actually stored the decision
        decision_path = f"/decisions/{result.decision.decision_id.value}"
        assert decision_path in gateway._brain_writes


# ===================================================================
# Missing ports
# ===================================================================


class TestNoValidationPort:
    """Missing ValidationPipelinePort -> publish directly."""

    @pytest.mark.asyncio
    async def test_orchestrator_no_validation_port(self) -> None:
        gateway = InMemoryGatewayAdapter()
        ports = _default_ports()
        del ports["ValidationPipelinePort"]
        ports["GatewayWritePort"] = gateway

        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.KNOWN)
        result = await orchestrator.execute_workflow_design(
            objective="No validation",
            requirements=[],
            context=context,
            ports=ports,
        )

        assert result.success is True
        assert result.validation_result is None
        assert result.published is True
        assert result.decision is not None

    @pytest.mark.asyncio
    async def test_orchestrator_no_memory_port(self) -> None:
        """Missing MemorySearchPort should not crash."""
        ports = _default_ports()
        del ports["MemorySearchPort"]

        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.PARTIAL)
        result = await orchestrator.execute_workflow_design(
            objective="No memory",
            requirements=[],
            context=context,
            ports=ports,
        )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_orchestrator_no_knowledge_port(self) -> None:
        """Missing RAGQueryPort should not crash ADAPTIVE path."""
        ports = _default_ports()
        del ports["RAGQueryPort"]

        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.PARTIAL)
        result = await orchestrator.execute_workflow_design(
            objective="No knowledge",
            requirements=[],
            context=context,
            ports=ports,
        )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_orchestrator_no_gateway_port(self) -> None:
        """Missing GatewayWritePort -> decision not published but still succeeds."""
        ports = _default_ports()
        del ports["GatewayWritePort"]

        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.KNOWN)
        result = await orchestrator.execute_workflow_design(
            objective="No gateway",
            requirements=[],
            context=context,
            ports=ports,
        )

        assert result.success is True
        assert result.published is False


# ===================================================================
# Persona selection
# ===================================================================


class TestPersonaSelection:
    """Persona selection rules."""

    @pytest.mark.asyncio
    async def test_unknown_novelty_selects_caa(self) -> None:
        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.UNKNOWN)
        result = await orchestrator.execute_workflow_design(
            objective="CAA test",
            requirements=[],
            context=context,
            ports=_default_ports(),
        )
        assert result.decision is not None
        assert result.decision.persona == PersonaType.CAA

    @pytest.mark.asyncio
    async def test_critical_validation_selects_caa(self) -> None:
        orchestrator = BrainOrchestrator()
        context = _make_context(
            novelty=NoveltyLevel.KNOWN, critical_count=2
        )
        result = await orchestrator.execute_workflow_design(
            objective="Critical test",
            requirements=[],
            context=context,
            ports=_default_ports(),
        )
        assert result.decision is not None
        assert result.decision.persona == PersonaType.CAA

    @pytest.mark.asyncio
    async def test_partial_novelty_selects_planner(self) -> None:
        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.PARTIAL)
        result = await orchestrator.execute_workflow_design(
            objective="Planner test",
            requirements=[],
            context=context,
            ports=_default_ports(),
        )
        assert result.decision is not None
        assert result.decision.persona == PersonaType.PLANNER

    @pytest.mark.asyncio
    async def test_known_novelty_selects_copilot(self) -> None:
        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.KNOWN)
        result = await orchestrator.execute_workflow_design(
            objective="Copilot test",
            requirements=[],
            context=context,
            ports=_default_ports(),
        )
        assert result.decision is not None
        assert result.decision.persona == PersonaType.COPILOT


# ===================================================================
# Error handling
# ===================================================================


class TestErrorHandling:
    """Exception in a port -> OrchestrationResult.success == False."""

    @pytest.mark.asyncio
    async def test_orchestrator_handles_port_exception(self) -> None:
        from src.shared.ports.knowledge import RAGQueryInput, RAGQueryPort

        class BrokenRAGPort(RAGQueryPort):
            async def query(self, input_data: RAGQueryInput):
                raise RuntimeError("RAG service down")

        ports = _default_ports()
        ports["RAGQueryPort"] = BrokenRAGPort()

        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.PARTIAL)
        result = await orchestrator.execute_workflow_design(
            objective="Broken RAG",
            requirements=[],
            context=context,
            ports=ports,
        )

        assert result.success is False
        assert result.error is not None
        assert "RAG service down" in result.error
        # Transitions logged up to the point of failure
        assert len(result.state_transitions) > 0
