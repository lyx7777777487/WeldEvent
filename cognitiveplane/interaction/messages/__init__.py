"""Rich media message types for the Interaction Layer."""

from cognitiveplane.interaction.messages.base import BaseChatMessage
from cognitiveplane.interaction.messages.inspection_result import (
    DefectSummary,
    ImageAnnotation,
    InspectionResultMessage,
    UserActionOption,
)
from cognitiveplane.interaction.messages.log import LogEntryMessage
from cognitiveplane.interaction.messages.progress import ProgressUpdateMessage
from cognitiveplane.interaction.messages.strategy_change import StrategyChangeNotification
from cognitiveplane.interaction.messages.zoom import ImageZoomMessage

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
