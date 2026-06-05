"""Tests for ImageEntity."""

from src.interaction.entities.image import ImageEntity
from src.shared.types import CaseId, ImageId


class TestImageEntity:
    def test_create(self):
        entity = ImageEntity(
            image_id=ImageId(value="IMG-001"),
            case_id=CaseId(value="CASE-001"),
            image_index=0,
        )
        assert entity.cp_status is None
        assert entity.annotations == []

    def test_with_cp_status(self):
        entity = ImageEntity(
            image_id=ImageId(value="IMG-002"),
            case_id=CaseId(value="CASE-001"),
            image_index=5,
            cp_status="anomaly",
            inspection_result={"defect_type": "crack"},
        )
        assert entity.cp_status == "anomaly"
        assert entity.inspection_result["defect_type"] == "crack"
