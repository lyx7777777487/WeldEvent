"""L1 Cognitive Plane -- DTO instantiation tests.

Verifies that every DTO model can be instantiated with minimal required
fields, and that each instance round-trips through model_dump() /
model_validate().
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.shared.enums import (
    AggregatedValidationResult,
    AudienceType,
    BrainStateType,
    CollaborationLayer,
    ConsensusStatus,
    ConsistencyStatus,
    DecisionPointType,
    EventType,
    FallbackMode,
    InspectionStrategyType,
    KnowledgeType,
    LearningEventType,
    MemoryType,
    NoveltyLevel,
    PersonaType,
    ProcessingStatus,
    PromotionStatus,
    ReasoningMode,
    ReviewStatus,
    ReviewType,
    RiskLevel,
    RoutingDecision,
    RuleStatus,
    SafetyStatus,
    ShadowStatus,
    UrgencyLevel,
    ValidationStageStatus,
    WorkflowType,
)
from src.shared.types import (
    CaseId,
    DecisionId,
    EventId,
    KnowledgeId,
    LearningEventId,
    MemoryId,
    ReviewRequestId,
    SessionId,
    ValidationId,
)

# Context DTOs
from src.shared.dto_context import (
    BrainInternalEvent,
    ContextSnapshot,
    DomainEvent,
    IQACompletedPayload,
    MEACompletedPayload,
    PPACompletedPayload,
    RDAVDACompletedPayload,
    ValidationCriticalPayload,
    WeldMapSnapshot,
    WorkflowEnteredPayload,
    HumanFeedbackReceivedPayload,
    MemoryPromotionRequestedPayload,
)

# Decision DTOs (subpackage)
from src.shared.dto_decision import (
    BrainDecision,
    ConsensusRecommendation,
    DecisionOutput,
    DecisionOutputContent,
    EscalationRecommendation,
    InspectionStrategyRecommendation,
    MarginalRangeRecommendation,
    OptimizationRecommendation,
    ParameterAdjustmentRecommendation,
    ParameterRecommendation,
    RDAStrategyRecommendation,
    ROIRecommendation,
    RiskAssessment,
    RootCauseHypotheses,
    RoutingRecommendation,
    SkipRecommendation,
    VDAStrategyRecommendation,
    WorkflowRecommendation,
)

# Decision value objects
from src.shared.dto_decision.outputs import (
    Constraint,
    CoverageArea,
    EscalationTarget,
    EvidenceReference,
    FocusArea,
    ImpactAssessment,
    InspectionStrategy,
    MarginalRange,
    MitigationSuggestion,
    Optimization,
    ParameterAdjustment,
    ParameterSet,
    ROIEstimate,
    RiskFactor,
    RiskFlag,
    RiskImpact,
    RootCauseHypothesis,
    StandardReference,
)

# Validation DTOs
from src.shared.dto_validation import (
    ConsistencyValidationResult,
    DivergencePoint,
    InconsistencyDetail,
    RuleValidationResult,
    SafetyValidationResult,
    ShadowDecision,
    ShadowValidationResult,
    StageResult,
    ValidationResult,
)

# Memory DTOs
from src.shared.dto_memory import (
    MemoryContent,
    MemoryRecord,
    MemorySearchQuery,
    MemorySearchResult,
)

# Knowledge DTOs
from src.shared.dto_knowledge import (
    CaseLibraryQuery,
    CaseLibraryResult,
    EquipmentKnowledgeQuery,
    EquipmentKnowledgeResult,
    ProcessKnowledgeQuery,
    ProcessKnowledgeResult,
    RAGQuery,
    KnowledgeResult,
    RuleQuery,
    RuleResult,
    StandardsQuery,
    StandardsResult,
)

# Collaboration DTOs
from src.shared.dto_collaboration import (
    ConversationMessage,
    ConversationSession,
    FeedbackContent,
    HumanReviewRequest,
    ResolutionContent,
    ReviewContent,
)

# Learning DTOs
from src.shared.dto_learning import (
    LearningContent,
    LearningEvent,
)

# Persona DTOs
from src.shared.dto_persona import (
    PersonaFrame,
    PersonaSelectionResult,
)

# Reasoning Mode DTOs
from src.shared.dto_reasoning_mode import (
    ReasoningModeSelectionInput,
    ReasoningModeSelectionResult,
)

# Gateway DTOs
from src.shared.dto_gateway import (
    AuditEntry,
    CaseData,
    ConsensusRequest,
    Escalation,
    Explanation,
    Measurement,
    ParameterPatch,
    PromotionRequest,
    PublishResult,
    RecheckRequest,
    RiskAlert,
    WorkflowState,
)

# DeepAgents DTOs
from src.shared.dto_deepagents import (
    AdaptationSuggestion,
    ApplicabilityScore,
    Conclusion,
    Experience,
    Factor,
    Gap,
    Plan,
    SituationDescription,
    Strategy,
    Suggestion,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID4 = uuid4()
NOW = datetime.now(tz=timezone.utc)


def _roundtrip(model_cls, instance):
    """Assert that an instance survives model_dump + model_validate."""
    data = instance.model_dump()
    rebuilt = model_cls.model_validate(data)
    assert rebuilt == instance, f"Round-trip failed for {model_cls.__name__}"


# ===========================================================================
# Identity types
# ===========================================================================


class TestIdentityTypes:
    def test_case_id(self):
        obj = CaseId(value="CASE-001")
        _roundtrip(CaseId, obj)

    def test_decision_id(self):
        obj = DecisionId(value=UUID4)
        _roundtrip(DecisionId, obj)

    def test_memory_id(self):
        obj = MemoryId(value=UUID4)
        _roundtrip(MemoryId, obj)

    def test_validation_id(self):
        obj = ValidationId(value=UUID4)
        _roundtrip(ValidationId, obj)

    def test_review_request_id(self):
        obj = ReviewRequestId(value=UUID4)
        _roundtrip(ReviewRequestId, obj)

    def test_learning_event_id(self):
        obj = LearningEventId(value=UUID4)
        _roundtrip(LearningEventId, obj)

    def test_knowledge_id(self):
        obj = KnowledgeId(value=UUID4)
        _roundtrip(KnowledgeId, obj)

    def test_session_id(self):
        obj = SessionId(value=UUID4)
        _roundtrip(SessionId, obj)

    def test_event_id(self):
        obj = EventId(value=UUID4)
        _roundtrip(EventId, obj)


# ===========================================================================
# Context DTOs
# ===========================================================================


class TestContextDTOs:
    def test_context_snapshot(self):
        obj = ContextSnapshot(
            case_id=CaseId(value="CASE-001"),
            event_type=EventType.WORKFLOW_ENTERED,
            workflow_state={"step": 1},
            case_data={"type": "weld"},
            measurements=[{"param": "voltage", "value": 22.5}],
            memory_match_confidence=0.8,
            knowledge_coverage=0.9,
            event_novelty=NoveltyLevel.KNOWN,
            validation_critical_count=0,
            timestamp=NOW,
        )
        _roundtrip(ContextSnapshot, obj)

    def test_weldmap_snapshot(self):
        obj = WeldMapSnapshot(
            case_id=CaseId(value="CASE-001"),
            workflow_state={"step": 1},
            measurements=[],
            decisions=[],
            events=[],
            snapshot_at=NOW,
        )
        _roundtrip(WeldMapSnapshot, obj)

    def test_domain_event(self):
        obj = DomainEvent(
            event_id=EventId(value=UUID4),
            event_type=EventType.IQA_COMPLETED,
            case_id=CaseId(value="CASE-001"),
            timestamp=NOW,
            source="gateway",
            payload={"result": "pass"},
        )
        _roundtrip(DomainEvent, obj)

    def test_workflow_entered_payload(self):
        obj = WorkflowEnteredPayload(
            workflow_type="FULL",
            case_type="standard",
            parameters={},
            operator_id="OP-001",
        )
        _roundtrip(WorkflowEnteredPayload, obj)

    def test_iqa_completed_payload(self):
        obj = IQACompletedPayload(
            iqa_result="pass",
            defect_indicators=[],
            measurement_summary={},
            inspector_id="INS-001",
        )
        _roundtrip(IQACompletedPayload, obj)

    def test_ppa_completed_payload(self):
        obj = PPACompletedPayload(
            ppa_result="pass",
            parameter_deviations=[],
            quality_indicators={},
            inspector_id="INS-001",
        )
        _roundtrip(PPACompletedPayload, obj)

    def test_mea_completed_payload(self):
        obj = MEACompletedPayload(
            mea_result="defect",
            severity=RiskLevel.HIGH,
            inspector_id="INS-001",
        )
        _roundtrip(MEACompletedPayload, obj)

    def test_rda_vda_completed_payload(self):
        obj = RDAVDACompletedPayload(
            rda_findings=[],
            vda_findings=[],
            combined_assessment="acceptable",
            inspector_id="INS-001",
        )
        _roundtrip(RDAVDACompletedPayload, obj)

    def test_human_feedback_received_payload(self):
        obj = HumanFeedbackReceivedPayload(
            decision_id=DecisionId(value=UUID4),
            operator_id="OP-001",
            feedback_type="correction",
            feedback_text="Adjust voltage",
            rating=4,
        )
        _roundtrip(HumanFeedbackReceivedPayload, obj)

    def test_memory_promotion_requested_payload(self):
        obj = MemoryPromotionRequestedPayload(
            memory_id=MemoryId(value=UUID4),
            promotion_rationale="Validated decision",
            source_decision_id=DecisionId(value=UUID4),
        )
        _roundtrip(MemoryPromotionRequestedPayload, obj)

    def test_validation_critical_payload(self):
        obj = ValidationCriticalPayload(
            validation_id=ValidationId(value=UUID4),
            decision_id=DecisionId(value=UUID4),
            consecutive_critical_count=2,
            fallback_mode=FallbackMode.NONE,
        )
        _roundtrip(ValidationCriticalPayload, obj)

    def test_brain_internal_event(self):
        obj = BrainInternalEvent(
            event_type="DECISION_INITIATED",
            decision_id=DecisionId(value=UUID4),
            timestamp=NOW,
            payload={"key": "value"},
        )
        _roundtrip(BrainInternalEvent, obj)


# ===========================================================================
# Decision value objects
# ===========================================================================


class TestDecisionValueObjects:
    def test_risk_flag(self):
        obj = RiskFlag(flag_type="safety", description="Over-voltage", severity=RiskLevel.HIGH)
        _roundtrip(RiskFlag, obj)

    def test_constraint(self):
        obj = Constraint(name="max_voltage", value="30V", source="ISO-3834")
        _roundtrip(Constraint, obj)

    def test_parameter_set(self):
        obj = ParameterSet(parameters={"voltage": "22V", "speed": "300mm/min"})
        _roundtrip(ParameterSet, obj)

    def test_parameter_adjustment(self):
        obj = ParameterAdjustment(
            parameter_name="voltage",
            current_value="22V",
            proposed_value="24V",
            unit="V",
        )
        _roundtrip(ParameterAdjustment, obj)

    def test_roi_estimate(self):
        obj = ROIEstimate(
            estimated_roi=1.5,
            cost_projection={"total": 1000},
            benefit_projection={"total": 1500},
            assumptions=["stable process"],
        )
        _roundtrip(ROIEstimate, obj)

    def test_marginal_range(self):
        obj = MarginalRange(parameter="voltage", lower_bound="20V", upper_bound="25V", unit="V")
        _roundtrip(MarginalRange, obj)

    def test_inspection_strategy(self):
        obj = InspectionStrategy(
            strategy_type=InspectionStrategyType.STANDARD,
            coverage_areas=["weld-seam-1"],
        )
        _roundtrip(InspectionStrategy, obj)

    def test_standard_reference(self):
        obj = StandardReference(standard_id="ISO-3834", section="5", clause="5.2.1")
        _roundtrip(StandardReference, obj)

    def test_evidence_reference(self):
        obj = EvidenceReference(source="measurement", reference_id="M-001", description="Voltage reading")
        _roundtrip(EvidenceReference, obj)

    def test_risk_factor(self):
        obj = RiskFactor(factor="porosity", likelihood=0.3, impact=RiskLevel.MEDIUM)
        _roundtrip(RiskFactor, obj)

    def test_mitigation_suggestion(self):
        obj = MitigationSuggestion(suggestion="Reduce speed", effectiveness=0.8)
        _roundtrip(MitigationSuggestion, obj)

    def test_risk_impact(self):
        obj = RiskImpact(
            risk_level=RiskLevel.HIGH,
            affected_areas=["weld-seam-1"],
            description="Potential cracking",
        )
        _roundtrip(RiskImpact, obj)

    def test_root_cause_hypothesis(self):
        obj = RootCauseHypothesis(
            hypothesis="Contaminated surface",
            likelihood=0.6,
            supporting_evidence=[EvidenceReference(source="visual", reference_id="E-001", description="Rust spots")],
            investigation_suggestions=["Clean surface and re-inspect"],
        )
        _roundtrip(RootCauseHypothesis, obj)

    def test_focus_area(self):
        obj = FocusArea(area="heat-affected zone", rationale="Historical defect location")
        _roundtrip(FocusArea, obj)

    def test_optimization(self):
        obj = Optimization(
            description="Increase travel speed",
            target_parameter="speed",
            current_value="300mm/min",
            proposed_value="350mm/min",
        )
        _roundtrip(Optimization, obj)

    def test_impact_assessment(self):
        obj = ImpactAssessment(
            quality_impact="positive",
            efficiency_impact="positive",
            risk_impact="neutral",
        )
        _roundtrip(ImpactAssessment, obj)

    def test_coverage_area(self):
        obj = CoverageArea(area_id="A1", area_name="weld-seam-1", priority=UrgencyLevel.ROUTINE)
        _roundtrip(CoverageArea, obj)

    def test_escalation_target(self):
        obj = EscalationTarget(target_type="engineer", target_id="ENG-001")
        _roundtrip(EscalationTarget, obj)


# ===========================================================================
# Decision Output Content subtypes (all 15)
# ===========================================================================


class TestDecisionOutputContent:
    def _make_workflow_recommendation(self):
        return WorkflowRecommendation(
            workflow_type=WorkflowType.FULL,
            confidence=0.9,
            rationale="Standard workflow appropriate",
            inspection_strategy=InspectionStrategy(
                strategy_type=InspectionStrategyType.STANDARD,
                coverage_areas=["seam-1"],
            ),
            parameters=ParameterSet(parameters={"voltage": "22V"}),
            roi_estimate=ROIEstimate(
                estimated_roi=1.2,
                cost_projection={"total": 500},
                benefit_projection={"total": 600},
                assumptions=["stable"],
            ),
            marginal_range=MarginalRange(
                parameter="voltage", lower_bound="20V", upper_bound="25V", unit="V"
            ),
            risk_flags=[RiskFlag(flag_type="safety", description="Minor", severity=RiskLevel.LOW)],
        )

    def test_workflow_recommendation(self):
        obj = self._make_workflow_recommendation()
        _roundtrip(WorkflowRecommendation, obj)

    def test_inspection_strategy_recommendation(self):
        obj = InspectionStrategyRecommendation(
            strategy_type=InspectionStrategyType.INTENSIVE,
            coverage_areas=[CoverageArea(area_id="A1", area_name="seam-1", priority=UrgencyLevel.URGENT)],
            confidence=0.85,
            rationale="Defect indicators present",
        )
        _roundtrip(InspectionStrategyRecommendation, obj)

    def test_parameter_recommendation(self):
        obj = ParameterRecommendation(
            parameters=ParameterSet(parameters={"voltage": "23V"}),
            confidence=0.88,
            rationale="Within acceptable range",
            constraints_applied=[Constraint(name="max_voltage", value="30V", source="ISO-3834")],
        )
        _roundtrip(ParameterRecommendation, obj)

    def test_roi_recommendation(self):
        obj = ROIRecommendation(
            estimated_roi=1.4,
            cost_projection={"total": 1000},
            benefit_projection={"total": 1400},
            confidence=0.75,
            assumptions=["No rework needed"],
        )
        _roundtrip(ROIRecommendation, obj)

    def test_marginal_range_recommendation(self):
        obj = MarginalRangeRecommendation(
            marginal_ranges=[MarginalRange(parameter="voltage", lower_bound="20V", upper_bound="25V", unit="V")],
            confidence=0.9,
            rationale="Standard operating range",
            applicable_standards=[StandardReference(standard_id="ISO-3834", section="5", clause="5.1")],
        )
        _roundtrip(MarginalRangeRecommendation, obj)

    def test_parameter_adjustment_recommendation(self):
        obj = ParameterAdjustmentRecommendation(
            adjustments=[ParameterAdjustment(parameter_name="voltage", current_value="22V", proposed_value="24V", unit="V")],
            confidence=0.82,
            rationale="Optimize for quality",
            risk_impact=RiskImpact(risk_level=RiskLevel.LOW, affected_areas=["seam-1"], description="Minor adjustment"),
        )
        _roundtrip(ParameterAdjustmentRecommendation, obj)

    def test_risk_assessment(self):
        obj = RiskAssessment(
            risk_level=RiskLevel.MEDIUM,
            risk_factors=[RiskFactor(factor="porosity", likelihood=0.3, impact=RiskLevel.MEDIUM)],
            confidence=0.7,
            mitigation_suggestions=[MitigationSuggestion(suggestion="Reduce speed", effectiveness=0.8)],
        )
        _roundtrip(RiskAssessment, obj)

    def test_consensus_recommendation(self):
        obj = ConsensusRecommendation(
            consensus_status="ACHIEVED",
            agreeing_evidence=[EvidenceReference(source="data", reference_id="E-1", description="Match")],
            disagreeing_evidence=[],
            recommendation="Proceed with standard workflow",
            confidence=0.85,
        )
        _roundtrip(ConsensusRecommendation, obj)

    def test_skip_recommendation(self):
        obj = SkipRecommendation(
            skippable=True,
            justification="Low risk case",
            risk_if_skipped=RiskAssessment(
                risk_level=RiskLevel.LOW,
                risk_factors=[],
                confidence=0.9,
                mitigation_suggestions=[],
            ),
            confidence=0.95,
        )
        _roundtrip(SkipRecommendation, obj)

    def test_escalation_recommendation(self):
        obj = EscalationRecommendation(
            escalate=True,
            escalation_reason="Critical validation failure",
            escalation_target=EscalationTarget(target_type="senior_engineer", target_id="ENG-002"),
            urgency=UrgencyLevel.CRITICAL,
            supporting_evidence=[EvidenceReference(source="validation", reference_id="V-1", description="Shadow CRITICAL")],
        )
        _roundtrip(EscalationRecommendation, obj)

    def test_rda_strategy_recommendation(self):
        obj = RDAStrategyRecommendation(
            strategy={"strategy_name": "destructive", "description": "Destructive testing"},
            focus_areas=[FocusArea(area="HAZ", rationale="Defect location")],
            expected_findings=["crack propagation"],
            confidence=0.7,
        )
        _roundtrip(RDAStrategyRecommendation, obj)

    def test_vda_strategy_recommendation(self):
        obj = VDAStrategyRecommendation(
            strategy={"strategy_name": "ultrasonic", "description": "Ultrasonic testing"},
            focus_areas=[FocusArea(area="weld-root", rationale="Inaccessible visually")],
            expected_findings=["internal defects"],
            confidence=0.75,
        )
        _roundtrip(VDAStrategyRecommendation, obj)

    def test_root_cause_hypotheses(self):
        obj = RootCauseHypotheses(
            hypotheses=[
                RootCauseHypothesis(
                    hypothesis="Contamination",
                    likelihood=0.6,
                    supporting_evidence=[],
                    investigation_suggestions=["Clean and re-test"],
                )
            ],
            ranked_by_likelihood=True,
            confidence=0.65,
        )
        _roundtrip(RootCauseHypotheses, obj)

    def test_routing_recommendation(self):
        obj = RoutingRecommendation(
            route=RoutingDecision.ACCEPT,
            destination="next_stage",
            confidence=0.92,
            rationale="All checks passed",
        )
        _roundtrip(RoutingRecommendation, obj)

    def test_optimization_recommendation(self):
        obj = OptimizationRecommendation(
            optimizations=[
                Optimization(
                    description="Increase speed",
                    target_parameter="speed",
                    current_value="300mm/min",
                    proposed_value="350mm/min",
                )
            ],
            expected_impact=ImpactAssessment(
                quality_impact="positive",
                efficiency_impact="positive",
                risk_impact="neutral",
            ),
            confidence=0.8,
            implementation_effort="LOW",
        )
        _roundtrip(OptimizationRecommendation, obj)


# ===========================================================================
# DecisionOutput and BrainDecision
# ===========================================================================


class TestBrainDecisionDTOs:
    def test_decision_output_with_workflow_recommendation(self):
        content = WorkflowRecommendation(
            workflow_type=WorkflowType.FULL,
            confidence=0.9,
            rationale="Standard",
            inspection_strategy=InspectionStrategy(
                strategy_type=InspectionStrategyType.STANDARD,
                coverage_areas=["seam-1"],
            ),
            parameters=ParameterSet(parameters={"voltage": "22V"}),
            roi_estimate=ROIEstimate(
                estimated_roi=1.2,
                cost_projection={},
                benefit_projection={},
                assumptions=[],
            ),
            marginal_range=MarginalRange(parameter="voltage", lower_bound="20V", upper_bound="25V", unit="V"),
            risk_flags=[],
        )
        obj = DecisionOutput(content=content, confidence=0.9)
        _roundtrip(DecisionOutput, obj)

    def test_decision_output_with_risk_assessment(self):
        content = RiskAssessment(
            risk_level=RiskLevel.HIGH,
            risk_factors=[RiskFactor(factor="cracking", likelihood=0.5, impact=RiskLevel.HIGH)],
            confidence=0.8,
            mitigation_suggestions=[MitigationSuggestion(suggestion="Pre-heat", effectiveness=0.9)],
        )
        obj = DecisionOutput(content=content, confidence=0.8)
        _roundtrip(DecisionOutput, obj)

    def test_brain_decision(self):
        wf_rec = WorkflowRecommendation(
            workflow_type=WorkflowType.FULL,
            confidence=0.9,
            rationale="Standard",
            inspection_strategy=InspectionStrategy(
                strategy_type=InspectionStrategyType.STANDARD,
                coverage_areas=["seam-1"],
            ),
            parameters=ParameterSet(parameters={"voltage": "22V"}),
            roi_estimate=ROIEstimate(
                estimated_roi=1.2,
                cost_projection={},
                benefit_projection={},
                assumptions=[],
            ),
            marginal_range=MarginalRange(parameter="voltage", lower_bound="20V", upper_bound="25V", unit="V"),
            risk_flags=[],
        )
        output = DecisionOutput(content=wf_rec, confidence=0.9)
        obj = BrainDecision(
            decision_id=DecisionId(value=UUID4),
            case_id=CaseId(value="CASE-001"),
            trigger_event_type=EventType.WORKFLOW_ENTERED,
            decision_point=DecisionPointType.DP1,
            persona=PersonaType.PLANNER,
            reasoning_mode=ReasoningMode.ROUTINE,
            state=BrainStateType.DECISION_GENERATION,
            outputs=[output],
            validation_result=AggregatedValidationResult.APPROVED,
            confidence=0.9,
            created_at=NOW,
        )
        _roundtrip(BrainDecision, obj)

    def test_brain_decision_with_risk_assessment_output(self):
        risk_assess = RiskAssessment(
            risk_level=RiskLevel.CRITICAL,
            risk_factors=[RiskFactor(factor="crack", likelihood=0.7, impact=RiskLevel.CRITICAL)],
            confidence=0.85,
            mitigation_suggestions=[],
        )
        output = DecisionOutput(content=risk_assess, confidence=0.85)
        obj = BrainDecision(
            decision_id=DecisionId(value=UUID4),
            case_id=CaseId(value="CASE-002"),
            trigger_event_type=EventType.MEA_COMPLETED,
            decision_point=DecisionPointType.DP2,
            persona=PersonaType.COPILOT,
            reasoning_mode=ReasoningMode.ADAPTIVE,
            state=BrainStateType.VALIDATION,
            outputs=[output],
            confidence=0.85,
            created_at=NOW,
        )
        _roundtrip(BrainDecision, obj)


# ===========================================================================
# Validation DTOs
# ===========================================================================


class TestValidationDTOs:
    def test_divergence_point(self):
        obj = DivergencePoint(
            output_field="voltage",
            brain_value="22V",
            shadow_value="24V",
            divergence_severity=0.3,
        )
        _roundtrip(DivergencePoint, obj)

    def test_shadow_decision(self):
        obj = ShadowDecision(decision_type="parameter", outputs={"voltage": "24V"}, confidence=0.8)
        _roundtrip(ShadowDecision, obj)

    def test_safety_validation_result(self):
        obj = SafetyValidationResult(result=SafetyStatus.PASS, checked_rules=["R1", "R2"], timestamp=NOW)
        _roundtrip(SafetyValidationResult, obj)

    def test_rule_validation_result(self):
        obj = RuleValidationResult(result=RuleStatus.PASS, violated_rules=[], timestamp=NOW)
        _roundtrip(RuleValidationResult, obj)

    def test_shadow_validation_result(self):
        obj = ShadowValidationResult(
            result=ShadowStatus.CONCUR,
            shadow_decision=ShadowDecision(decision_type="parameter", outputs={}, confidence=0.9),
            alignment_score=0.95,
            divergence_points=[],
            timestamp=NOW,
        )
        _roundtrip(ShadowValidationResult, obj)

    def test_inconsistency_detail(self):
        obj = InconsistencyDetail(
            check_type="factual",
            description="Voltage mismatch",
            severity=0.4,
            reference="R-001",
        )
        _roundtrip(InconsistencyDetail, obj)

    def test_consistency_validation_result(self):
        obj = ConsistencyValidationResult(
            result=ConsistencyStatus.CONSISTENT,
            factual_consistency=ConsistencyStatus.CONSISTENT,
            historical_consistency=ConsistencyStatus.CONSISTENT,
            inconsistencies=[],
            timestamp=NOW,
        )
        _roundtrip(ConsistencyValidationResult, obj)

    def test_stage_result(self):
        obj = StageResult(
            stage="safety",
            status=ValidationStageStatus.COMPLETED,
            result=SafetyValidationResult(result=SafetyStatus.PASS, checked_rules=["R1"], timestamp=NOW),
            duration_ms=50,
        )
        _roundtrip(StageResult, obj)

    def test_validation_result(self):
        obj = ValidationResult(
            validation_id=ValidationId(value=UUID4),
            decision_id=DecisionId(value=UUID4),
            safety_result=SafetyValidationResult(result=SafetyStatus.PASS, checked_rules=["R1"], timestamp=NOW),
            rule_result=RuleValidationResult(result=RuleStatus.PASS, violated_rules=[], timestamp=NOW),
            shadow_result=ShadowValidationResult(
                result=ShadowStatus.CONCUR,
                shadow_decision=ShadowDecision(decision_type="parameter", outputs={}, confidence=0.9),
                alignment_score=0.95,
                divergence_points=[],
                timestamp=NOW,
            ),
            consistency_result=ConsistencyValidationResult(
                result=ConsistencyStatus.CONSISTENT,
                factual_consistency=ConsistencyStatus.CONSISTENT,
                historical_consistency=ConsistencyStatus.CONSISTENT,
                inconsistencies=[],
                timestamp=NOW,
            ),
            aggregated_result=AggregatedValidationResult.APPROVED,
            stages=[
                StageResult(
                    stage="safety",
                    status=ValidationStageStatus.COMPLETED,
                    duration_ms=50,
                )
            ],
            total_duration_ms=150,
            timestamp=NOW,
        )
        _roundtrip(ValidationResult, obj)


# ===========================================================================
# Memory DTOs
# ===========================================================================


class TestMemoryDTOs:
    def test_memory_content(self):
        obj = MemoryContent(summary="Approved decision", details={"key": "val"}, feature_vector=[0.1, 0.2, 0.3])
        _roundtrip(MemoryContent, obj)

    def test_memory_search_query(self):
        obj = MemorySearchQuery(
            case_features={"type": "weld"},
            feature_vector=[0.1, 0.2],
            memory_type=MemoryType.APPROVED_DECISION,
            max_results=5,
            min_confidence=0.5,
        )
        _roundtrip(MemorySearchQuery, obj)

    def test_memory_search_result(self):
        obj = MemorySearchResult(
            memory_id=MemoryId(value=UUID4),
            content=MemoryContent(summary="Test", details={}, feature_vector=[0.1]),
            similarity_score=0.85,
            promotion_status=PromotionStatus.PROMOTED,
            created_at=NOW,
        )
        _roundtrip(MemorySearchResult, obj)

    def test_memory_record(self):
        obj = MemoryRecord(
            memory_id=MemoryId(value=UUID4),
            memory_type=MemoryType.APPROVED_DECISION,
            content=MemoryContent(summary="Test record", details={}, feature_vector=[0.5]),
            source_decision_id=DecisionId(value=UUID4),
            promotion_status=PromotionStatus.RAW,
            created_at=NOW,
        )
        _roundtrip(MemoryRecord, obj)


# ===========================================================================
# Knowledge DTOs
# ===========================================================================


class TestKnowledgeDTOs:
    def test_rag_query(self):
        obj = RAGQuery(query_text="welding voltage standards")
        _roundtrip(RAGQuery, obj)

    def test_knowledge_result(self):
        obj = KnowledgeResult(
            knowledge_id=KnowledgeId(value=UUID4),
            knowledge_type=KnowledgeType.STANDARD,
            content="ISO 3834 requirements",
            relevance_score=0.9,
            source_reference="ISO-3834",
        )
        _roundtrip(KnowledgeResult, obj)

    def test_rule_query(self):
        obj = RuleQuery(rule_category="safety", context={"voltage": "22V"})
        _roundtrip(RuleQuery, obj)

    def test_rule_result(self):
        obj = RuleResult(
            rule_id="R-001",
            rule_text="Voltage must not exceed 30V",
            applicability=0.95,
            constraints=[Constraint(name="max_voltage", value="30V", source="ISO-3834")],
        )
        _roundtrip(RuleResult, obj)

    def test_standards_query(self):
        obj = StandardsQuery(standard_id="ISO-3834")
        _roundtrip(StandardsQuery, obj)

    def test_standards_result(self):
        obj = StandardsResult(
            standard_id="ISO-3834",
            section="5",
            clause="5.1",
            text="Requirements for quality",
            relevance=0.9,
        )
        _roundtrip(StandardsResult, obj)

    def test_equipment_knowledge_query(self):
        obj = EquipmentKnowledgeQuery(equipment_id="WELDER-001")
        _roundtrip(EquipmentKnowledgeQuery, obj)

    def test_equipment_knowledge_result(self):
        obj = EquipmentKnowledgeResult(
            equipment_id="WELDER-001",
            specifications={"model": "X-100"},
            operational_limits={"max_voltage": "30V"},
            maintenance_requirements=["Annual calibration"],
        )
        _roundtrip(EquipmentKnowledgeResult, obj)

    def test_process_knowledge_query(self):
        obj = ProcessKnowledgeQuery(process_type="MIG")
        _roundtrip(ProcessKnowledgeQuery, obj)

    def test_process_knowledge_result(self):
        obj = ProcessKnowledgeResult(
            process_id="PROC-001",
            recommended_parameters=ParameterSet(parameters={"voltage": "22V"}),
            quality_criteria={"porosity": "<2%"},
            common_defects=["porosity", "lack of fusion"],
        )
        _roundtrip(ProcessKnowledgeResult, obj)

    def test_case_library_query(self):
        obj = CaseLibraryQuery(defect_type="porosity")
        _roundtrip(CaseLibraryQuery, obj)

    def test_case_library_result(self):
        obj = CaseLibraryResult(
            case_id="CASE-HIST-001",
            defect_description="Porosity in root pass",
            resolution="Adjusted gas flow",
            outcome="Approved after rework",
            similarity_score=0.88,
        )
        _roundtrip(CaseLibraryResult, obj)


# ===========================================================================
# Collaboration DTOs
# ===========================================================================


class TestCollaborationDTOs:
    def test_review_content(self):
        obj = ReviewContent(
            decision_id=DecisionId(value=UUID4),
            summary="Risk assessment review",
            reasoning_summary="Adaptive reasoning applied",
            risk_summary="Medium risk identified",
            recommendation="Approve with conditions",
            confidence=0.8,
        )
        _roundtrip(ReviewContent, obj)

    def test_resolution_content(self):
        obj = ResolutionContent(
            resolution="Approved with monitoring",
            conditions=["Monitor for 24h"],
        )
        _roundtrip(ResolutionContent, obj)

    def test_human_review_request(self):
        obj = HumanReviewRequest(
            request_id=ReviewRequestId(value=UUID4),
            decision_id=DecisionId(value=UUID4),
            collaboration_layer=CollaborationLayer.L1,
            review_type=ReviewType.APPROVAL,
            status=ReviewStatus.PENDING,
            content=ReviewContent(
                decision_id=DecisionId(value=UUID4),
                summary="Review needed",
                reasoning_summary="Adaptive mode",
                risk_summary="Medium risk",
                recommendation="Approve",
                confidence=0.7,
            ),
            created_at=NOW,
        )
        _roundtrip(HumanReviewRequest, obj)

    def test_feedback_content(self):
        obj = FeedbackContent(
            decision_id=DecisionId(value=UUID4),
            operator_id="OP-001",
            feedback_type="correction",
            feedback_text="Voltage should be 24V",
            rating=3,
            timestamp=NOW,
        )
        _roundtrip(FeedbackContent, obj)

    def test_conversation_message(self):
        obj = ConversationMessage(role="operator", content="Why was 22V selected?", timestamp=NOW)
        _roundtrip(ConversationMessage, obj)

    def test_conversation_session(self):
        obj = ConversationSession(
            session_id=SessionId(value=UUID4),
            decision_id=DecisionId(value=UUID4),
            operator_id="OP-001",
            messages=[ConversationMessage(role="operator", content="Question", timestamp=NOW)],
            status="active",
            created_at=NOW,
        )
        _roundtrip(ConversationSession, obj)


# ===========================================================================
# Learning DTOs
# ===========================================================================


class TestLearningDTOs:
    def test_learning_content(self):
        obj = LearningContent(
            event_type=LearningEventType.EXPERIENCE,
            source_decision_id=DecisionId(value=UUID4),
            data={"key": "value"},
        )
        _roundtrip(LearningContent, obj)

    def test_learning_event(self):
        obj = LearningEvent(
            learning_event_id=LearningEventId(value=UUID4),
            event_type=LearningEventType.FEEDBACK,
            source_decision_id=DecisionId(value=UUID4),
            content=LearningContent(
                event_type=LearningEventType.FEEDBACK,
                data={"rating": 4},
            ),
            processing_status=ProcessingStatus.PENDING,
            created_at=NOW,
        )
        _roundtrip(LearningEvent, obj)


# ===========================================================================
# Persona DTOs
# ===========================================================================


class TestPersonaDTOs:
    def test_persona_frame(self):
        obj = PersonaFrame(
            persona_type=PersonaType.PLANNER,
            decision_point=DecisionPointType.DP1,
            deepagents_port_preference=["reasoning", "planning"],
            reasoning_orientation="systematic",
            output_templates=["workflow_rec"],
            knowledge_port_priority=["standards", "rules"],
            memory_query_bias={"recency": 0.7},
        )
        _roundtrip(PersonaFrame, obj)

    def test_persona_selection_result(self):
        obj = PersonaSelectionResult(
            primary_persona=PersonaType.PLANNER,
            secondary_persona=PersonaType.COPILOT,
            frame=PersonaFrame(
                persona_type=PersonaType.PLANNER,
                decision_point=DecisionPointType.DP1,
                deepagents_port_preference=["reasoning"],
                reasoning_orientation="systematic",
                output_templates=["workflow_rec"],
                knowledge_port_priority=["standards"],
                memory_query_bias={},
            ),
        )
        _roundtrip(PersonaSelectionResult, obj)


# ===========================================================================
# Reasoning Mode DTOs
# ===========================================================================


class TestReasoningModeDTOs:
    def test_reasoning_mode_selection_input(self):
        obj = ReasoningModeSelectionInput(
            memory_match_confidence=0.85,
            knowledge_coverage=0.9,
            event_novelty=NoveltyLevel.KNOWN,
            validation_critical_count=0,
        )
        _roundtrip(ReasoningModeSelectionInput, obj)

    def test_reasoning_mode_selection_result(self):
        obj = ReasoningModeSelectionResult(
            selected_mode=ReasoningMode.ROUTINE,
            selection_rationale="High memory match, known event",
            input_signals=ReasoningModeSelectionInput(
                memory_match_confidence=0.85,
                knowledge_coverage=0.9,
                event_novelty=NoveltyLevel.KNOWN,
                validation_critical_count=0,
            ),
        )
        _roundtrip(ReasoningModeSelectionResult, obj)


# ===========================================================================
# Gateway DTOs
# ===========================================================================


class TestGatewayDTOs:
    def test_publish_result(self):
        obj = PublishResult(success=True, weldmap_path="/weldmap/case/1", timestamp=NOW)
        _roundtrip(PublishResult, obj)

    def test_explanation(self):
        obj = Explanation(
            explanation_id=UUID4,
            decision_id=DecisionId(value=UUID4),
            audience=AudienceType.OPERATOR,
            explanation_text="The voltage was set to 22V based on standard parameters.",
            key_factors=["material type", "joint geometry"],
            confidence_justification="High memory match confidence",
            created_at=NOW,
        )
        _roundtrip(Explanation, obj)

    def test_parameter_patch(self):
        obj = ParameterPatch(
            patch_id=UUID4,
            decision_id=DecisionId(value=UUID4),
            case_id=CaseId(value="CASE-001"),
            parameter_adjustments=[
                ParameterAdjustment(parameter_name="voltage", current_value="22V", proposed_value="24V", unit="V")
            ],
            confidence=0.85,
            rationale="Optimization based on feedback",
            created_at=NOW,
        )
        _roundtrip(ParameterPatch, obj)

    def test_recheck_request(self):
        obj = RecheckRequest(
            recheck_id=UUID4,
            decision_id=DecisionId(value=UUID4),
            case_id=CaseId(value="CASE-001"),
            recheck_type="full_inspection",
            target_areas=["weld-seam-1"],
            rationale="Post-adjustment verification",
            urgency=UrgencyLevel.ROUTINE,
            created_at=NOW,
        )
        _roundtrip(RecheckRequest, obj)

    def test_consensus_request(self):
        obj = ConsensusRequest(
            consensus_id=UUID4,
            decision_id=DecisionId(value=UUID4),
            case_id=CaseId(value="CASE-001"),
            consensus_status=ConsensusStatus.PARTIAL,
            agreeing_evidence=["E-001"],
            disagreeing_evidence=["E-002"],
            recommendation="Proceed with caution",
            confidence=0.7,
            created_at=NOW,
        )
        _roundtrip(ConsensusRequest, obj)

    def test_risk_alert(self):
        obj = RiskAlert(
            alert_id=UUID4,
            decision_id=DecisionId(value=UUID4),
            case_id=CaseId(value="CASE-001"),
            risk_level=RiskLevel.HIGH,
            risk_factors=["porosity", "cracking"],
            mitigation_suggestions=["Pre-heat", "Reduce speed"],
            confidence=0.8,
            created_at=NOW,
        )
        _roundtrip(RiskAlert, obj)

    def test_escalation(self):
        obj = Escalation(
            escalation_id=UUID4,
            decision_id=DecisionId(value=UUID4),
            case_id=CaseId(value="CASE-001"),
            reason="Consecutive critical validations",
            urgency=UrgencyLevel.CRITICAL,
            fallback_mode=FallbackMode.COGNITIVE_FALLBACK,
            supporting_evidence=[EvidenceReference(source="validation", reference_id="V-1", description="Shadow CRITICAL")],
            created_at=NOW,
        )
        _roundtrip(Escalation, obj)

    def test_promotion_request(self):
        obj = PromotionRequest(
            memory_id=MemoryId(value=UUID4),
            promotion_rationale="Validated by expert review",
        )
        _roundtrip(PromotionRequest, obj)

    def test_workflow_state(self):
        obj = WorkflowState(
            case_id=CaseId(value="CASE-001"),
            status="in_progress",
            current_activity="IQA",
            parameters={"voltage": "22V"},
            updated_at=NOW,
        )
        _roundtrip(WorkflowState, obj)

    def test_case_data(self):
        obj = CaseData(
            case_id=CaseId(value="CASE-001"),
            case_type="standard_weld",
            creation_date=NOW,
            status="active",
            metadata={},
        )
        _roundtrip(CaseData, obj)

    def test_measurement(self):
        obj = Measurement(
            measurement_id="M-001",
            case_id=CaseId(value="CASE-001"),
            parameter="voltage",
            value="22.5",
            unit="V",
            timestamp=NOW,
        )
        _roundtrip(Measurement, obj)

    def test_audit_entry(self):
        obj = AuditEntry(
            entry_id="AE-001",
            case_id=CaseId(value="CASE-001"),
            action="decision_published",
            actor="brain",
            timestamp=NOW,
            details={"decision_id": str(UUID4)},
        )
        _roundtrip(AuditEntry, obj)


# ===========================================================================
# DeepAgents DTOs
# ===========================================================================


class TestDeepAgentsDTOs:
    def test_conclusion(self):
        obj = Conclusion(statement="Voltage is within range", confidence=0.9, supporting_evidence=["M-001"])
        _roundtrip(Conclusion, obj)

    def test_plan(self):
        obj = Plan(steps=["Step 1", "Step 2"], expected_outcome="Approved decision", confidence=0.85)
        _roundtrip(Plan, obj)

    def test_strategy(self):
        obj = Strategy(name="systematic", description="Step-by-step analysis", applicable_conditions=["known event"])
        _roundtrip(Strategy, obj)

    def test_gap(self):
        obj = Gap(description="Missing equipment specs", severity=0.6)
        _roundtrip(Gap, obj)

    def test_suggestion(self):
        obj = Suggestion(description="Add equipment validation", expected_improvement="Better coverage", priority=UrgencyLevel.ROUTINE)
        _roundtrip(Suggestion, obj)

    def test_factor(self):
        obj = Factor(name="voltage", description="Operating voltage", weight=0.8)
        _roundtrip(Factor, obj)

    def test_experience(self):
        obj = Experience(case_id="CASE-HIST-001", outcome="approved", applicability=0.75, summary="Similar case approved")
        _roundtrip(Experience, obj)

    def test_applicability_score(self):
        obj = ApplicabilityScore(experience_id="EXP-001", score=0.8, reasoning="Similar parameters")
        _roundtrip(ApplicabilityScore, obj)

    def test_adaptation_suggestion(self):
        obj = AdaptationSuggestion(
            parameter="voltage",
            current_value="22V",
            suggested_value="23V",
            rationale="Slight increase for better penetration",
        )
        _roundtrip(AdaptationSuggestion, obj)

    def test_situation_description(self):
        obj = SituationDescription(
            current_state={"step": "IQA"},
            relevant_factors=["voltage", "speed"],
            constraints=["max 30V"],
        )
        _roundtrip(SituationDescription, obj)


def test_interaction_enums_exist():
    from src.shared.enums import (
        MatchStrategy,
        InstructionType,
        InstructionStatus,
        ResponseType,
        SessionStatus,
        DesignPhase,
        InterventionGranularity,
        AnnotationType,
        ResultLevel,
        SenderType,
        ChatMessageType,
        UserActionType,
    )
    assert MatchStrategy.KEYWORD == "keyword"
    assert InstructionType.UPGRADE_STRATEGY == "upgrade_strategy"
    assert InstructionStatus.ACTIVE == "active"
    assert ResponseType.TEXT_REPLY == "text_reply"
    assert SessionStatus.ACTIVE == "active"
    assert DesignPhase.COLLECTING == "collecting"
    assert InterventionGranularity.IMAGE == "image"
    assert AnnotationType.BBOX == "bbox"
    assert ResultLevel.ROUTINE == "routine"
    assert SenderType.SYSTEM == "system"
    assert ChatMessageType.TEXT == "text"
    assert UserActionType.ZOOM_IN == "zoom_in"


def test_interaction_types_exist():
    from src.shared.types import ImageId, InstructionId, OperatorId
    from uuid import uuid4
    img = ImageId(value="IMG-CASE001-003")
    assert img.value == "IMG-CASE001-003"
    inst = InstructionId(value=uuid4())
    assert str(inst.value)
    op = OperatorId(value="zhangsan")
    assert op.value == "zhangsan"
