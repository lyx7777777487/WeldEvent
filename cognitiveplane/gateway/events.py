"""Gateway Action/Observation event pairs — dual-ID pairing.

Source: 7-plane redesign spec §6 Action/Observation Event Pairs.
Each CognitiveGateway write produces an ActionEvent; WeldMap response is
an ObservationEvent. Pairing via action_id back-pointer + tool_call_id grouping.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class GatewayActionEvent:
    """Action: CognitiveGateway initiates a write to WeldMap.

    Modeled after OpenHands ActionEvent (event/llm_convertible/action.py:23-66).
    """
    action_id: str
    tool_call_id: str
    action_type: str       # "publish_decision" | "publish_escalation" | "notify_workflow"
    domain: str            # WeldMap domain: "decision" | "negotiation" | "workflow" | "instruction"
    payload: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class GatewayObservationEvent:
    """Observation: WeldMap responds to a write action.

    Modeled after OpenHands ObservationEvent (event/llm_convertible/observation.py:18-37).
    Pairs back to ActionEvent via action_id back-pointer.
    """
    observation_id: str
    action_id: str         # Back-pointer to the GatewayActionEvent.action_id
    tool_call_id: str      # Same tool_call_id as the paired ActionEvent
    success: bool
    domain: str
    result: dict | None = None
    error: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
