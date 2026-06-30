"""WeldMapWriteGateway — implements CognitiveGatewayWritePort.

Delegates to WeldMapHTTPClient for actual writes to WeldMap Store.
Phase 1e: emits GatewayActionEvent / GatewayObservationEvent pairs for
audit trail (dual-ID pairing: action_id back-pointer + tool_call_id grouping).
"""

from datetime import datetime, timezone
from uuid import uuid4

from cognitiveplane.gateway.events import GatewayActionEvent, GatewayObservationEvent
from cognitiveplane.gateway.ports import CognitiveGatewayWritePort
from cognitiveplane.gateway.weldmap_client import WeldMapHTTPClient
from cognitiveplane.shared.dto_decision import BrainDecision
from cognitiveplane.shared.dto.gateway import Escalation, PublishResult
from cognitiveplane.interaction.signals.instruction import LiveInstruction
from cognitiveplane.shared.types import CaseId


class WeldMapWriteGateway(CognitiveGatewayWritePort):
    """Write gateway delegating to WeldMapHTTPClient.

    Records every write as an Action↔Observation event pair.
    """

    def __init__(self, client: WeldMapHTTPClient) -> None:
        self._client = client
        self._action_events: list[GatewayActionEvent] = []
        self._observation_events: list[GatewayObservationEvent] = []

    @property
    def action_events(self) -> list[GatewayActionEvent]:
        return list(self._action_events)

    @property
    def observation_events(self) -> list[GatewayObservationEvent]:
        return list(self._observation_events)

    def find_paired_observation(
        self, action: GatewayActionEvent
    ) -> GatewayObservationEvent | None:
        """Find the observation paired with an action via action_id back-pointer."""
        for obs in self._observation_events:
            if obs.action_id == action.action_id:
                return obs
        return None

    async def _execute_write(
        self,
        action_type: str,
        domain: str,
        key: str,
        value: dict,
        tool_call_id: str | None = None,
    ) -> tuple[GatewayActionEvent, GatewayObservationEvent]:
        """Execute a WeldMap write as an Action↔Observation event pair."""
        action_id = str(uuid4())
        tci = tool_call_id or str(uuid4())

        action = GatewayActionEvent(
            action_id=action_id,
            tool_call_id=tci,
            action_type=action_type,
            domain=domain,
            payload={"key": key, "value": value},
            timestamp=datetime.now(timezone.utc),
        )
        self._action_events.append(action)

        success = True
        result: dict | None = None
        error: str | None = None
        try:
            client_result = await self._client.write(domain=domain, key=key, value=value)
            result = client_result if isinstance(client_result, dict) else {"raw": str(client_result)}
        except Exception as e:
            success = False
            error = str(e)

        observation = GatewayObservationEvent(
            observation_id=str(uuid4()),
            action_id=action.action_id,
            tool_call_id=action.tool_call_id,
            success=success,
            domain=domain,
            result=result,
            error=error,
            timestamp=datetime.now(timezone.utc),
        )
        self._observation_events.append(observation)
        return action, observation

    async def publish_decision(self, decision: BrainDecision) -> PublishResult:
        action, observation = await self._execute_write(
            action_type="publish_decision",
            domain="decision",
            key=str(decision.decision_id.value),
            value=decision.model_dump(),
        )
        return PublishResult(
            success=observation.success,
            weldmap_path=f"/decision/{decision.decision_id.value}",
            timestamp=observation.timestamp,
        )

    async def publish_escalation(self, escalation: Escalation) -> PublishResult:
        action, observation = await self._execute_write(
            action_type="publish_escalation",
            domain="negotiation",
            key=str(escalation.escalation_id),
            value=escalation.model_dump(),
        )
        return PublishResult(
            success=observation.success,
            weldmap_path=f"/negotiation/{escalation.escalation_id}",
            timestamp=observation.timestamp,
        )

    async def publish_instruction(self, instruction: LiveInstruction) -> PublishResult:
        action, observation = await self._execute_write(
            action_type="publish_instruction",
            domain="instruction",
            key=str(instruction.instruction_id),
            value=instruction.model_dump(),
        )
        return PublishResult(
            success=observation.success,
            weldmap_path=f"/instruction/{instruction.instruction_id}",
            timestamp=observation.timestamp,
        )

    async def notify_workflow_trigger(self, case_id: CaseId, workflow_config: dict) -> None:
        """P1-5 fix: notify L2 Control Plane to start a Temporal workflow.

        Writes to WeldMap workflow domain → Temporal listens for Signal.
        """
        await self._execute_write(
            action_type="notify_workflow",
            domain="workflow",
            key=str(case_id.value),
            value={"trigger": "cognitive_decision", "config": workflow_config},
        )
