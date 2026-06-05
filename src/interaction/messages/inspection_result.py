"""InspectionResultMessage — core quality inspection result with annotations.

Source: L1-Interaction-Layer-Business-Requirements.md §7.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.interaction.messages.base import BaseChatMessage
from src.shared.enums import AnnotationType, ChatMessageType, ResultLevel, SenderType, UserActionType
from src.shared.types import ImageId


class ImageAnnotation(BaseModel):
    """Annotation on an inspection image."""

    annotation_type: AnnotationType
    coordinates: list[float] = []
    label: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = {}


class DefectSummary(BaseModel):
    """Summary of detected defects."""

    defect_type: str
    severity: ResultLevel = ResultLevel.ROUTINE
    description: str = ""
    location: str = ""


class UserActionOption(BaseModel):
    """An action the user can take in response to an inspection result."""

    action_type: UserActionType
    label: str
    description: str = ""


class InspectionResultMessage(BaseChatMessage):
    """Core quality inspection result with annotations and action options."""

    message_type: ChatMessageType = ChatMessageType.INSPECTION_RESULT
    sender: SenderType = SenderType.SYSTEM

    image_id: ImageId
    result_level: ResultLevel = ResultLevel.ROUTINE
    summary: str = ""
    annotations: list[ImageAnnotation] = []
    defects: list[DefectSummary] = []
    action_options: list[UserActionOption] = []
