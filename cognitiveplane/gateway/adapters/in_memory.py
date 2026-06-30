"""In-memory implementation of all Gateway port interfaces.

Source: L1_Port_and_Contract_Design.md (Phase 4, Sections 10.advice.md–10.4).
"""

import asyncio
from datetime import datetime, timezone
from typing import Any

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
from cognitiveplane.shared.ports.gateway import (
    EventSubscriptionConfig,
    GatewayEventSubscriptionPort,
    GatewayHealthPort,
    GatewayHealthStatus,
    GatewayReadPort,
    GatewayWritePort,
    SubscriptionStatus,
)
from cognitiveplane.shared.types import CaseId
from cognitiveplane.gateway.ports import CognitiveGatewayWritePort


class InMemoryGatewayAdapter(
    GatewayReadPort,
    GatewayWritePort,
    GatewayEventSubscriptionPort,
    GatewayHealthPort,
    CognitiveGatewayWritePort,
):
    """In-memory gateway adapter for testing and development.

    Stores all published payloads in ``_brain_writes`` keyed by a WeldMap path
    derived from the payload type.  Read methods return empty data unless
    populated externally (e.g. by a test harness).
    """

    def __init__(self) -> None:
        self._workflow_states: dict[str, WorkflowState] = {}
        self._cases: dict[str, CaseData] = {}
        self._measurements: dict[str, list[Measurement]] = {}
        self._events: dict[str, list[DomainEvent]] = {}
        self._audit: dict[str, list[AuditEntry]] = {}
        self._brain_writes: dict[str, Any] = {}
        self._event_queue: asyncio.Queue[DomainEvent] = asyncio.Queue()
        self._subscribed: bool = False
        self._subscribed_paths: list[str] = []
        self._last_write: datetime | None = None
        self._last_read: datetime | None = None

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Reset all internal storage. Required for test isolation."""
        self._workflow_states.clear()
        self._cases.clear()
        self._measurements.clear()
        self._events.clear()
        self._audit.clear()
        self._brain_writes.clear()
        self._event_queue = asyncio.Queue()
        self._subscribed = False
        self._subscribed_paths = []
        self._last_write = None
        self._last_read = None

    # ------------------------------------------------------------------
    # 10.advice.md GatewayReadPort
    # ------------------------------------------------------------------

    async def read_workflow_state(self, case_id: CaseId) -> WorkflowState:
        self._last_read = datetime.now(timezone.utc)
        key = case_id.value
        if key in self._workflow_states:
            return self._workflow_states[key]
        return WorkflowState(
            case_id=case_id,
            status="",
            parameters={},
            updated_at=datetime.now(timezone.utc),
        )

    async def read_case(self, case_id: CaseId) -> CaseData:
        self._last_read = datetime.now(timezone.utc)
        key = case_id.value
        if key in self._cases:
            return self._cases[key]
        return CaseData(
            case_id=case_id,
            case_type="",
            creation_date=datetime.now(timezone.utc),
            status="",
            metadata={},
        )

    async def read_measurements(
        self, case_id: CaseId, parameter_filter: list[str] | None = None
    ) -> list[Measurement]:
        self._last_read = datetime.now(timezone.utc)
        key = case_id.value
        measurements = self._measurements.get(key, [])
        if parameter_filter is not None:
            measurements = [
                m for m in measurements if m.parameter in parameter_filter
            ]
        return measurements

    async def read_events(
        self, case_id: CaseId, since: datetime | None = None
    ) -> list[DomainEvent]:
        self._last_read = datetime.now(timezone.utc)
        key = case_id.value
        events = self._events.get(key, [])
        if since is not None:
            events = [e for e in events if e.timestamp > since]
        return events

    async def read_audit_trail(self, case_id: CaseId) -> list[AuditEntry]:
        self._last_read = datetime.now(timezone.utc)
        key = case_id.value
        return self._audit.get(key, [])

    async def read_weldmap_snapshot(self, case_id: CaseId) -> WeldMapSnapshot:
        self._last_read = datetime.now(timezone.utc)
        key = case_id.value

        # Assemble workflow_state from stored WorkflowState (if any)
        ws = self._workflow_states.get(key)
        workflow_state = ws.parameters if ws else {}

        # Collect decisions published for this case (as dicts per WeldMapSnapshot schema)
        decisions = [
            v.model_dump(mode="json") if hasattr(v, "model_dump") else v
            for v in self._brain_writes.values()
            if isinstance(v, BrainDecision) and v.case_id == case_id
        ]

        return WeldMapSnapshot(
            case_id=case_id,
            workflow_state=workflow_state,
            measurements=self._measurements.get(key, []),
            decisions=decisions,
            events=self._events.get(key, []),
            snapshot_at=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # 10.2 GatewayWritePort
    # ------------------------------------------------------------------

    async def publish_decision(self, decision: BrainDecision) -> PublishResult:
        now = datetime.now(timezone.utc)
        path = f"/decisions/{decision.decision_id.value}"
        self._brain_writes[path] = decision
        self._last_write = now
        return PublishResult(success=True, weldmap_path=path, timestamp=now)

    async def publish_explanation(self, explanation: Explanation) -> PublishResult:
        now = datetime.now(timezone.utc)
        path = f"/explanations/{explanation.explanation_id}"
        self._brain_writes[path] = explanation
        self._last_write = now
        return PublishResult(success=True, weldmap_path=path, timestamp=now)

    async def publish_parameter_patch(self, patch: ParameterPatch) -> PublishResult:
        now = datetime.now(timezone.utc)
        path = f"/patches/{patch.patch_id}"
        self._brain_writes[path] = patch
        self._last_write = now
        return PublishResult(success=True, weldmap_path=path, timestamp=now)

    async def publish_recheck_request(
        self, request: RecheckRequest
    ) -> PublishResult:
        now = datetime.now(timezone.utc)
        path = f"/recheck_requests/{request.recheck_id}"
        self._brain_writes[path] = request
        self._last_write = now
        return PublishResult(success=True, weldmap_path=path, timestamp=now)

    async def publish_consensus_request(
        self, request: ConsensusRequest
    ) -> PublishResult:
        now = datetime.now(timezone.utc)
        path = f"/consensus_requests/{request.consensus_id}"
        self._brain_writes[path] = request
        self._last_write = now
        return PublishResult(success=True, weldmap_path=path, timestamp=now)

    async def publish_risk_alert(self, alert: RiskAlert) -> PublishResult:
        now = datetime.now(timezone.utc)
        path = f"/risk_alerts/{alert.alert_id}"
        self._brain_writes[path] = alert
        self._last_write = now
        return PublishResult(success=True, weldmap_path=path, timestamp=now)

    async def publish_escalation(self, escalation: Escalation) -> PublishResult:
        now = datetime.now(timezone.utc)
        path = f"/escalations/{escalation.escalation_id}"
        self._brain_writes[path] = escalation
        self._last_write = now
        return PublishResult(success=True, weldmap_path=path, timestamp=now)

    async def publish_feedback(self, feedback: FeedbackContent) -> PublishResult:
        now = datetime.now(timezone.utc)
        path = f"/feedback/{feedback.decision_id.value}"
        self._brain_writes[path] = feedback
        self._last_write = now
        return PublishResult(success=True, weldmap_path=path, timestamp=now)

    async def publish_memory_promotion_request(
        self, request: PromotionRequest
    ) -> PublishResult:
        now = datetime.now(timezone.utc)
        path = f"/promotion_requests/{request.memory_id.value}"
        self._brain_writes[path] = request
        self._last_write = now
        return PublishResult(success=True, weldmap_path=path, timestamp=now)

    # ------------------------------------------------------------------
    # CognitiveGatewayWritePort — P1-2 fix: 补齐 notify_workflow_trigger
    # 和 publish_instruction，让 InMemoryGatewayAdapter 真正实现该接口
    # ------------------------------------------------------------------

    async def notify_workflow_trigger(
        self, case_id: CaseId, workflow_config: dict
    ) -> None:
        """P1-2 fix: 写 WeldMap workflow domain 记录 workflow 触发事件。

        EventConnector 提交 Temporal 前调此方法（§6.2 契约 2）。
        """
        now = datetime.now(timezone.utc)
        path = f"/workflow_triggers/{case_id.value}/{workflow_config.get('workflow_id', 'unknown')}"
        self._brain_writes[path] = {
            "case_id": case_id.value,
            "workflow_config": workflow_config,
            "triggered_at": now,
        }
        self._last_write = now

    async def publish_instruction(self, instruction) -> PublishResult:
        """CognitiveGatewayWritePort.publish_instruction 实现。"""
        now = datetime.now(timezone.utc)
        instr_id = getattr(instruction, "instruction_id", "unknown")
        path = f"/instructions/{instr_id}"
        self._brain_writes[path] = instruction
        self._last_write = now
        return PublishResult(success=True, weldmap_path=path, timestamp=now)

    # ------------------------------------------------------------------
    # 10.3 GatewayEventSubscriptionPort
    # ------------------------------------------------------------------

    async def subscribe(self, config: EventSubscriptionConfig) -> None:
        self._subscribed = True
        self._subscribed_paths = list(config.paths)

    async def unsubscribe(self) -> None:
        self._subscribed = False
        self._subscribed_paths = []

    async def dequeue_event(self) -> DomainEvent | None:
        if self._event_queue.empty():
            return None
        return self._event_queue.get_nowait()

    async def get_subscription_status(self) -> SubscriptionStatus:
        last_ts: datetime | None = None
        # Peek at the last event without removing it
        if not self._event_queue.empty():
            # We cannot easily peek at the last item in a Queue,
            # so we leave last_event_timestamp as None for now.
            pass
        return SubscriptionStatus(
            active=self._subscribed,
            subscribed_paths=self._subscribed_paths,
            buffer_depth=self._event_queue.qsize(),
            last_event_timestamp=last_ts,
        )

    # ------------------------------------------------------------------
    # 10.4 GatewayHealthPort
    # ------------------------------------------------------------------

    async def get_health(self) -> GatewayHealthStatus:
        return GatewayHealthStatus(
            weldmap_connected=True,
            subscription_active=self._subscribed,
            event_buffer_depth=self._event_queue.qsize(),
            last_successful_write=self._last_write,
            last_successful_read=self._last_read,
        )
