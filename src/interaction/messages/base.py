"""Base chat message for the Interaction Layer."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.shared.enums import ChatMessageType, SenderType


class BaseChatMessage(BaseModel):
    """Base class for all chat messages."""

    message_type: ChatMessageType
    sender: SenderType = SenderType.SYSTEM
    timestamp: datetime = Field(default_factory=lambda: datetime.now())
    metadata: dict[str, Any] = {}
