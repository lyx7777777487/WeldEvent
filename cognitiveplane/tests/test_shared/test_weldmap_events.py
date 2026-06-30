"""Tests for shared/dto_weldmap_events.py — WeldMapEventType + helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from cognitiveplane.shared.dto_decision import BrainDecision, DecisionOutput
from cognitiveplane.shared.dto_decision.outputs import (
    EvidenceReference,
    ParameterRecommendation,
    ParameterSet,
)
from cognitiveplane.shared.dto.gateway import Escalation
from cognitiveplane.shared.dto.weldmap_events import (
    WeldMapDomainEvent,
    WeldMapEventType,
    decision_made_event,
    decision_overridden_event,
    escalation_raised_event,
    human_feedback_received_event,
    workflow_triggered_event,
)
from cognitiveplane.shared.enums import (
    BrainStateType,
    DecisionPointType,
    EventType,
    FallbackMode,
    PersonaType,
    ReasoningMode,
    UrgencyLevel,
)
from cognitiveplane.shared.types import CaseId, DecisionId


def _decision() -> BrainDecision:
    return BrainDecision(
        decision_id=DecisionId(value=uuid4()),
        case_id=CaseId(value="case-1"),
        trigger_event_type=EventType.WORKFLOW_ENTERED,
        decision_point=DecisionPointType.DP0,
        persona=PersonaType.COPILOT,
        reasoning_mode=ReasoningMode.ROUTINE,
        state=BrainStateType.VALIDATION,
        outputs=[
            DecisionOutput(
                content=ParameterRecommendation(
                    parameters=ParameterSet(parameters={"current": "120A"}),
                    rationale="ok",
                    confidence=0.85,
                    constraints_applied=[],
                ),
                confidence=0.85,
            )
        ],
        confidence=0.85,
        created_at=datetime.now(timezone.utc),
    )


def test_event_type_values():
    assert WeldMapEventType.DECISION_MADE.value == "decision_made"
    assert WeldMapEventType.WORKFLOW_TRIGGERED.value == "workflow_triggered"
    assert (
        WeldMapEventType.HUMAN_FEEDBACK_RECEIVED.value
        == "human_feedback_received"
    )


def test_decision_made_event():
    d = _decision()
    ev = decision_made_event(d, actor="brain-orchestrator")
    assert ev.event_type == WeldMapEventType.DECISION_MADE
    assert ev.case_id == d.case_id
    assert ev.actor == "brain-orchestrator"
    assert ev.payload["persona"] == "COPILOT"
    assert ev.payload["confidence"] == 0.85


def test_decision_overridden_event():
    d_id = DecisionId(value=uuid4())
    case = CaseId(value="case-X")
    ts = datetime.now(timezone.utc)
    ev = decision_overridden_event(
        decision_id=d_id,
        case_id=case,
        operator_id="alice",
        timestamp=ts,
        rationale="missed defect",
    )
    assert ev.event_type == WeldMapEventType.DECISION_OVERRIDDEN
    assert ev.actor == "alice"
    assert ev.payload["rationale"] == "missed defect"


def test_escalation_raised_event():
    e = Escalation(
        escalation_id=uuid4(),
        decision_id=DecisionId(value=uuid4()),
        case_id=CaseId(value="case-E"),
        reason="critical safety hit",
        urgency=UrgencyLevel.CRITICAL,
        fallback_mode=FallbackMode.HUMAN_INTERVENTION,
        supporting_evidence=[],
        created_at=datetime.now(timezone.utc),
    )
    ev = escalation_raised_event(e)
    assert ev.event_type == WeldMapEventType.ESCALATION_RAISED
    assert ev.payload["urgency"] == "CRITICAL"
    assert ev.payload["fallback_mode"] == "HUMAN_INTERVENTION"


def test_workflow_triggered_event():
    case = CaseId(value="case-W")
    ts = datetime.now(timezone.utc)
    ev = workflow_triggered_event(
        case_id=case,
        workflow_config={"type": "FULL"},
        timestamp=ts,
    )
    assert ev.event_type == WeldMapEventType.WORKFLOW_TRIGGERED
    assert ev.payload["workflow_config"] == {"type": "FULL"}


def test_human_feedback_event():
    ts = datetime.now(timezone.utc)
    case = CaseId(value="case-F")
    d_id = DecisionId(value=uuid4())
    ev = human_feedback_received_event(
        case_id=case,
        decision_id=d_id,
        operator_id="bob",
        feedback_type="rating",
        feedback_text="looks correct",
        rating=5,
        timestamp=ts,
    )
    assert ev.event_type == WeldMapEventType.HUMAN_FEEDBACK_RECEIVED
    assert ev.payload["rating"] == 5
    assert ev.actor == "bob"


def test_event_is_appendable_pydantic_model():
    d = _decision()
    ev: WeldMapDomainEvent = decision_made_event(d)
    # Round-trip through model_dump for log persistence
    dumped = ev.model_dump()
    assert dumped["event_type"] == "decision_made"
    rebuilt = WeldMapDomainEvent.model_validate(dumped)
    assert rebuilt.event_type == ev.event_type
