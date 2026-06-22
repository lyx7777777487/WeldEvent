"""ValidationPipeline -- orchestrates the four-stage validation sequence.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 9.advice.md).
P2-12 fix: Pipeline is now pure computation. Data (memory, gateway) is
pre-fetched by the Orchestrator and passed as parameters to validate().

Flow:
  advice.md. SafetyValidator  -> if BLOCK, short-circuit to ESCALATED
  2. RuleValidator    -> if REJECT, short-circuit to REJECTED
  3. ShadowValidator  -> record result
  4. ConsistencyValidator (with prior_memories + weldmap_state) -> record result
  5. Aggregate per Phase 4 Section 15 rules
  6. Update escalation tracker
  7. Return ValidationPipelineOutput
"""

from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4

from cognitiveplane.shared.dto_memory import MemorySearchResult
from cognitiveplane.shared.dto_validation import (
    ConsistencyValidationResult,
    RuleValidationResult,
    SafetyValidationResult,
    ShadowValidationResult,
    StageResult,
    ValidationResult,
)
from cognitiveplane.shared.enums import (
    AggregatedValidationResult,
    ConsistencyStatus,
    FallbackMode,
    RuleStatus,
    SafetyStatus,
    ShadowStatus,
    ValidationStageStatus,
)
from cognitiveplane.shared.ports.validation import (
    ConsistencyValidatorInput,
    ConsistencyValidatorPort,
    EscalationTrackerInput,
    EscalationTrackerPort,
    RuleValidatorInput,
    RuleValidatorPort,
    SafetyValidatorInput,
    SafetyValidatorPort,
    ShadowValidatorInput,
    ShadowValidatorPort,
    ValidationPipelineInput,
    ValidationPipelineOutput,
    ValidationPipelinePort,
)
from cognitiveplane.shared.types import ValidationId
from cognitiveplane.shared.dto_context import WeldMapSnapshot


