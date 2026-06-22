"""BrainOrchestrator -- full Brain decision pipeline executor.

Chains: Persona selection -> Reasoning mode -> Knowledge retrieval ->
Memory search -> Reasoning/Planning -> Decision -> Validation -> Publish.

Drives the BrainStateMachine through each state transition and delegates
domain work to typed CognitiveDependencies. Per-run EventLog captures
all events.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from cognitiveplane.control.deps import CognitiveDependencies
from cognitiveplane.control.event_log import BrainEventType, EventLog
from cognitiveplane.control.state_machine import BrainStateMachine
from cognitiveplane.shared.dto_context import ContextSnapshot
from cognitiveplane.shared.dto_decision import BrainDecision, DecisionOutput
from cognitiveplane.shared.dto_decision.outputs import ParameterRecommendation, ParameterSet
from cognitiveplane.shared.dto_knowledge import RAGQuery
from cognitiveplane.shared.dto_memory import MemoryContent, MemorySearchQuery
from cognitiveplane.shared.dto_validation import ValidationResult
from cognitiveplane.shared.enums import (
    AggregatedValidationResult,
    BrainStateType,
    BrainTrigger,
    DecisionPointType,
    FallbackMode,
    MemoryType,
    NoveltyLevel,
    PersonaType,
    ReasoningMode,
    SafetyStatus,
    UrgencyLevel,
)
from cognitiveplane.shared.types import DecisionId


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
    awaiting_feedback: bool = False
    error: str | None = None
    event_log: EventLog | None = None


class BrainOrchestrator:
    """Core execution engine for the Brain decision pipeline.

    Chains the full decision pipeline and drives the state machine
    through each transition. Uses typed CognitiveDependencies for
    all dependency access — no dict[str, Any] ports pattern.
    """

    def __init__(self) -> None:
        self._sm = BrainStateMachine()

    async def execute(
        self,
        objective: str,
        requirements: list[str],
        context: ContextSnapshot,
        deps: CognitiveDependencies,
    ) -> OrchestrationResult:
        """Execute the full decision pipeline using typed dependencies."""
        transitions: list[tuple[BrainStateType, BrainTrigger]] = []
        event_log = EventLog(case_id=context.case_id)

        try:
            # Step 1: Select Persona
            persona = self._select_persona(context)

            # Step 2: Select Reasoning Mode
            reasoning_mode = self._select_reasoning_mode(context)

            event_log.emit(BrainEventType.STATE_TRANSITION, "orchestrator", {
                "persona": persona.value, "reasoning_mode": reasoning_mode.value,
            })

            # Step 3: IDLE -> OBSERVING
            current_state = self._transition(
                BrainStateType.IDLE, BrainTrigger.EVENT_DEQUEUED, transitions
            )

            # Step 4: OBSERVING -> branch by reasoning mode
            if reasoning_mode == ReasoningMode.ROUTINE:
                current_state = self._transition(
                    current_state, BrainTrigger.CONTEXT_LOADED_ROUTINE, transitions
                )
            elif reasoning_mode == ReasoningMode.ADAPTIVE:
                current_state = self._transition(
                    current_state, BrainTrigger.CONTEXT_LOADED_ADAPTIVE, transitions
                )
            else:
                current_state = self._transition(
                    current_state, BrainTrigger.CONTEXT_LOADED_EXPLORATORY, transitions
                )

            # Step 5: Knowledge retrieval (ADAPTIVE / EXPLORATORY only)
            knowledge_results: list = []
            if reasoning_mode != ReasoningMode.ROUTINE:
                current_state = self._transition(
                    current_state, BrainTrigger.CONTEXT_UNDERSTOOD, transitions
                )

                if deps.knowledge.rag_query is not None:
                    from cognitiveplane.shared.ports.knowledge import RAGQueryInput
                    rag_output = await deps.knowledge.rag_query.query(
                        RAGQueryInput(query=RAGQuery(
                            query_text=objective, max_results=5,
                        ))
                    )
                    knowledge_results = rag_output.results

                event_log.emit(BrainEventType.TOOL_CALL, "knowledge", {
                    "tool": "RAGQuery", "count": len(knowledge_results),
                })
                current_state = self._transition(
                    current_state, BrainTrigger.KNOWLEDGE_RECEIVED, transitions
                )

            # Step 6: Memory search
            memory_results: list = []
            if deps.memory.search is not None:
                from cognitiveplane.shared.ports.memory import MemorySearchInput
                mem_output = await deps.memory.search.search(
                    MemorySearchInput(query=MemorySearchQuery(
                        case_features=context.case_data,
                        feature_vector=[0.0],
                        max_results=10,
                    ))
                )
                memory_results = mem_output.results

            event_log.emit(BrainEventType.TOOL_CALL, "memory", {
                "tool": "MemorySearch", "count": len(memory_results),
            })

            # Step 7: MEMORY_RETRIEVAL -> next state by reasoning mode
            if reasoning_mode == ReasoningMode.ROUTINE:
                current_state = self._transition(
                    current_state, BrainTrigger.MEMORY_RECEIVED_ROUTINE, transitions
                )
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
                if persona == PersonaType.PLANNER:
                    from cognitiveplane.control.planner import Planner, PlannerInput
                    planner = Planner()
                    planner_out = planner.plan(PlannerInput(
                        context=context,
                        objective=objective,
                        constraints=[],
                        available_strategies=[],
                        knowledge_results=knowledge_results,
                        memory_results=memory_results,
                    ))
                    reasoning_output = planner_out.plan
                else:
                    # Use LLM for reasoning if available
                    llm = deps.capability.llm_provider
                    if llm is not None:
                        from cognitiveplane.capability.provider import LLMRequest
                        knowledge_summary = "\n".join(
                            str(k)[:300] for k in knowledge_results[:3]
                        )
                        memory_summary = "\n".join(
                            str(m)[:300] for m in memory_results[:3]
                        )
                        reasoning_prompt = (
                            f"你是焊接质检系统的推理引擎。请根据以下信息进行分析：\n\n"
                            f"目标：{objective}\n\n"
                            f"知识库结果：\n{knowledge_summary or '无'}\n\n"
                            f"历史记忆：\n{memory_summary or '无'}\n\n"
                            f"请给出：1) 分析结论 2) 置信度(0-1) 3) 推理过程简述"
                        )
                        try:
                            llm_response = await llm.complete(LLMRequest(
                                messages=[{"role": "user", "content": reasoning_prompt}],
                                caller="orchestrator_reasoning",
                            ))
                            reasoning_output = type("ReasoningResult", (), {
                                "confidence": 0.75,
                                "reasoning_trace": llm_response.content[:500],
                                "conclusions": [type("Conclusion", (), {
                                    "statement": llm_response.content[:200],
                                    "confidence": 0.75,
                                    "supporting_evidence": [],
                                })()],
                            })()
                        except Exception as e:
                            reasoning_output = type("ReasoningResult", (), {
                                "confidence": 0.5,
                                "reasoning_trace": f"LLM推理失败: {e}",
                                "conclusions": [],
                            })()
                    else:
                        # Fallback: no LLM available
                        reasoning_output = type("ReasoningResult", (), {
                            "confidence": 0.5,
                            "reasoning_trace": f"Analyzed objective (no LLM): {objective}",
                            "conclusions": [],
                        })()

                current_state = self._transition(
                    current_state, BrainTrigger.REASONING_COMPLETED, transitions
                )
                current_state = self._transition(
                    current_state, BrainTrigger.DECISION_GENERATED, transitions
                )

            # Step 9: Generate BrainDecision
            now = datetime.now(timezone.utc)
            reasoning_confidence = (
                float(getattr(reasoning_output, "confidence", 0.0))
                if reasoning_output is not None
                else 0.0
            )
            if reasoning_confidence <= 0.0:
                reasoning_confidence = {
                    ReasoningMode.ROUTINE: 0.9,
                    ReasoningMode.ADAPTIVE: 0.75,
                    ReasoningMode.EXPLORATORY: 0.6,
                }.get(reasoning_mode, 0.75)
            reasoning_confidence = max(0.0, min(1.0, reasoning_confidence))

            rationale_parts: list[str] = []
            if requirements:
                rationale_parts.append("; ".join(requirements))
            else:
                rationale_parts.append(objective)

            extra_params: dict[str, str] = {"objective": objective}
            if reasoning_output is not None:
                trace = getattr(reasoning_output, "reasoning_trace", None)
                if trace:
                    rationale_parts.append(f"推理: {trace[:500]}")
                plan_obj = getattr(reasoning_output, "plan", None)
                if plan_obj is not None:
                    steps = getattr(plan_obj, "steps", None)
                    if steps:
                        for i, step in enumerate(steps[:5], 1):
                            extra_params[f"step_{i}"] = step
                    outcome = getattr(plan_obj, "expected_outcome", None)
                    if outcome:
                        extra_params["expected_outcome"] = outcome
                conclusions = getattr(reasoning_output, "conclusions", None)
                if conclusions:
                    for i, c in enumerate(conclusions[:3], 1):
                        stmt = getattr(c, "statement", str(c))
                        extra_params[f"conclusion_{i}"] = stmt
            elif memory_results:
                for i, m in enumerate(memory_results[:5], 1):
                    content = getattr(m, "content", None)
                    if content:
                        text = str(content)[:200]
                        extra_params[f"memory_match_{i}"] = text
                        if i == 1:
                            rationale_parts.append(f"历史匹配: {text}")
                match_conf = sum(
                    float(getattr(m, "confidence", 0.0)) for m in memory_results[:3]
                ) / min(len(memory_results), 3) if memory_results else 0.0
                if match_conf > reasoning_confidence:
                    reasoning_confidence = match_conf

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
                            parameters=ParameterSet(parameters=extra_params),
                            rationale=" | ".join(rationale_parts),
                            confidence=reasoning_confidence,
                            constraints_applied=[],
                        ),
                        confidence=reasoning_confidence,
                    )
                ],
                confidence=reasoning_confidence,
                created_at=now,
            )

            # Step 10: Validate
            validation_result: ValidationResult | None = None
            if deps.governance.validation is not None:
                from cognitiveplane.shared.ports.validation import ValidationPipelineInput
                val_output = await deps.governance.validation.validate(
                    ValidationPipelineInput(
                        context=context,
                        decision=decision,
                        reasoning_mode=reasoning_mode,
                    )
                )
                validation_result = val_output.validation_result

            if validation_result:
                event_log.emit(BrainEventType.VALIDATION, "governance", {
                    "result": validation_result.aggregated_result.value,
                })

            # Step 11: Decide on publication
            published = False
            if validation_result is not None:
                agg = validation_result.aggregated_result

                if agg == AggregatedValidationResult.APPROVED:
                    current_state = self._transition(
                        current_state, BrainTrigger.APPROVED, transitions
                    )
                    published = await self._publish_decision(decision, deps, transitions)
                    pub_trigger = self._publication_trigger(reasoning_mode)
                    current_state = self._transition(
                        current_state, pub_trigger, transitions
                    )

                elif agg == AggregatedValidationResult.REQUIRES_REVIEW:
                    current_state = self._transition(
                        current_state, BrainTrigger.REQUIRES_REVIEW, transitions
                    )
                    published = await self._publish_decision(decision, deps, transitions)
                    current_state = self._transition(
                        current_state, BrainTrigger.PUBLISHED_REQUIRES_REVIEW, transitions
                    )

                elif agg == AggregatedValidationResult.ESCALATED:
                    current_state = self._transition(
                        current_state, BrainTrigger.ESCALATED, transitions
                    )
                    await self._publish_escalation(decision, validation_result, deps)
                    published = True
                    current_state = self._transition(
                        current_state, BrainTrigger.ESCALATION_PUBLISHED, transitions
                    )

                else:  # REJECTED
                    current_state = self._transition(
                        current_state, BrainTrigger.REJECTED, transitions
                    )
            else:
                current_state = self._transition(
                    current_state, BrainTrigger.APPROVED, transitions
                )
                published = await self._publish_decision(decision, deps, transitions)
                pub_trigger = self._publication_trigger(reasoning_mode)
                current_state = self._transition(
                    current_state, pub_trigger, transitions
                )

            # Persist decision
            if deps.control.decision_repo is not None:
                await deps.control.decision_repo.save(decision)

            # Emit decision event
            event_log.emit(BrainEventType.DECISION, "orchestrator", {
                "decision_id": str(decision.decision_id.value),
                "published": published,
                "persona": persona.value,
                "reasoning_mode": reasoning_mode.value,
            })

            # Write to memory when decision is published
            if published and deps.memory.write is not None:
                from cognitiveplane.shared.ports.memory import MemoryWriteInput
                await deps.memory.write.write(MemoryWriteInput(
                    memory_type=MemoryType.APPROVED_DECISION,
                    content=MemoryContent(
                        summary=str(decision.outputs[0].content)[:200] if decision.outputs else str(decision.decision_id),
                        details={"decision_id": str(decision.decision_id.value)},
                        feature_vector=[0.0],
                    ),
                    source_decision_id=decision.decision_id,
                ))

            # Write LearningEvent after PUBLISHED
            if published and deps.governance.learning_repo is not None:
                from cognitiveplane.shared.dto_learning import LearningContent, LearningEvent
                from cognitiveplane.shared.enums import LearningEventType, ProcessingStatus
                from cognitiveplane.shared.types import LearningEventId
                await deps.governance.learning_repo.save(LearningEvent(
                    learning_event_id=LearningEventId(value=uuid4()),
                    event_type=LearningEventType.EXPERIENCE,
                    source_decision_id=decision.decision_id,
                    content=LearningContent(
                        event_type=LearningEventType.EXPERIENCE,
                        source_decision_id=decision.decision_id,
                        data={"persona": persona.value, "reasoning_mode": reasoning_mode.value},
                    ),
                    processing_status=ProcessingStatus.PENDING,
                    created_at=datetime.now(timezone.utc),
                ))

            awaiting_feedback = current_state == BrainStateType.WAITING_FEEDBACK

            return OrchestrationResult(
                success=True,
                decision=decision,
                validation_result=validation_result,
                state_transitions=transitions,
                published=published,
                awaiting_feedback=awaiting_feedback,
                event_log=event_log,
            )

        except Exception as e:
            event_log.emit(BrainEventType.STATE_TRANSITION, "orchestrator", {
                "error": str(e),
            })
            return OrchestrationResult(
                success=False,
                decision=None,
                validation_result=None,
                state_transitions=transitions,
                error=str(e),
                event_log=event_log,
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _publish_decision(
        decision: BrainDecision,
        deps: CognitiveDependencies,
        transitions: list[tuple[BrainStateType, BrainTrigger]],
    ) -> bool:
        """Publish decision via GatewayWritePort, return success."""
        if deps.gateway.write is not None:
            await deps.gateway.write.publish_decision(decision)
            return True
        return False

    @staticmethod
    async def _publish_escalation(
        decision: BrainDecision,
        validation_result: ValidationResult,
        deps: CognitiveDependencies,
    ) -> bool:
        """Publish escalation via GatewayWritePort."""
        if deps.gateway.write is not None:
            from cognitiveplane.shared.dto_gateway import Escalation
            from cognitiveplane.shared.dto_decision.outputs import EvidenceReference

            escalation = Escalation(
                escalation_id=uuid4(),
                decision_id=decision.decision_id,
                case_id=decision.case_id,
                reason=f"Escalated by validation: {validation_result.aggregated_result.value}",
                urgency=UrgencyLevel.CRITICAL
                if validation_result.safety_result.result == SafetyStatus.BLOCK
                else UrgencyLevel.URGENT,
                fallback_mode=FallbackMode.HUMAN_INTERVENTION
                if validation_result.safety_result.result == SafetyStatus.BLOCK
                else FallbackMode.COGNITIVE_FALLBACK,
                supporting_evidence=[],
                created_at=datetime.now(timezone.utc),
            )
            await deps.gateway.write.publish_escalation(escalation)
            return True
        return False

    @staticmethod
    def _publication_trigger(reasoning_mode: ReasoningMode) -> BrainTrigger:
        if reasoning_mode == ReasoningMode.ROUTINE:
            return BrainTrigger.PUBLISHED_ROUTINE_APPROVED
        if reasoning_mode == ReasoningMode.ADAPTIVE:
            return BrainTrigger.PUBLISHED_ADAPTIVE_APPROVED
        return BrainTrigger.PUBLISHED_EXPLORATORY

    @staticmethod
    def _select_persona(context: ContextSnapshot) -> PersonaType:
        if context.event_novelty == NoveltyLevel.UNKNOWN:
            return PersonaType.CAA
        if context.validation_critical_count > 0:
            return PersonaType.CAA
        if context.event_novelty == NoveltyLevel.PARTIAL:
            return PersonaType.PLANNER
        return PersonaType.COPILOT

    @staticmethod
    def _select_reasoning_mode(context: ContextSnapshot) -> ReasoningMode:
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
