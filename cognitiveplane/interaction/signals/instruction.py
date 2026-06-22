"""LiveInstruction — async non-blocking production line intervention.

Source: L1-Interaction-Layer-Business-Requirements.md §2.5.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from cognitiveplane.shared.enums import InstructionStatus, InstructionType, InterventionGranularity
from cognitiveplane.shared.types import CaseId, InstructionId


class LiveInstruction(BaseModel):
    """An instruction injected into the production line without stopping it."""

    instruction_id: InstructionId
    instruction_type: InstructionType
    status: InstructionStatus = InstructionStatus.ACTIVE

    target_case_id: CaseId | None = None
    target_image_index: int | None = None
    target_step: str | None = None

    granularity: InterventionGranularity = InterventionGranularity.IMAGE
    payload: dict[str, Any] = {}

    reason: str = ""
    issued_by: str = ""
    issued_at: datetime = Field(default_factory=lambda: datetime.now())
    superseded_by: InstructionId | None = None
