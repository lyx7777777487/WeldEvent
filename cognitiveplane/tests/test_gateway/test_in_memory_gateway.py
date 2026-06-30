"""Tests for InMemoryGatewayAdapter.

Covers: publish_decision, publish_explanation, publish_parameter_patch,
publish_risk_alert, publish_escalation, read_workflow_state (empty),
subscribe + enqueue + dequeue_event, get_health.
"""

import asyncio

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from cognitiveplane.shared.dto.context import DomainEvent
from cognitiveplane.shared.dto_decision import BrainDecision, DecisionOutput
from cognitiveplane.shared.dto.gateway import (
    Escalation,
    Explanation,
    ParameterPatch,
    RiskAlert,
    WorkflowState,
)
from cognitiveplane.shared.enums import (
    AggregatedValidationResult,
    BrainStateType,
    DecisionPointType,
    EventType,
    FallbackMode,
    PersonaType,
    ReasoningMode,
    RiskLevel,
    UrgencyLevel,
    AudienceType,
)
from cognitiveplane.shared.ports.gateway import EventSubscriptionConfig
from cognitiveplane.shared.types import CaseId, DecisionId
from cognitiveplane.gateway.adapters.in_memory import InMemoryGatewayAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_case_id() -> CaseId:
    return CaseId(value="CASE-001")


def _make_decision_id() -> DecisionId:
    return DecisionId(value=uuid4())


def _make_brain_decision(
    case_id: CaseId | None = None,
    decision_id: DecisionId | None = None,
) -> BrainDecision:
    return BrainDecision(
        decision_id=decision_id or _make_decision_id(),
        case_id=case_id or _make_case_id(),
        trigger_event_type=EventType.IQA_COMPLETED,
        decision_point=DecisionPointType.DP1,
        persona=PersonaType.COPILOT,
        reasoning_mode=ReasoningMode.ROUTINE,
        state=BrainStateType.DECISION_GENERATION,
        outputs=[],
        confidence=0.85,
        created_at=datetime.now(timezone.utc),
    )


def _make_explanation(decision_id: DecisionId | None = None) -> Explanation:
    return Explanation(
        explanation_id=uuid4(),
        decision_id=decision_id or _make_decision_id(),
        audience=AudienceType.OPERATOR,
        explanation_text="Test explanation",
        key_factors=["factor_a"],
        confidence_justification="High confidence",
        created_at=datetime.now(timezone.utc),
    )


def _make_parameter_patch(
    case_id: CaseId | None = None,
    decision_id: DecisionId | None = None,
) -> ParameterPatch:
    from cognitiveplane.shared.dto_decision.outputs import ParameterAdjustment

    return ParameterPatch(
        patch_id=uuid4(),
        decision_id=decision_id or _make_decision_id(),
        case_id=case_id or _make_case_id(),
        parameter_adjustments=[
            ParameterAdjustment(
                parameter_name="heat_input",
                current_value="advice.md.5",
                proposed_value="advice.md.8",
                unit="kJ/mm",
            )
        ],
        confidence=0.9,
        rationale="Within acceptable range",
        created_at=datetime.now(timezone.utc),
    )


def _make_risk_alert(
    case_id: CaseId | None = None,
    decision_id: DecisionId | None = None,
) -> RiskAlert:
    return RiskAlert(
        alert_id=uuid4(),
        decision_id=decision_id or _make_decision_id(),
        case_id=case_id or _make_case_id(),
        risk_level=RiskLevel.HIGH,
        risk_factors=["exceeds_tolerance"],
        mitigation_suggestions=["reduce_heat_input"],
        confidence=0.75,
        created_at=datetime.now(timezone.utc),
    )


def _make_escalation(
    case_id: CaseId | None = None,
    decision_id: DecisionId | None = None,
) -> Escalation:
    from cognitiveplane.shared.dto_decision.outputs import EvidenceReference

    return Escalation(
        escalation_id=uuid4(),
        decision_id=decision_id or _make_decision_id(),
        case_id=case_id or _make_case_id(),
        reason="Confidence below threshold",
        urgency=UrgencyLevel.URGENT,
        fallback_mode=FallbackMode.HUMAN_INTERVENTION,
        supporting_evidence=[
            EvidenceReference(
                source="validation", reference_id="ref-advice.md", description="Low confidence"
            )
        ],
        created_at=datetime.now(timezone.utc),
    )


