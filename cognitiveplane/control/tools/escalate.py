"""EscalateTool — escalate to human intervention."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cognitiveplane.control.tools import BrainTool, ToolResult

if TYPE_CHECKING:
    from cognitiveplane.shared.ports.validation import ValidationPipelinePort
    from cognitiveplane.gateway.ports import CognitiveGatewayWritePort


class EscalateTool(BrainTool):
    """Escalate a decision to human intervention via WeldMap."""

    phase = 3

    def __init__(
        self,
        validation_port: ValidationPipelinePort,
        gateway_write: CognitiveGatewayWritePort,
    ) -> None:
        # validation_port retained in signature for backward compat with
        # composition root (tool_registry.py wires EscalateTool(validation, write)).
        # Field dropped 2026-06-26 — was dead (assigned but never read).
        self._gateway = gateway_write

    @property
    def name(self) -> str:
        return "escalate"

    @property
    def description(self) -> str:
        return (
            "Escalate to human intervention. Use when validation fails critically "
            "or when the system cannot confidently make a decision. Requires reason."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Reason for escalation (required by policy)",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["routine", "urgent", "critical"],
                    "description": "Urgency level",
                },
                "case_id": {
                    "type": "string",
                    "description": "Case ID being escalated",
                },
            },
            "required": ["reason"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        from cognitiveplane.shared.dto.gateway import Escalation
        from cognitiveplane.shared.dto_decision.outputs import EvidenceReference
        from cognitiveplane.shared.enums import UrgencyLevel, FallbackMode
        from cognitiveplane.shared.types import CaseId, DecisionId
        from datetime import datetime, timezone
        from uuid import uuid4

        urgency_map = {
            "routine": UrgencyLevel.ROUTINE,
            "urgent": UrgencyLevel.URGENT,
            "critical": UrgencyLevel.CRITICAL,
        }
        urgency = urgency_map.get(kwargs.get("urgency", "urgent"), UrgencyLevel.URGENT)

        escalation = Escalation(
            escalation_id=uuid4(),
            decision_id=DecisionId(value=uuid4()),
            case_id=CaseId(value=kwargs.get("case_id", "unknown")),
            reason=kwargs["reason"],
            urgency=urgency,
            fallback_mode=FallbackMode.HUMAN_INTERVENTION,
            supporting_evidence=[],
            created_at=datetime.now(timezone.utc),
        )

        try:
            result = await self._gateway.publish_escalation(escalation)
            return ToolResult(output={
                "escalation_id": str(escalation.escalation_id),
                "success": result.success,
                "urgency": urgency.value,
            })
        except Exception as e:
            return ToolResult(error=str(e))
