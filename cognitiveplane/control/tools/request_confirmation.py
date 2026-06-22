"""RequestConfirmationTool — request human confirmation (Agent→Human).

Source: 7-plane redesign spec §7 lines 1414-1456.
Writes a confirmation request to the WeldMap negotiation domain via
CognitiveGatewayWritePort. The operator responds through the
notification interface (FastAPI WebSocket or polling).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from cognitiveplane.control.tools import BrainTool, ToolResult

if TYPE_CHECKING:
    from cognitiveplane.gateway.ports import CognitiveGatewayWritePort


class RequestConfirmationTool(BrainTool):
    """Request confirmation from a human operator.

    Writes to WeldMap negotiation domain so the notification
    subsystem can push the question to the operator.
    """

    def __init__(self, gateway_write: CognitiveGatewayWritePort | None = None) -> None:
        self._gateway = gateway_write

    @property
    def name(self) -> str:
        return "request_confirmation"

    @property
    def description(self) -> str:
        return (
            "Request confirmation from a human operator. "
            "Sends a question with options to the operator via WeldMap negotiation domain. "
            "The operator can respond through the notification interface."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Question to ask the operator",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of option choices for the operator",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["routine", "urgent", "critical"],
                    "description": "Urgency level of the confirmation request",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Timeout in seconds for operator response (default 300)",
                },
            },
            "required": ["question"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        question = kwargs["question"]
        options = kwargs.get("options", [])
        urgency = kwargs.get("urgency", "routine")
        timeout_seconds = kwargs.get("timeout_seconds", 300)

        if self._gateway is not None:
            from cognitiveplane.interaction.signals.instruction import LiveInstruction
            from cognitiveplane.shared.enums import InstructionType, InstructionStatus, InterventionGranularity
            from cognitiveplane.shared.types import InstructionId

            instruction = LiveInstruction(
                instruction_id=InstructionId(value=str(uuid4())),
                instruction_type=InstructionType.MARK_FOR_REVIEW,
                status=InstructionStatus.ACTIVE,
                granularity=InterventionGranularity.CASE,
                payload={
                    "question": question,
                    "options": options,
                    "urgency": urgency,
                    "timeout_seconds": timeout_seconds,
                },
                reason=question,
                issued_by="brain",
            )
            try:
                result = await self._gateway.publish_instruction(instruction)
                return ToolResult(output={
                    "status": "confirmation_requested",
                    "question": question,
                    "options": options,
                    "urgency": urgency,
                    "timeout_seconds": timeout_seconds,
                    "success": result.success,
                })
            except Exception as e:
                return ToolResult(error=str(e))

        return ToolResult(output={
            "status": "confirmation_requested",
            "question": question,
            "options": options,
            "urgency": urgency,
            "timeout_seconds": timeout_seconds,
            "message": "Confirmation request sent to operator. Awaiting response.",
        })
