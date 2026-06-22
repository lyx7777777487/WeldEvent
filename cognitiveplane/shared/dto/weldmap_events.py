"""WeldMap domain events (spec 17.7 lines 2585-2625).

Event Sourcing -- every WeldMap state change emits a typed domain event.
Phase 1 = in-memory event log + materialized views.
Phase 2 = NATS JetStream subjects for cross-plane fan-out.

Five canonical event types are defined here; additional workflow / image /
mask / annotation / rendering / SPC events live in their own modules but
inherit from `WeldMapDomainEvent`.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from cognitiveplane.shared.dto_decision import BrainDecision
from cognitiveplane.shared.dto.gateway import Escalation
from cognitiveplane.shared.types import CaseId, DecisionId


class WeldMapEventType(str, Enum):
    """Canonical WeldMap domain events (spec 17.7 lines 2586-2592).

    These five are written by the Cognitive Plane.
    Workflow / image / mask / annotation / rendering / SPC events are written
    by the Execution Plane and consumed read-only by the Cognitive Plane.
    """

    DECISION_MADE = "decision_made"
    DECISION_OVERRIDDEN = "decision_overridden"
    ESCALATION_RAISED = "escalation_raised"
    WORKFLOW_TRIGGERED = "workflow_triggered"
    HUMAN_FEEDBACK_RECEIVED = "human_feedback_received"


class WeldMapDomainEvent(BaseModel):
    """Append-only base event written into the WeldMap event log."""

    event_id: UUID = Field(default_factory=uuid4)
    event_type: WeldMapEventType
    case_id: CaseId
    timestamp: datetime
    actor: str = Field(min_length=1)         # service-name / operator-id
    correlation_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Typed payload helpers (constructors that fill `payload` correctly)
# ---------------------------------------------------------------------------


def decision_made_event(
    decision: BrainDecision, actor: str = "cognitive-plane"
) -> WeldMapDomainEvent:
    return WeldMapDomainEvent(
        event_type=WeldMapEventType.DECISION_MADE,
        case_id=decision.case_id,
        timestamp=decision.created_at,
        actor=actor,
        payload={
            "decision_id": str(decision.decision_id.value),
            "persona": decision.persona.value,
            "reasoning_mode": decision.reasoning_mode.value,
            "decision_point": decision.decision_point.value,
            "confidence": decision.confidence,
        },
    )


def decision_overridden_event(
    decision_id: DecisionId,
    case_id: CaseId,
    operator_id: str,
    timestamp: datetime,
    rationale: str,
) -> WeldMapDomainEvent:
    return WeldMapDomainEvent(
        event_type=WeldMapEventType.DECISION_OVERRIDDEN,
        case_id=case_id,
        timestamp=timestamp,
        actor=operator_id,
        payload={
            "decision_id": str(decision_id.value),
            "rationale": rationale,
        },
    )


def escalation_raised_event(
    escalation: Escalation, actor: str = "cognitive-plane"
) -> WeldMapDomainEvent:
    return WeldMapDomainEvent(
        event_type=WeldMapEventType.ESCALATION_RAISED,
        case_id=escalation.case_id,
        timestamp=escalation.created_at,
        actor=actor,
        payload={
            "escalation_id": str(escalation.escalation_id),
            "decision_id": str(escalation.decision_id.value),
            "urgency": escalation.urgency.value,
            "fallback_mode": escalation.fallback_mode.value,
            "reason": escalation.reason,
        },
    )


def workflow_triggered_event(
    case_id: CaseId,
    workflow_config: dict[str, Any],
    timestamp: datetime,
    actor: str = "cognitive-plane",
) -> WeldMapDomainEvent:
    return WeldMapDomainEvent(
        event_type=WeldMapEventType.WORKFLOW_TRIGGERED,
        case_id=case_id,
        timestamp=timestamp,
        actor=actor,
        payload={"workflow_config": workflow_config},
    )


def human_feedback_received_event(
    case_id: CaseId,
    decision_id: DecisionId,
    operator_id: str,
    feedback_type: str,
    feedback_text: str,
    rating: int,
    timestamp: datetime,
) -> WeldMapDomainEvent:
    return WeldMapDomainEvent(
        event_type=WeldMapEventType.HUMAN_FEEDBACK_RECEIVED,
        case_id=case_id,
        timestamp=timestamp,
        actor=operator_id,
        payload={
            "decision_id": str(decision_id.value),
            "feedback_type": feedback_type,
            "feedback_text": feedback_text,
            "rating": rating,
        },
    )


__all__ = [
    "WeldMapEventType",
    "WeldMapDomainEvent",
    "decision_made_event",
    "decision_overridden_event",
    "escalation_raised_event",
    "workflow_triggered_event",
    "human_feedback_received_event",
]
