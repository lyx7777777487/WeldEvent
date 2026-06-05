"""ImageZoomMessage."""

from src.interaction.messages.base import BaseChatMessage
from src.shared.enums import ChatMessageType, SenderType
from src.shared.types import ImageId


class ImageZoomMessage(BaseChatMessage):
    """Message showing a zoomed-in region of an inspection image."""

    message_type: ChatMessageType = ChatMessageType.IMAGE_ANNOTATED
    sender: SenderType = SenderType.SYSTEM

    image_id: ImageId
    zoom_region: list[float] = []  # [x1, y1, x2, y2] normalized coords
    zoom_level: float = 1.0
    highlight_description: str = ""
