"""CopilotOperate — Production line operation guidance (spec §11).

⚠️ HISTORICAL LEGACY — not wired into running app (2026-06-26 audit).
Phase 4/5 rewrite as /chat preset, do not extend. See copilot/__init__.py.

Reads current case state from the CognitiveGateway and emits a `LiveInstruction`
that can be published via `CognitiveGatewayWritePort.publish_instruction()`.
This is the B-level "what should the operator do right now" surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from cognitiveplane.gateway.ports import (
    CognitiveGatewayReadPort,
    CognitiveGatewayWritePort,
)
from cognitiveplane.interaction.signals.instruction import LiveInstruction
from cognitiveplane.shared.dto.context import WeldMapSnapshot
from cognitiveplane.shared.dto.gateway import PublishResult
from cognitiveplane.shared.enums import (
    InstructionStatus,
    InstructionType,
    InterventionGranularity,
)
from cognitiveplane.shared.types import CaseId, InstructionId


@dataclass
class OperationGuidance:
    case_id: CaseId
    snapshot: WeldMapSnapshot
    instruction: LiveInstruction
    rationale: str
    publish_result: PublishResult | None = None


class CopilotOperate:
    def __init__(
        self,
        gateway_read: CognitiveGatewayReadPort,
        gateway_write: CognitiveGatewayWritePort,
    ) -> None:
        self._read = gateway_read
        self._write = gateway_write

    async def recommend(
        self,
        case_id: CaseId,
        intent: InstructionType = InstructionType.ANNOTATE,
        granularity: InterventionGranularity = InterventionGranularity.IMAGE,
        payload: dict[str, Any] | None = None,
        rationale: str = "",
        issued_by: str = "copilot",
        publish: bool = False,
    ) -> OperationGuidance:
        snapshot = await self._read.read_weldmap_snapshot(case_id)
        instruction = LiveInstruction(
            instruction_id=InstructionId(value=uuid4()),
            instruction_type=intent,
            status=InstructionStatus.ACTIVE,
            target_case_id=case_id,
            granularity=granularity,
            payload=payload or {},
            reason=rationale or self._derive_rationale(snapshot, intent),
            issued_by=issued_by,
            issued_at=datetime.now(timezone.utc),
        )
        publish_result: PublishResult | None = None
        if publish:
            publish_result = await self._write.publish_instruction(instruction)
        return OperationGuidance(
            case_id=case_id,
            snapshot=snapshot,
            instruction=instruction,
            rationale=instruction.reason,
            publish_result=publish_result,
        )

    @staticmethod
    def _derive_rationale(
        snapshot: WeldMapSnapshot, intent: InstructionType
    ) -> str:
        return (
            f"copilot:{intent.value} suggested for case "
            f"{getattr(snapshot, 'case_id', 'unknown')}"
        )
