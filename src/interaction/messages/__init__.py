"""Rich media message types for the Interaction Layer."""

from src.interaction.messages.base import BaseChatMessage
from src.interaction.messages.inspection_result import (
    DefectSummary,
    ImageAnnotation,
    InspectionResultMessage,
    UserActionOption,
)
from src.interaction.messages.log import LogEntryMessage
from src.interaction.messages.progress import ProgressUpdateMessage
from src.interaction.messages.strategy_change import StrategyChangeNotification
from src.interaction.messages.zoom import ImageZoomMessage

__all__ = [
    "BaseChatMessage",
    "DefectSummary",
    "ImageAnnotation",
    "ImageZoomMessage",
    "InspectionResultMessage",
    "LogEntryMessage",
    "ProgressUpdateMessage",
    "StrategyChangeNotification",
    "UserActionOption",
]