class ValidationPipeline(ValidationPipelinePort):
    """Orchestrates the four-stage validation sequence.

    P2-12 fix: Pipeline is pure computation. No memory_search or gateway_read
    in constructor. Data is pre-fetched by the Orchestrator and passed as
    optional parameters to validate().
    """

    def __init__(
        self,
        safety: SafetyValidatorPort,
        rule: RuleValidatorPort,
        shadow: ShadowValidatorPort,
        consistency: ConsistencyValidatorPort,
        escalation: EscalationTrackerPort,
    ) -> None:
        self._safety = safety
        self._rule = rule
        self._shadow = shadow
        self._consistency = consistency
        self._escalation = escalation

    async def validate(
        self,
        input_data: ValidationPipelineInput,
        memory_context: list[MemorySearchResult] | None = None,
        gateway_context: WeldMapSnapshot | None = None,
    ) -> ValidationPipelineOutput:
        pipeline_start = monotonic()
        now = datetime.now(timezone.utc)
        stages: list[StageResult] = []

        context = input_data.context
        decision = input_data.decision

        # ---- Stage advice.md: Safety ----
        t0 = monotonic()
        safety_out = await self._safety.check(
            SafetyValidatorInput(context=context, decision=decision)
        )
        safety_ms = int((monotonic() - t0) * 1000)
        safety_result = SafetyValidationResult(
            result=safety_out.result,
            checked_rules=safety_out.checked_rules,
            timestamp=safety_out.timestamp,
        )
        stages.append(
            StageResult(
                stage="safety",
                status=ValidationStageStatus.COMPLETED,
                result=safety_result,
                duration_ms=safety_ms,
            )
        )

        # Short-circuit: BLOCK -> ESCALATED
        if safety_out.result == SafetyStatus.BLOCK:
            total_ms = int((monotonic() - pipeline_start) * 1000)
            stages.append(
                StageResult(
                    stage="rule",
                    status=ValidationStageStatus.TERMINATED,
                    duration_ms=0,
                )
            )
            stages.append(
                StageResult(
                    stage="shadow",
                    status=ValidationStageStatus.TERMINATED,
                    duration_ms=0,
                )
            )
            stages.append(
                StageResult(
                    stage="consistency",
                    status=ValidationStageStatus.TERMINATED,
                    duration_ms=0,
                )
            )
            validation_result = ValidationResult(
                validation_id=ValidationId(value=uuid4()),
                decision_id=decision.decision_id,
                safety_result=safety_result,
                aggregated_result=AggregatedValidationResult.ESCALATED,
                stages=stages,
                total_duration_ms=total_ms,
                timestamp=now,
            )
            return ValidationPipelineOutput(
                validation_result=validation_result,
                escalation_counter_updated=False,
                new_fallback_mode=None,
            )

        # ---- Stage 2: Rule ----
        t0 = monotonic()
        rule_out = await self._rule.check(
            RuleValidatorInput(context=context, decision=decision)
        )
        rule_ms = int((monotonic() - t0) * 1000)
        rule_result = RuleValidationResult(
            result=rule_out.result,
            violated_rules=rule_out.violated_rules,
            timestamp=rule_out.timestamp,
        )
        stages.append(
            StageResult(
                stage="rule",
                status=ValidationStageStatus.COMPLETED,
                result=rule_result,
                duration_ms=rule_ms,
            )
        )

        # Short-circuit: REJECT -> REJECTED
        if rule_out.result == RuleStatus.REJECT:
            total_ms = int((monotonic() - pipeline_start) * 1000)
            stages.append(
                StageResult(
                    stage="shadow",
                    status=ValidationStageStatus.TERMINATED,
                    duration_ms=0,
                )
            )
            stages.append(
                StageResult(
                    stage="consistency",
                    status=ValidationStageStatus.TERMINATED,
                    duration_ms=0,
                )
            )
            validation_result = ValidationResult(
                validation_id=ValidationId(value=uuid4()),
                decision_id=decision.decision_id,
                safety_result=safety_result,
                rule_result=rule_result,
                aggregated_result=AggregatedValidationResult.REJECTED,
                stages=stages,
                total_duration_ms=total_ms,
                timestamp=now,
            )
            return ValidationPipelineOutput(
                validation_result=validation_result,
                escalation_counter_updated=False,
                new_fallback_mode=None,
            )

        # ---- Stage 3: Shadow ----
        t0 = monotonic()
        shadow_out = await self._shadow.check(
            ShadowValidatorInput(context=context, decision=decision)
        )
        shadow_ms = int((monotonic() - t0) * 1000)
        shadow_result = ShadowValidationResult(
            result=shadow_out.result,
            shadow_decision=shadow_out.shadow_decision,
            alignment_score=shadow_out.alignment_score,
            divergence_points=shadow_out.divergence_points,
            timestamp=shadow_out.timestamp,
        )
        stages.append(
            StageResult(
                stage="shadow",
                status=ValidationStageStatus.COMPLETED,
                result=shadow_result,
                duration_ms=shadow_ms,
            )
        )

        # ---- Stage 4: Consistency ----
        # P2-12: use pre-fetched data passed as parameters
        prior_memories = memory_context or []
        weldmap_state = gateway_context or WeldMapSnapshot(
            case_id=context.case_id,
            workflow_state={},
            measurements=[],
            decisions=[],
            events=[],
            snapshot_at=now,
        )

        t0 = monotonic()
        consistency_out = await self._consistency.check(
            ConsistencyValidatorInput(
                context=context,
                decision=decision,
                prior_memories=prior_memories,
                weldmap_state=weldmap_state,
            )
        )
        consistency_ms = int((monotonic() - t0) * 1000)
        consistency_result = ConsistencyValidationResult(
            result=consistency_out.result,
            factual_consistency=consistency_out.factual_consistency,
            historical_consistency=consistency_out.historical_consistency,
            inconsistencies=consistency_out.inconsistencies,
            timestamp=consistency_out.timestamp,
        )
        stages.append(
            StageResult(
                stage="consistency",
                status=ValidationStageStatus.COMPLETED,
                result=consistency_result,
                duration_ms=consistency_ms,
            )
        )

        # ---- Aggregate ----
        aggregated = self._aggregate(
            shadow_status=shadow_out.result,
            consistency_status=consistency_out.result,
        )

        total_ms = int((monotonic() - pipeline_start) * 1000)

        validation_result = ValidationResult(
            validation_id=ValidationId(value=uuid4()),
            decision_id=decision.decision_id,
            safety_result=safety_result,
            rule_result=rule_result,
            shadow_result=shadow_result,
            consistency_result=consistency_result,
            aggregated_result=aggregated,
            stages=stages,
            total_duration_ms=total_ms,
            timestamp=now,
        )

        # ---- Update escalation tracker ----
        esc_out = await self._escalation.update(
            EscalationTrackerInput(shadow_result=shadow_out.result, case_id=context.case_id)
        )

        return ValidationPipelineOutput(
            validation_result=validation_result,
            escalation_counter_updated=True,
            new_fallback_mode=esc_out.new_state.current_fallback_mode
            if esc_out.mode_changed
            else None,
        )

    # ------------------------------------------------------------------
    # Aggregation logic (Phase 4 Section 15)
    # ------------------------------------------------------------------

    @staticmethod
    def _aggregate(
        shadow_status: ShadowStatus,
        consistency_status: ConsistencyStatus,
    ) -> AggregatedValidationResult:
        if shadow_status == ShadowStatus.CRITICAL:
            return AggregatedValidationResult.ESCALATED
        if shadow_status == ShadowStatus.WARN or consistency_status == ConsistencyStatus.INCONSISTENT:
            return AggregatedValidationResult.REQUIRES_REVIEW
        return AggregatedValidationResult.APPROVED
