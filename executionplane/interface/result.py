"""IQA检测结果数据结构。"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..activities.base import ActivityOutput, ActivityStatus


@dataclass
class IqaResult:
    """IQA检测结果"""

    # 基本信息
    image_path: str
    workflow_id: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    # 检测状态
    status: str = ""  # OK / MARGINAL / NG / ERROR
    route_decision: str = ""  # AUTO_PASS / SUGGEST_REVIEW / MANDATORY_REVIEW / REJECT
    confidence: float = 0.0

    # 各项检测结果
    resolution_passed: bool = False
    resolution_detail: str = ""
    exposure_passed: bool = False
    exposure_detail: str = ""
    focus_passed: bool = False
    focus_detail: str = ""
    completeness_passed: bool = False
    completeness_detail: str = ""

    # MLLM结果
    deep_vision_triggered: bool = False
    deep_vision_anomalies: list[str] = field(default_factory=list)
    mllm_error: str | None = None

    # 错误信息
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "image_path": self.image_path,
            "workflow_id": self.workflow_id,
            "timestamp": self.timestamp,
            "status": self.status,
            "route_decision": self.route_decision,
            "confidence": self.confidence,
            "checks": {
                "resolution": {
                    "passed": self.resolution_passed,
                    "detail": self.resolution_detail,
                },
                "exposure": {
                    "passed": self.exposure_passed,
                    "detail": self.exposure_detail,
                },
                "focus": {
                    "passed": self.focus_passed,
                    "detail": self.focus_detail,
                },
                "completeness": {
                    "passed": self.completeness_passed,
                    "detail": self.completeness_detail,
                },
            },
            "deep_vision": {
                "triggered": self.deep_vision_triggered,
                "anomalies": self.deep_vision_anomalies,
                "error": self.mllm_error,
            },
            "error": self.error,
        }

    def to_json(self) -> str:
        """转换为JSON"""
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @property
    def is_passed(self) -> bool:
        """是否通过检测"""
        return self.status == "OK"

    @property
    def needs_review(self) -> bool:
        """是否需要人工审查"""
        return self.status == "MARGINAL"

    @property
    def is_rejected(self) -> bool:
        """是否被拒绝"""
        return self.status == "NG"

    @property
    def has_error(self) -> bool:
        """是否有错误"""
        return self.status == "ERROR"


def _convert_output_to_result(
    output: ActivityOutput,
    image_path: str,
    workflow_id: str,
) -> IqaResult:
    """将ActivityOutput转换为IqaResult"""

    if output.status == ActivityStatus.ERROR:
        return IqaResult(
            image_path=image_path,
            workflow_id=workflow_id,
            status="ERROR",
            error=output.error,
        )

    data = output.data or {}
    checks = data.get("checks", {})

    resolution_check = checks.get("resolution", {})
    exposure_check = checks.get("exposure", {})
    focus_check = checks.get("focus", {})
    completeness_check = checks.get("completeness", {})

    deep_vision = data.get("deep_vision", {})

    return IqaResult(
        image_path=image_path,
        workflow_id=workflow_id,
        status=output.status.value,
        route_decision=data.get("route_decision", ""),
        confidence=data.get("confidence", 0.0),
        resolution_passed=resolution_check.get("passed", False),
        resolution_detail=resolution_check.get("detail", ""),
        exposure_passed=exposure_check.get("passed", False),
        exposure_detail=exposure_check.get("detail", ""),
        focus_passed=focus_check.get("passed", False),
        focus_detail=focus_check.get("detail", ""),
        completeness_passed=completeness_check.get("passed", False),
        completeness_detail=completeness_check.get("detail", ""),
        deep_vision_triggered=deep_vision.get("triggered", False),
        deep_vision_anomalies=deep_vision.get("anomalies", []),
        mllm_error=deep_vision.get("error"),
    )
