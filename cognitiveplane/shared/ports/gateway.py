"""L1 Cognitive Plane — Gateway port ABCs and Input/Output models.

Source: L1_Port_and_Contract_Design.md (Phase 4, Sections 10.advice.md–10.4).
"""

from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import BaseModel, Field

from cognitiveplane.shared.dto.context import DomainEvent, WeldMapSnapshot
from cognitiveplane.shared.dto_decision import BrainDecision
from cognitiveplane.shared.dto.gateway import (
    AuditEntry,
    CaseData,
    Escalation,
    Explanation,
    Measurement,
    ParameterPatch,
    PromotionRequest,
    PublishResult,
    RecheckRequest,
    ConsensusRequest,
    RiskAlert,
    WorkflowState,
)
from cognitiveplane.shared.dto.collaboration import FeedbackContent
from cognitiveplane.shared.types import CaseId


# ---------------------------------------------------------------------------
# 10.advice.md GatewayReadPort
# ---------------------------------------------------------------------------


class GatewayReadPort(ABC):

    @abstractmethod
    async def read_workflow_state(
        self, case_id: CaseId
    ) -> WorkflowState: ...

    @abstractmethod
    async def read_case(
        self, case_id: CaseId
    ) -> CaseData: ...

    @abstractmethod
    async def read_measurements(
        self, case_id: CaseId, parameter_filter: list[str] | None = None
    ) -> list[Measurement]: ...

    @abstractmethod
    async def read_events(
        self, case_id: CaseId, since: datetime | None = None
    ) -> list[DomainEvent]: ...

    @abstractmethod
    async def read_audit_trail(
        self, case_id: CaseId
    ) -> list[AuditEntry]: ...

    @abstractmethod
    async def read_weldmap_snapshot(
        self, case_id: CaseId
    ) -> WeldMapSnapshot: ...


# ---------------------------------------------------------------------------
# 10.2 GatewayWritePort
# ---------------------------------------------------------------------------


class GatewayWritePort(ABC):

    @abstractmethod
    async def publish_decision(
        self, decision: BrainDecision
    ) -> PublishResult: ...

    @abstractmethod
    async def publish_explanation(
        self, explanation: Explanation
    ) -> PublishResult: ...

    @abstractmethod
    async def publish_parameter_patch(
        self, patch: ParameterPatch
    ) -> PublishResult: ...

    @abstractmethod
    async def publish_recheck_request(
        self, request: RecheckRequest
    ) -> PublishResult: ...

    @abstractmethod
    async def publish_consensus_request(
        self, request: ConsensusRequest
    ) -> PublishResult: ...

    @abstractmethod
    async def publish_risk_alert(
        self, alert: RiskAlert
    ) -> PublishResult: ...

    @abstractmethod
    async def publish_escalation(
        self, escalation: Escalation
    ) -> PublishResult: ...

    @abstractmethod
    async def publish_feedback(
        self, feedback: FeedbackContent
    ) -> PublishResult: ...

    @abstractmethod
    async def publish_memory_promotion_request(
        self, request: PromotionRequest
    ) -> PublishResult: ...


# ---------------------------------------------------------------------------
# 10.3 GatewayEventSubscriptionPort
# ---------------------------------------------------------------------------


class EventSubscriptionConfig(BaseModel):
    paths: list[str]
    buffer_size: int = Field(default=1000, ge=1, le=10000)
    backpressure_policy: str = Field(default="drop_oldest")


class SubscriptionStatus(BaseModel):
    active: bool
    subscribed_paths: list[str]
    buffer_depth: int
    last_event_timestamp: datetime | None = None


class GatewayEventSubscriptionPort(ABC):

    @abstractmethod
    async def subscribe(
        self, config: EventSubscriptionConfig
    ) -> None: ...

    @abstractmethod
    async def unsubscribe(self) -> None: ...

    @abstractmethod
    async def dequeue_event(self) -> DomainEvent | None: ...

    @abstractmethod
    async def get_subscription_status(self) -> SubscriptionStatus: ...


# ---------------------------------------------------------------------------
# 10.4 GatewayHealthPort
# ---------------------------------------------------------------------------


class GatewayHealthStatus(BaseModel):
    weldmap_connected: bool
    subscription_active: bool
    event_buffer_depth: int
    last_successful_write: datetime | None = None
    last_successful_read: datetime | None = None


class GatewayHealthPort(ABC):

    @abstractmethod
    async def get_health(self) -> GatewayHealthStatus: ...
