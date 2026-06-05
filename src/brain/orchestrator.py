"""BrainOrchestrator -- full Brain decision pipeline executor.

Chains: Persona selection -> Reasoning mode -> Knowledge retrieval ->
Memory search -> Reasoning/Planning -> Decision -> Validation -> Publish.

Drives the BrainStateMachine through each state transition and delegates
domain work to injected ports.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from src.brain.state_machine import BrainStateMachine
from src.shared.dto_context import ContextSnapshot
from src.shared.dto_decision import BrainDecision, DecisionOutput
from src.shared.dto_decision.outputs import ParameterRecommendation, ParameterSet
from src.shared.dto_knowledge import RAGQuery
from src.shared.dto_memory import MemorySearchQuery
from src.shared.dto_validation import ValidationResult
from src.shared.enums import (
    AggregatedValidationResult,
    BrainStateType,
    BrainTrigger,
    DecisionPointType,
    EventType,
    NoveltyLevel,
    PersonaType,
    ReasoningMode,
)
from src.shared.types import CaseId, DecisionId
from src.shared.ports.deepagents import (
    DeepAgentsPlanningPort,
    DeepAgentsReasoningPort,
    PlanningInput,
    ReasoningInput,
)
from src.shared.ports.gateway import GatewayWritePort
from src.shared.ports.knowledge import RAGQueryInput, RAGQueryPort
from src.shared.ports.memory import MemorySearchInput, MemorySearchPort
from src.shared.ports.validation import ValidationPipelineInput, ValidationPipelinePort


@dataclass
class OrchestrationResult:
    """Outcome of an orchestrator run."""

    success: bool
    decision: BrainDecision | None
    validation_result: ValidationResult | None
    state_transitions: list[tuple[BrainStateType, BrainTrigger]] = field(
        default_factory=list
    )
    published: bool = False
    error: str | None = None


class BrainOrchestrator:
    """Core execution engine for Mode B (Workflow Design).

    Chains the full Brain decision pipeline and drives the state machine
    through each transition.  Ports are injected via a dict so that tests
    can supply only the adapters they need.
    """

    def __init__(self) -> None:
        self._sm = BrainStateMachine()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute_workflow_design(
        self,
        objective: str,
        requirements: list[str],
        context: ContextSnapshot,
        ports: dict[str, Any],
    ) -> OrchestrationResult:
        """Execute the full decision pipeline for a workflow-design request.

        Parameters
        ----------
        objective:
            The high-level goal (e.g. "Design inspection workflow for case X").
        requirements:
            Constraints or quality requirements.
        context:
            Current case snapshot driving the decision.
        ports:
            Dict of port instances keyed by name.  Recognised keys:
            ``"RAGQueryPort"``, ``"MemorySearchPort"``,
            ``"DeepAgentsReasoningPort"``, ``"DeepAgentsPlanningPort"``,
            ``"ValidationPipelinePort"``, ``"GatewayWritePort"``.
        """
        transitions: list[tuple[BrainStateType, BrainTrigger]] = []

        try:
            # Step 1: Select Persona
            persona = self._select_persona(context)

            # Step 2: Select Reasoning Mode
            reasoning_mode = self._select_reasoning_mode(context)

            # Step 3: IDLE -> OBSERVING
            current_state = self._transition(
                BrainStateType.IDLE, BrainTrigger.EVENT_DEQUEUED, transitions
            )

            # Step 4: OBSERVING -> branch by reasoning mode
            if reasoning_mode == ReasoningMode.ROUTINE:
                current_state = self._transition(
                    current_state,
                    BrainTrigger.CONTEXT_LOADED_ROUTINE,
                    transitions,
                )
            elif reasoning_mode == ReasoningMode.ADAPTIVE:
                current_state = self._transition(
                    current_state,
                    BrainTrigger.CONTEXT_LOADED_ADAPTIVE,
                    transitions,
                )
            else:  # EXPLORATORY
                current_state = self._transition(
                    current_state,
                    BrainTrigger.CONTEXT_LOADED_EXPLORATORY,
                    transitions,
                )

            # Step 5: Knowledge retrieval (ADAPTIVE / EXPLORATORY only)
            knowledge_results: list[Any] = []
            if reasoning_mode != ReasoningMode.ROUTINE:
                current_state = self._transition(
                    current_state, BrainTrigger.CONTEXT_UNDERSTOOD, transitions
                )

                if self._has_port(ports, "RAGQueryPort"):
                    rag_port: RAGQueryPort = ports["RAGQueryPort"]
                    rag_output = await rag_port.query(
                        RAGQueryInput(
                            query=RAGQuery(
                                query_text=objective,
                                max_results=5,
                            )
                        )
                    )
                    knowledge_results = rag_output.results

                current_state = self._transition(
                    current_state, BrainTrigger.KNOWLEDGE_RECEIVED, transitions
                )

            # Step 6: Memory search
            memory_results: list[Any] = []
            if self._has_port(ports, "MemorySearchPort"):
                mem_port: MemorySearchPort = ports["MemorySearchPort"]
                mem_output = await mem_port.search(
                    MemorySearchInput(
                        query=MemorySearchQuery(
                            case_features=context.case_data,
                            feature_vector=[0.0],
                            max_results=10,
                        )
                    )
                )
                memory_results = mem_output.results

            # Step 7: MEMORY_RETRIEVAL -> next state by reasoning mode
            if reasoning_mode == ReasoningMode.ROUTINE:
                current_state = self._transition(
                    current_state,
                    BrainTrigger.MEMORY_RECEIVED_ROUTINE,
                    transitions,
                )
                # MEMORY_MATCHING -> VALIDATION
                current_state = self._transition(
                    current_state, BrainTrigger.MATCH_PRODUCED, transitions
                )
            else:
                current_state = self._transition(
                    current_state,
                    BrainTrigger.MEMORY_RECEIVED_ADAPTIVE
                    if reasoning_mode == ReasoningMode.ADAPTIVE
                    else BrainTrigger.MEMORY_RECEIVED_EXPLORATORY,
                    transitions,
                )

            # Step 8: Execute reasoning or planning (non-ROUTINE)
            reasoning_output = None
            if reasoning_mode != ReasoningMode.ROUTINE:
                if (
                    persona == PersonaType.PLANNER
                    and self._has_port(ports, "DeepAgentsPlanningPort")
                ):
                    plan_port: DeepAgentsPlanningPort = ports[
                        "DeepAgentsPlanningPort"
                    ]
                    reasoning_output = await plan_port.plan(
                        PlanningInput(
                            context=context,
                            objective=objective,
                            constraints=[],
                            available_strategies=[],
                        )
                    )
                elif self._has_port(ports, "DeepAgentsReasoningPort"):
                    reason_port: DeepAgentsReasoningPort = ports[
                        "DeepAgentsReasoningPort"
                    ]
                    reasoning_output = await reason_port.reason(
                        ReasoningInput(
                            context=context,
                            question=objective,
                            knowledge_results=knowledge_results,
                            memory_results=memory_results,
                        )
                    )

                # REASONING -> DECISION_GENERATION -> VALIDATION
                current_state = self._transition(
                    current_state,
                    BrainTrigger.REASONING_COMPLETED,
                    transitions,
                )
                current_state = self._transition(
                    current_state, BrainTrigger.DECISION_GENERATED, transitions
                )

            # Step 9: Generate BrainDecision
            now = datetime.now(timezone.utc)
            decision = BrainDecision(
                decision_id=DecisionId(value=uuid4()),
                case_id=context.case_id,
                trigger_event_type=context.event_type,
                decision_point=DecisionPointType.DP0,
                persona=persona,
                reasoning_mode=reasoning_mode,
                state=current_state,
                outputs=[
                    DecisionOutput(
                        content=ParameterRecommendation(
                            parameters=ParameterSet(
                                parameters={"objective": objective}
                            ),
                            rationale="; ".join(requirements) if requirements else objective,
                            confidence=0.8,
                            constraints_applied=[],
                        ),
                        confidence=0.8,
                    )
                ],
                confidence=0.8,
                created_at=now,
            )

            # Step 10: Validate
            validation_result: ValidationResult | None = None
            if self._has_port(ports, "ValidationPipelinePort"):
                val_port: ValidationPipelinePort = ports["ValidationPipelinePort"]
                val_output = await val_port.validate(
                    ValidationPipelineInput(
                        context=context,
                        decision=decision,
                        reasoning_mode=reasoning_mode,
                    )
                )
                validation_result = val_output.validation_result

            # Step 11: Decide on publication
            published = False
            if validation_result is not None:
                agg = validation_result.aggregated_result
                if agg in (
                    AggregatedValidationResult.APPROVED,
                    AggregatedValidationResult.REQUIRES_REVIEW,
                    AggregatedValidationResult.ESCALATED,
                ):
                    current_state = self._transition(
                        current_state, BrainTrigger.APPROVED, transitions
                    )
                    if self._has_port(ports, "GatewayWritePort"):
                        gw_port: GatewayWritePort = ports["GatewayWritePort"]
                        await gw_port.publish_decision(decision)
                        published = True

                    # Determine publication trigger based on reasoning mode
                    if agg == AggregatedValidationResult.ESCALATED:
                        pub_trigger = BrainTrigger.ESCALATION_PUBLISHED
                    elif reasoning_mode == ReasoningMode.ROUTINE:
                        pub_trigger = BrainTrigger.PUBLISHED_ROUTINE_APPROVED
                    elif reasoning_mode == ReasoningMode.ADAPTIVE:
                        pub_trigger = BrainTrigger.PUBLISHED_ADAPTIVE_APPROVED
                    else:  # EXPLORATORY
                        pub_trigger = BrainTrigger.PUBLISHED_EXPLORATORY

                    current_state = self._transition(
                        current_state, pub_trigger, transitions
                    )
                else:
                    # REJECTED
                    current_state = self._transition(
                        current_state, BrainTrigger.REJECTED, transitions
                    )
            else:
                # No validation port -> publish directly
                current_state = self._transition(
                    current_state, BrainTrigger.APPROVED, transitions
                )
                if self._has_port(ports, "GatewayWritePort"):
                    gw_port2: GatewayWritePort = ports["GatewayWritePort"]
                    await gw_port2.publish_decision(decision)
                    published = True

                if reasoning_mode == ReasoningMode.ROUTINE:
                    pub_trigger = BrainTrigger.PUBLISHED_ROUTINE_APPROVED
                elif reasoning_mode == ReasoningMode.ADAPTIVE:
                    pub_trigger = BrainTrigger.PUBLISHED_ADAPTIVE_APPROVED
                else:
                    pub_trigger = BrainTrigger.PUBLISHED_EXPLORATORY
                current_state = self._transition(
                    current_state, pub_trigger, transitions
                )

            return OrchestrationResult(
                success=True,
                decision=decision,
                validation_result=validation_result,
                state_transitions=transitions,
                published=published,
            )

        except Exception as e:
            return OrchestrationResult(
                success=False,
                decision=None,
                validation_result=None,
                state_transitions=transitions,
                error=str(e),
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _select_persona(context: ContextSnapshot) -> PersonaType:
        """Select persona based on context signals.

        Rules:
        - UNKNOWN novelty or critical validations -> CAA
        - PARTIAL novelty -> PLANNER
        - otherwise -> COPILOT
        """
        if context.event_novelty == NoveltyLevel.UNKNOWN:
            return PersonaType.CAA
        if context.validation_critical_count > 0:
            return PersonaType.CAA
        if context.event_novelty == NoveltyLevel.PARTIAL:
            return PersonaType.PLANNER
        return PersonaType.COPILOT

    @staticmethod
    def _select_reasoning_mode(context: ContextSnapshot) -> ReasoningMode:
        """Select reasoning mode based on novelty level."""
        novelty_map = {
            NoveltyLevel.KNOWN: ReasoningMode.ROUTINE,
            NoveltyLevel.PARTIAL: ReasoningMode.ADAPTIVE,
            NoveltyLevel.UNKNOWN: ReasoningMode.EXPLORATORY,
        }
        return novelty_map.get(context.event_novelty, ReasoningMode.ADAPTIVE)

    def _transition(
        self,
        current: BrainStateType,
        trigger: BrainTrigger,
        log: list[tuple[BrainStateType, BrainTrigger]],
    ) -> BrainStateType:
        """Apply a state-machine transition and log it."""
        next_state = self._sm.transition(current, trigger)
        log.append((current, trigger))
        return next_state

    @staticmethod
    def _has_port(ports: dict[str, Any], name: str) -> bool:
        """Check whether a port is present and not None."""
        return name in ports and ports[name] is not None
