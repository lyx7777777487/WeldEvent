"""Tests for control/persona.py, control/reasoning_mode.py, control/decision_factory.py."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from cognitiveplane.control.decision_factory import DecisionFactory
from cognitiveplane.control.persona import PersonaSelector
from cognitiveplane.control.reasoning_mode import ReasoningModeSelector
from cognitiveplane.shared.dto_context import ContextSnapshot
from cognitiveplane.shared.dto_decision.assessment import RootCauseHypotheses
from cognitiveplane.shared.dto_decision.outputs import (
    ParameterRecommendation,
    WorkflowRecommendation,
)
from cognitiveplane.shared.enums import (
    EventType,
    NoveltyLevel,
    PersonaType,
    ReasoningMode,
)
from cognitiveplane.shared.types import CaseId


def _ctx(novelty: NoveltyLevel = NoveltyLevel.KNOWN, critical: int = 0) -> ContextSnapshot:
    return ContextSnapshot(
        case_id=CaseId(value="case-1"),
        event_type=EventType.WORKFLOW_ENTERED,
        workflow_state={},
        case_data={},
        measurements=[],
        memory_match_confidence=0.0,
        knowledge_coverage=0.0,
        event_novelty=novelty,
        validation_critical_count=critical,
        timestamp=datetime.now(timezone.utc),
    )


class TestPersonaSelector:
    def test_unknown_returns_caa(self):
        assert PersonaSelector.select(_ctx(NoveltyLevel.UNKNOWN)) == PersonaType.CAA

    def test_critical_count_returns_caa(self):
        assert PersonaSelector.select(_ctx(NoveltyLevel.KNOWN, critical=1)) == PersonaType.CAA

    def test_partial_returns_planner(self):
        assert PersonaSelector.select(_ctx(NoveltyLevel.PARTIAL)) == PersonaType.PLANNER

    def test_known_returns_copilot(self):
        assert PersonaSelector.select(_ctx(NoveltyLevel.KNOWN)) == PersonaType.COPILOT


class TestReasoningModeSelector:
    def test_known_routine(self):
        assert ReasoningModeSelector.select(_ctx(NoveltyLevel.KNOWN)) == ReasoningMode.ROUTINE

    def test_partial_adaptive(self):
        assert ReasoningModeSelector.select(_ctx(NoveltyLevel.PARTIAL)) == ReasoningMode.ADAPTIVE

    def test_unknown_exploratory(self):
        assert ReasoningModeSelector.select(_ctx(NoveltyLevel.UNKNOWN)) == ReasoningMode.EXPLORATORY


class TestDecisionFactory:
    def test_copilot_returns_parameter_recommendation(self):
        ctx = _ctx(NoveltyLevel.KNOWN)
        d = DecisionFactory.create(
            persona=PersonaType.COPILOT,
            mode=ReasoningMode.ROUTINE,
            result=None,
            memory=None,
            context=ctx,
        )
        assert isinstance(d.outputs[0].content, ParameterRecommendation)
        assert d.persona == PersonaType.COPILOT
        assert d.confidence > 0
        assert d.case_id == ctx.case_id

    def test_planner_returns_workflow_recommendation(self):
        ctx = _ctx(NoveltyLevel.PARTIAL)
        d = DecisionFactory.create(
            persona=PersonaType.PLANNER,
            mode=ReasoningMode.ADAPTIVE,
            result=None,
            memory=None,
            context=ctx,
        )
        assert isinstance(d.outputs[0].content, WorkflowRecommendation)

    def test_caa_returns_root_cause_hypotheses(self):
        ctx = _ctx(NoveltyLevel.UNKNOWN)
        d = DecisionFactory.create(
            persona=PersonaType.CAA,
            mode=ReasoningMode.EXPLORATORY,
            result=None,
            memory=None,
            context=ctx,
        )
        assert isinstance(d.outputs[0].content, RootCauseHypotheses)
        assert len(d.outputs[0].content.hypotheses) >= 1

    def test_default_confidence_by_mode(self):
        ctx = _ctx()
        routine = DecisionFactory.create(
            PersonaType.COPILOT, ReasoningMode.ROUTINE, None, None, ctx
        )
        adaptive = DecisionFactory.create(
            PersonaType.COPILOT, ReasoningMode.ADAPTIVE, None, None, ctx
        )
        exploratory = DecisionFactory.create(
            PersonaType.COPILOT, ReasoningMode.EXPLORATORY, None, None, ctx
        )
        assert routine.confidence > adaptive.confidence > exploratory.confidence
