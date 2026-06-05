"""Tests for Interaction Layer message types."""

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
from src.shared.enums import (
    AnnotationType,
    ChatMessageType,
    ResultLevel,
    SenderType,
    UserActionType,
)
from src.shared.types import ImageId


class TestBaseChatMessage:
    def test_create(self):
        msg = BaseChatMessage(message_type=ChatMessageType.TEXT)
        assert msg.sender == SenderType.SYSTEM
        assert msg.metadata == {}


class TestInspectionResultMessage:
    def test_with_annotations_and_defects(self):
        msg = InspectionResultMessage(
            image_id=ImageId(value="IMG-001"),
            result_level=ResultLevel.ANOMALY,
            summary="Crack detected at weld seam",
            annotations=[
                ImageAnnotation(
                    annotation_type=AnnotationType.BBOX,
                    coordinates=[0.1, 0.2, 0.3, 0.4],
                    label="crack",
                    confidence=0.92,
                )
            ],
            defects=[
                DefectSummary(
                    defect_type="crack",
                    severity=ResultLevel.ANOMALY,
                    description="Longitudinal crack 15mm",
                    location="weld seam",
                )
            ],
            action_options=[
                UserActionOption(
                    action_type=UserActionType.ESCALATE,
                    label="Escalate",
                    description="Escalate to senior engineer",
                )
            ],
        )
        assert msg.message_type == ChatMessageType.INSPECTION_RESULT
        assert len(msg.annotations) == 1
        assert msg.annotations[0].annotation_type == AnnotationType.BBOX
        assert len(msg.defects) == 1
        assert msg.defects[0].severity == ResultLevel.ANOMALY
        assert len(msg.action_options) == 1


class TestProgressUpdateMessage:
    def test_create(self):
        msg = ProgressUpdateMessage(
            case_id="CASE-001",
            current_step=5,
            total_steps=20,
            progress_percent=25.0,
            status_text="Processing image 5/20",
        )
        assert msg.message_type == ChatMessageType.PROGRESS_UPDATE
        assert msg.progress_percent == 25.0


class TestStrategyChangeNotification:
    def test_create(self):
        msg = StrategyChangeNotification(
            case_id="CASE-001",
            old_strategy="standard",
            new_strategy="intensified",
            reason="Anomaly detected",
        )
        assert msg.message_type == ChatMessageType.STRATEGY_CHANGE
        assert msg.new_strategy == "intensified"


class TestImageZoomMessage:
    def test_create(self):
        msg = ImageZoomMessage(
            image_id=ImageId(value="IMG-001"),
            zoom_region=[0.1, 0.2, 0.3, 0.4],
            zoom_level=2.5,
            highlight_description="Crack region",
        )
        assert msg.message_type == ChatMessageType.IMAGE_ANNOTATED
        assert msg.zoom_level == 2.5


class TestLogEntryMessage:
    def test_create(self):
        msg = LogEntryMessage(
            log_level="warning",
            source="CP-Engine",
            content="Threshold exceeded on parameter W2",
        )
        assert msg.message_type == ChatMessageType.LOG_ENTRY
        assert msg.log_level == "warning"
