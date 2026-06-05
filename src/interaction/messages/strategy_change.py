"""StrategyChangeNotification."""

from src.interaction.messages.base import BaseChatMessage
from src.shared.enums import ChatMessageType, SenderType


class StrategyChangeNotification(BaseChatMessage):
    """Notification when inspection strategy changes."""

    message_type: ChatMessageType = ChatMessageType.STRATEGY_CHANGE
    sender: SenderType = SenderType.SYSTEM

    case_id: str = ""
    old_strategy: str = ""
    new_strategy: str = ""
    reason: str = ""