def _make_domain_event(case_id: CaseId | None = None) -> DomainEvent:
    from cognitiveplane.shared.types import EventId

    return DomainEvent(
        event_id=EventId(value=uuid4()),
        event_type=EventType.IQA_COMPLETED,
        case_id=case_id or _make_case_id(),
        timestamp=datetime.now(timezone.utc),
        source="test",
        payload={"key": "value"},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def gateway() -> InMemoryGatewayAdapter:
    return InMemoryGatewayAdapter()


# ===================================================================
# Test cases
# ===================================================================


class TestPublishDecision:
    """publish_decision() returns success and stores in _brain_writes."""

    @pytest.mark.asyncio
    async def test_publish_decision_returns_success(
        self, gateway: InMemoryGatewayAdapter
    ):
        decision = _make_brain_decision()
        result = await gateway.publish_decision(decision)
        assert result.success is True
        assert f"/decisions/{decision.decision_id.value}" in gateway._brain_writes


class TestPublishExplanation:
    """publish_explanation() returns success."""

    @pytest.mark.asyncio
    async def test_publish_explanation_returns_success(
        self, gateway: InMemoryGatewayAdapter
    ):
        explanation = _make_explanation()
        result = await gateway.publish_explanation(explanation)
        assert result.success is True


class TestPublishParameterPatch:
    """publish_parameter_patch() returns success."""

    @pytest.mark.asyncio
    async def test_publish_parameter_patch_returns_success(
        self, gateway: InMemoryGatewayAdapter
    ):
        patch = _make_parameter_patch()
        result = await gateway.publish_parameter_patch(patch)
        assert result.success is True


class TestPublishRiskAlert:
    """publish_risk_alert() returns success."""

    @pytest.mark.asyncio
    async def test_publish_risk_alert_returns_success(
        self, gateway: InMemoryGatewayAdapter
    ):
        alert = _make_risk_alert()
        result = await gateway.publish_risk_alert(alert)
        assert result.success is True


class TestPublishEscalation:
    """publish_escalation() returns success."""

    @pytest.mark.asyncio
    async def test_publish_escalation_returns_success(
        self, gateway: InMemoryGatewayAdapter
    ):
        escalation = _make_escalation()
        result = await gateway.publish_escalation(escalation)
        assert result.success is True


class TestReadWorkflowState:
    """read_workflow_state() returns empty WorkflowState when no data loaded."""

    @pytest.mark.asyncio
    async def test_read_workflow_state_returns_empty(
        self, gateway: InMemoryGatewayAdapter
    ):
        case_id = _make_case_id()
        state = await gateway.read_workflow_state(case_id)
        assert isinstance(state, WorkflowState)
        assert state.case_id == case_id
        assert state.status == ""
        assert state.parameters == {}


class TestEventSubscription:
    """subscribe + enqueue + dequeue_event returns the event."""

    @pytest.mark.asyncio
    async def test_subscribe_and_dequeue_event(
        self, gateway: InMemoryGatewayAdapter
    ):
        config = EventSubscriptionConfig(paths=["/events/*"])
        await gateway.subscribe(config)

        event = _make_domain_event()
        await gateway._event_queue.put(event)

        dequeued = await gateway.dequeue_event()
        assert dequeued is not None
        assert dequeued.event_id == event.event_id


class TestGetHealth:
    """get_health() reports subscription status."""

    @pytest.mark.asyncio
    async def test_get_health_reports_unsubscribed(
        self, gateway: InMemoryGatewayAdapter
    ):
        health = await gateway.get_health()
        assert health.subscription_active is False

    @pytest.mark.asyncio
    async def test_get_health_reports_subscribed(
        self, gateway: InMemoryGatewayAdapter
    ):
        config = EventSubscriptionConfig(paths=["/events/*"])
        await gateway.subscribe(config)
        health = await gateway.get_health()
        assert health.subscription_active is True
