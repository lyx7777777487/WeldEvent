"""ProgressUpdateMessage."""

from pydantic import Field

from cognitiveplane.interaction.messages.base import BaseChatMessage
from cognitiveplane.shared.enums import ChatMessageType, SenderType


class ProgressUpdateMessage(BaseChatMessage):
    """Progress update for a running case."""

    message_type: ChatMessageType = ChatMessageType.PROGRESS_UPDATE
    sender: SenderType = SenderType.SYSTEM

    case_id: str = ""
    current_step: int = 0
    total_steps: int = 0
    progress_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    status_text: str = ""
