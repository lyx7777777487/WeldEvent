"""test_validator — template_validator 三重验证。"""

import pytest

from l1_agent.design.template_validator import validate, ValidationResult


def _valid_template() -> dict:
    return {
        "id": "weld_annotation_v1",
        "version": "1",
        "entry_point": "cp1",
        "control_points": [
            {
                "id": "cp1",
                "name": "annotation",
                "activity_binding": {"activity_name": "annotation"},
                "execution_policy": {},
                "params": {
                    "dataset_id": "ds-001",
                    "job_name": "焊缝标注作业",
                    "labels": "气孔,裂纹",
                },
            }
        ],
        "transitions": [],
    }


def test_validate_happy_path():
    result = validate(_valid_template())
    assert result.ok is True


def test_validate_missing_id():
    t = _valid_template()
    del t["id"]
    result = validate(t)
    assert result.ok is False
    assert "id" in result.error


def test_validate_entry_point_not_in_cps():
    t = _valid_template()
    t["entry_point"] = "cp99"
    result = validate(t)
    assert result.ok is False
    assert "cp99" in result.error or "entry_point" in result.error


def test_validate_unknown_activity():
    t = _valid_template()
    t["control_points"][0]["activity_binding"]["activity_name"] = "unknown_activity"
    result = validate(t)
    assert result.ok is False
    assert "unknown_activity" in result.error or "白名单" in result.error


def test_validate_empty_cps():
    t = _valid_template()
    t["control_points"] = []
    result = validate(t)
    assert result.ok is False
    assert "control_points" in result.error
