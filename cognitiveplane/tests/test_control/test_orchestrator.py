"""Tests for BrainOrchestrator -- full decision pipeline execution.

Covers ROUTINE, ADAPTIVE, and EXPLORATORY paths through the state machine,
validation approval/rejection, publishing, and graceful handling of missing
deps.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from cognitiveplane.control.deps import (
    CognitiveDependencies,
    ControlDeps,
    GatewayDeps,
    GovernanceDeps,
    KnowledgeDeps,
    MemoryDeps,
)
from cognitiveplane.control.orchestrator import BrainOrchestrator, OrchestrationResult
from cognitiveplane.control.repositories.in_memory import InMemoryBrainDecisionRepository
from cognitiveplane.gateway.adapters.in_memory import InMemoryGatewayAdapter
from cognitiveplane.knowledge.adapters.stub import StubKnowledgeAdapter
from cognitiveplane.memory.adapters.port_adapters import MemorySearchAdapter
from cognitiveplane.memory.repositories.in_memory import InMemoryMemoryRepository
from cognitiveplane.shared.dto_context import ContextSnapshot
from cognitiveplane.shared.enums import (
    AggregatedValidationResult,
    BrainStateType,
    BrainTrigger,
    EventType,
    NoveltyLevel,
    PersonaType,
    ReasoningMode,
    RuleStatus,
    ValidationStageStatus,
)
from cognitiveplane.shared.types import CaseId, ValidationId
from cognitiveplane.governance.escalation import EscalationTracker
from cognitiveplane.governance.pipeline import ValidationPipeline
from cognitiveplane.governance.validators.consistency import StubConsistencyValidator
from cognitiveplane.governance.validators.rule import StubRuleValidator
from cognitiveplane.governance.validators.safety import StubSafetyValidator
from cognitiveplane.governance.validators.shadow import StubShadowValidator


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
    return ValidationPipeline(
        safety=StubSafetyValidator(),
        rule=StubRuleValidator(),
        shadow=StubShadowValidator(),
        consistency=StubConsistencyValidator(),
        escalation=EscalationTracker(),
    )


def _default_deps() -> CognitiveDependencies:
    """Deps that satisfy every path of the orchestrator."""
    memory_repo = InMemoryMemoryRepository()
    gateway = InMemoryGatewayAdapter()
    knowledge = StubKnowledgeAdapter()
    pipeline = _make_validation_pipeline()

    return CognitiveDependencies(
        knowledge=KnowledgeDeps(
            rag_query=knowledge,
            standards_query=knowledge,
            case_library=knowledge,
            process_knowledge=knowledge,
        ),
        memory=MemoryDeps(
            search=MemorySearchAdapter(memory_repo),
        ),
        gateway=GatewayDeps(read=gateway, write=gateway),
        governance=GovernanceDeps(
            validation=pipeline,
            escalation=EscalationTracker(),
        ),
        control=ControlDeps(
            orchestrator=BrainOrchestrator(),
            decision_repo=InMemoryBrainDecisionRepository(),
        ),
    )


# ===================================================================
# ROUTINE path
# ===================================================================


class TestRoutinePath:
    """ROUTINE novelty -> memory-driven path (no knowledge/reasoning)."""

    @pytest.mark.asyncio
    async def test_orchestrator_routine_path(self) -> None:
        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.KNOWN)
        result = await orchestrator.execute(
            objective="Inspect weld joint",
            requirements=["ISO 5817"],
            context=context,
            deps=_default_deps(),
        )

        assert result.success is True
        assert result.decision is not None
        assert result.decision.reasoning_mode == ReasoningMode.ROUTINE
        assert result.decision.persona == PersonaType.COPILOT
        assert result.validation_result is not None
        assert result.published is True

        triggers = [t for _, t in result.state_transitions]
        assert BrainTrigger.EVENT_DEQUEUED in triggers
        assert BrainTrigger.CONTEXT_LOADED_ROUTINE in triggers
        assert BrainTrigger.MEMORY_RECEIVED_ROUTINE in triggers
        assert BrainTrigger.MATCH_PRODUCED in triggers
        assert BrainTrigger.CONTEXT_UNDERSTOOD not in triggers
        assert BrainTrigger.KNOWLEDGE_RECEIVED not in triggers

    @pytest.mark.asyncio
    async def test_routine_path_ends_in_idle(self) -> None:
        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.KNOWN)
        result = await orchestrator.execute(
            objective="Routine check",
            requirements=[],
            context=context,
            deps=_default_deps(),
        )
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
        result = await orchestrator.execute(
            objective="Investigate defect pattern",
            requirements=["Check standards"],
            context=context,
            deps=_default_deps(),
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
        result = await orchestrator.execute(
            objective="Adaptive analysis",
            requirements=[],
            context=context,
            deps=_default_deps(),
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
        result = await orchestrator.execute(
            objective="Investigate unknown anomaly",
            requirements=["Safety first"],
            context=context,
            deps=_default_deps(),
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
        from cognitiveplane.shared.dto_validation import (
            RuleValidationResult,
            SafetyValidationResult,
            StageResult,
            ValidationResult,
        )
        from cognitiveplane.shared.ports.validation import (
            ValidationPipelineInput,
            ValidationPipelineOutput,
            ValidationPipelinePort,
        )

        class RejectingValidationPipeline(ValidationPipelinePort):
            async def validate(
                self, input_data: ValidationPipelineInput
            ) -> ValidationPipelineOutput:
                now = datetime.now(timezone.utc)
                val_result = ValidationResult(
                    validation_id=ValidationId(value=uuid4()),
                    decision_id=input_data.decision.decision_id,
                    safety_result=SafetyValidationResult(
                        result="PASS", checked_rules=[], timestamp=now,
                    ),
                    rule_result=RuleValidationResult(
                        result=RuleStatus.REJECT, violated_rules=["rule_1"], timestamp=now,
                    ),
                    aggregated_result=AggregatedValidationResult.REJECTED,
                    stages=[
                        StageResult(stage="safety", status=ValidationStageStatus.COMPLETED, duration_ms=1),
                        StageResult(stage="rule", status=ValidationStageStatus.COMPLETED, duration_ms=1),
                    ],
                    total_duration_ms=2,
                    timestamp=now,
                )
                return ValidationPipelineOutput(validation_result=val_result, escalation_counter_updated=False)

        deps = _default_deps()
        # Replace validation with rejecting pipeline
        deps = CognitiveDependencies(
            knowledge=deps.knowledge,
            memory=deps.memory,
            gateway=deps.gateway,
            governance=GovernanceDeps(
                validation=RejectingValidationPipeline(),
                escalation=EscalationTracker(),
            ),
            control=deps.control,
        )

        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.KNOWN)
        result = await orchestrator.execute(
            objective="Test rejection",
            requirements=[],
            context=context,
            deps=deps,
        )

        assert result.success is True
        assert result.validation_result is not None
        assert result.validation_result.aggregated_result == AggregatedValidationResult.REJECTED
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
        deps = CognitiveDependencies(
            knowledge=KnowledgeDeps(
                rag_query=StubKnowledgeAdapter(),
                standards_query=StubKnowledgeAdapter(),
                case_library=StubKnowledgeAdapter(),
                process_knowledge=StubKnowledgeAdapter(),
            ),
            memory=MemoryDeps(
                search=MemorySearchAdapter(InMemoryMemoryRepository()),
            ),
            gateway=GatewayDeps(read=gateway, write=gateway),
            governance=GovernanceDeps(
                validation=_make_validation_pipeline(),
                escalation=EscalationTracker(),
            ),
            control=ControlDeps(
                orchestrator=BrainOrchestrator(),
                decision_repo=InMemoryBrainDecisionRepository(),
            ),
        )

        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.KNOWN)
        result = await orchestrator.execute(
            objective="Publish test",
            requirements=[],
            context=context,
            deps=deps,
        )

        assert result.success is True
        assert result.published is True
        assert result.decision is not None
        decision_path = f"/decisions/{result.decision.decision_id.value}"
        assert decision_path in gateway._brain_writes


# ===================================================================
# Missing deps
# ===================================================================


class TestMissingDeps:
    """None deps should be handled gracefully."""

    @pytest.mark.asyncio
    async def test_orchestrator_no_validation(self) -> None:
        """Missing validation -> publish directly."""
        gateway = InMemoryGatewayAdapter()
        deps = CognitiveDependencies(
            gateway=GatewayDeps(read=gateway, write=gateway),
            governance=GovernanceDeps(escalation=EscalationTracker()),
            control=ControlDeps(
                orchestrator=BrainOrchestrator(),
                decision_repo=InMemoryBrainDecisionRepository(),
            ),
        )

        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.KNOWN)
        result = await orchestrator.execute(
            objective="No validation",
            requirements=[],
            context=context,
            deps=deps,
        )

        assert result.success is True
        assert result.validation_result is None
        assert result.published is True

    @pytest.mark.asyncio
    async def test_orchestrator_no_memory(self) -> None:
        """Missing memory search should not crash."""
        deps = CognitiveDependencies(
            knowledge=KnowledgeDeps(
                rag_query=StubKnowledgeAdapter(),
                standards_query=StubKnowledgeAdapter(),
                case_library=StubKnowledgeAdapter(),
                process_knowledge=StubKnowledgeAdapter(),
            ),
            governance=GovernanceDeps(
                validation=_make_validation_pipeline(),
                escalation=EscalationTracker(),
            ),
            control=ControlDeps(
                orchestrator=BrainOrchestrator(),
                decision_repo=InMemoryBrainDecisionRepository(),
            ),
        )

        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.PARTIAL)
        result = await orchestrator.execute(
            objective="No memory",
            requirements=[],
            context=context,
            deps=deps,
        )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_orchestrator_no_knowledge(self) -> None:
        """Missing knowledge should not crash ADAPTIVE path."""
        deps = CognitiveDependencies(
            memory=MemoryDeps(
                search=MemorySearchAdapter(InMemoryMemoryRepository()),
            ),
            governance=GovernanceDeps(
                validation=_make_validation_pipeline(),
                escalation=EscalationTracker(),
            ),
            control=ControlDeps(
                orchestrator=BrainOrchestrator(),
                decision_repo=InMemoryBrainDecisionRepository(),
            ),
        )

        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.PARTIAL)
        result = await orchestrator.execute(
            objective="No knowledge",
            requirements=[],
            context=context,
            deps=deps,
        )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_orchestrator_no_gateway(self) -> None:
        """Missing gateway -> decision not published but still succeeds."""
        deps = CognitiveDependencies(
            memory=MemoryDeps(
                search=MemorySearchAdapter(InMemoryMemoryRepository()),
            ),
            governance=GovernanceDeps(
                validation=_make_validation_pipeline(),
                escalation=EscalationTracker(),
            ),
            control=ControlDeps(
                orchestrator=BrainOrchestrator(),
                decision_repo=InMemoryBrainDecisionRepository(),
            ),
        )

        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.KNOWN)
        result = await orchestrator.execute(
            objective="No gateway",
            requirements=[],
            context=context,
            deps=deps,
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
        result = await orchestrator.execute(
            objective="CAA test", requirements=[], context=context, deps=_default_deps(),
        )
        assert result.decision is not None
        assert result.decision.persona == PersonaType.CAA

    @pytest.mark.asyncio
    async def test_critical_validation_selects_caa(self) -> None:
        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.KNOWN, critical_count=2)
        result = await orchestrator.execute(
            objective="Critical test", requirements=[], context=context, deps=_default_deps(),
        )
        assert result.decision is not None
        assert result.decision.persona == PersonaType.CAA

    @pytest.mark.asyncio
    async def test_partial_novelty_selects_planner(self) -> None:
        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.PARTIAL)
        result = await orchestrator.execute(
            objective="Planner test", requirements=[], context=context, deps=_default_deps(),
        )
        assert result.decision is not None
        assert result.decision.persona == PersonaType.PLANNER

    @pytest.mark.asyncio
    async def test_known_novelty_selects_copilot(self) -> None:
        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.KNOWN)
        result = await orchestrator.execute(
            objective="Copilot test", requirements=[], context=context, deps=_default_deps(),
        )
        assert result.decision is not None
        assert result.decision.persona == PersonaType.COPILOT


# ===================================================================
# Error handling
# ===================================================================


class TestErrorHandling:
    """Exception in a dep -> OrchestrationResult.success == False."""

    @pytest.mark.asyncio
    async def test_orchestrator_handles_port_exception(self) -> None:
        from cognitiveplane.shared.ports.knowledge import RAGQueryInput, RAGQueryPort

        class BrokenRAGPort(RAGQueryPort):
            async def query(self, input_data: RAGQueryInput):
                raise RuntimeError("RAG service down")

        knowledge = StubKnowledgeAdapter()
        deps = CognitiveDependencies(
            knowledge=KnowledgeDeps(
                rag_query=BrokenRAGPort(),
                standards_query=knowledge,
                case_library=knowledge,
                process_knowledge=knowledge,
            ),
            memory=MemoryDeps(
                search=MemorySearchAdapter(InMemoryMemoryRepository()),
            ),
            governance=GovernanceDeps(
                validation=_make_validation_pipeline(),
                escalation=EscalationTracker(),
            ),
            control=ControlDeps(
                orchestrator=BrainOrchestrator(),
                decision_repo=InMemoryBrainDecisionRepository(),
            ),
        )

        orchestrator = BrainOrchestrator()
        context = _make_context(novelty=NoveltyLevel.PARTIAL)
        result = await orchestrator.execute(
            objective="Broken RAG",
            requirements=[],
            context=context,
            deps=deps,
        )

        assert result.success is False
        assert result.error is not None
        assert "RAG service down" in result.error
        assert len(result.state_transitions) > 0
