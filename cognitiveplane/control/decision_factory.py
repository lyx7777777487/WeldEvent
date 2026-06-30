"""DecisionFactory — multi-output-type by Persona (spec §5 lines 1011-1023).

PLANNER → WorkflowRecommendation (full strategy)
CAA     → RootCauseHypotheses (investigation directive — exploration result)
COPILOT → ParameterRecommendation (concrete parameter advice)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from cognitiveplane.shared.dto.context import ContextSnapshot
from cognitiveplane.shared.dto_decision import BrainDecision, DecisionOutput
from cognitiveplane.shared.dto_decision.outputs import (
    InspectionStrategy,
    MarginalRange,
    ParameterRecommendation,
    ParameterSet,
    ROIEstimate,
    RootCauseHypothesis,
    WorkflowRecommendation,
)
from cognitiveplane.shared.dto_decision.assessment import RootCauseHypotheses
from cognitiveplane.shared.enums import (
    BrainStateType,
    DecisionPointType,
    InspectionStrategyType,
    PersonaType,
    ReasoningMode,
    WorkflowType,
)
from cognitiveplane.shared.types import CaseId, DecisionId


class DecisionFactory:
    """Build BrainDecision with persona-appropriate output content."""

    @staticmethod
    def create(
        persona: PersonaType,
        mode: ReasoningMode,
        result: Any,
        memory: list[Any] | None,
        context: ContextSnapshot,
        *,
        decision_point: DecisionPointType = DecisionPointType.DP0,
        state: BrainStateType = BrainStateType.VALIDATION,
    ) -> BrainDecision:
        confidence = DecisionFactory._extract_confidence(result, mode)
        rationale = DecisionFactory._extract_rationale(result, context)
        params = DecisionFactory._extract_params(result, memory, context)

        if persona == PersonaType.PLANNER:
            content = DecisionFactory._workflow_recommendation(
                params, rationale, confidence
            )
        elif persona == PersonaType.CAA:
            content = DecisionFactory._investigation_directive(
                result, rationale, confidence
            )
        else:
            content = DecisionFactory._parameter_recommendation(
                params, rationale, confidence
            )

        return BrainDecision(
            decision_id=DecisionId(value=uuid4()),
            case_id=context.case_id,
            trigger_event_type=context.event_type,
            decision_point=decision_point,
            persona=persona,
            reasoning_mode=mode,
            state=state,
            outputs=[DecisionOutput(content=content, confidence=confidence)],
            confidence=confidence,
            created_at=datetime.now(timezone.utc),
        )

    # ---------------- helpers ----------------

    @staticmethod
    def _extract_confidence(result: Any, mode: ReasoningMode) -> float:
        if result is not None:
            c = float(getattr(result, "confidence", 0.0) or 0.0)
            if c > 0:
                return max(0.0, min(1.0, c))
        return {
            ReasoningMode.ROUTINE: 0.9,
            ReasoningMode.ADAPTIVE: 0.75,
            ReasoningMode.EXPLORATORY: 0.6,
        }.get(mode, 0.75)

    @staticmethod
    def _extract_rationale(result: Any, context: ContextSnapshot) -> str:
        if result is not None:
            trace = getattr(result, "reasoning_trace", None)
            if trace:
                return str(trace)[:500]
            conclusions = getattr(result, "conclusions", None)
            if conclusions:
                first = conclusions[0]
                stmt = getattr(first, "statement", str(first))
                return str(stmt)[:500]
        return f"Decision for case {context.case_id.value}"

    @staticmethod
    def _extract_params(
        result: Any, memory: list[Any] | None, context: ContextSnapshot
    ) -> dict[str, str]:
        params: dict[str, str] = {"case_id": str(context.case_id.value)}
        if result is not None:
            plan_obj = getattr(result, "plan", None)
            if plan_obj is not None:
                steps = getattr(plan_obj, "steps", None) or []
                for i, step in enumerate(steps[:5], 1):
                    params[f"step_{i}"] = str(step)
                outcome = getattr(plan_obj, "expected_outcome", None)
                if outcome:
                    params["expected_outcome"] = str(outcome)
        if memory:
            for i, m in enumerate(memory[:3], 1):
                content = getattr(m, "content", None)
                if content:
                    params[f"memory_match_{i}"] = str(content)[:200]
        return params

    @staticmethod
    def _parameter_recommendation(
        params: dict[str, str], rationale: str, confidence: float
    ) -> ParameterRecommendation:
        return ParameterRecommendation(
            parameters=ParameterSet(parameters=params),
            rationale=rationale or "Copilot parameter recommendation",
            confidence=confidence,
            constraints_applied=[],
        )

    @staticmethod
    def _workflow_recommendation(
        params: dict[str, str], rationale: str, confidence: float
    ) -> WorkflowRecommendation:
        return WorkflowRecommendation(
            workflow_type=WorkflowType.FULL,
            confidence=confidence,
            rationale=rationale or "Planner workflow recommendation",
            inspection_strategy=InspectionStrategy(
                strategy_type=InspectionStrategyType.STANDARD,
                coverage_areas=[],
            ),
            parameters=ParameterSet(parameters=params),
            roi_estimate=ROIEstimate(
                estimated_roi=0.0,
                cost_projection={},
                benefit_projection={},
                assumptions=[],
            ),
            marginal_range=MarginalRange(
                parameter="placeholder",
                lower_bound="0",
                upper_bound="1",
                unit="",
            ),
            risk_flags=[],
        )

    @staticmethod
    def _investigation_directive(
        result: Any, rationale: str, confidence: float
    ) -> RootCauseHypotheses:
        hypotheses: list[RootCauseHypothesis] = []
        if result is not None:
            trace = getattr(result, "reasoning_trace", None) or rationale
            hypotheses = [
                RootCauseHypothesis(
                    hypothesis=str(trace)[:200] or "Unknown root cause",
                    likelihood=confidence,
                    supporting_evidence=[],
                    investigation_suggestions=[],
                )
            ]
        if not hypotheses:
            hypotheses = [
                RootCauseHypothesis(
                    hypothesis=rationale or "Unknown root cause",
                    likelihood=confidence,
                    supporting_evidence=[],
                    investigation_suggestions=[],
                )
            ]
        return RootCauseHypotheses(
            hypotheses=hypotheses,
            ranked_by_likelihood=True,
            confidence=confidence,
        )
