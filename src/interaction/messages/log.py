"""LogEntryMessage."""

from src.interaction.messages.base import BaseChatMessage
from src.shared.enums import ChatMessageType, SenderType


class LogEntryMessage(BaseChatMessage):
    """A log entry displayed in the interaction chat."""

    message_type: ChatMessageType = ChatMessageType.LOG_ENTRY
    sender: SenderType = SenderType.SYSTEM

    log_level: str = "info"
    source: str = ""
    content: str = ""
