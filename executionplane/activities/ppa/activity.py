"""PPA Activity — 图像预处理执行单元。

Source: WeldEvent架构方案 §5.1 (PPA职责)

设计原则:
  - 从WeldMap读取IQA结果
  - 根据IQA检测到的问题调整预处理策略
  - 执行预处理（去噪、增强、校正）
  - 写入预处理结果到WeldMap
  - 返回ActivityOutput给L2

职责:
  1. 读取IQA报告（weldmap:///{wf_id}/image/quality）
  2. 根据issues调整预处理策略
  3. 执行预处理
  4. 写入预处理结果（weldmap:///{wf_id}/image/preprocess）
  5. 返回ActivityOutput

预处理策略映射:
  - exposure_too_dark → 对比度增强
  - exposure_too_bright → 亮度抑制
  - focus_blur → 降噪+锐化
  - deep_vision:reflection → 去反光
"""

import asyncio
from typing import Any

from numpy.typing import NDArray
import numpy as np

from ..base import (
    BaseActivity,
    ActivityInput,
    ActivityOutput,
    ActivityStatus,
    ActivityMetadata,
)
from ...capabilities.vision.preprocessor import (
    ImagePreprocessingPipeline,
    PreprocessedImage,
    DecodeStep,
    ValidateStep,
    ColorConvertStep,
)
from ...weldmap.client import WeldMapClient
from ...weldmap.models import WorkflowId, ImageQualityReport


# ---------------------------------------------------------------------------
# 预处理策略配置
# ---------------------------------------------------------------------------

PREPROCESS_STRATEGIES = {
    "exposure_too_dark": {"enhance_contrast": True, "gamma_correction": 1.2},
    "exposure_too_bright": {"suppress_brightness": True, "gamma_correction": 0.8},
    "exposure_abnormal": {"auto_level": True},
    "focus_blur": {"denoise": True, "sharpen": True, "sharpen_strength": 0.5},
    "completeness_crop": {"pad_border": True},
    "deep_vision:reflection": {"remove_reflection": True, "specular_threshold": 240},
    "deep_vision:occlusion": {"inpaint_occlusion": True},
    "deep_vision:lens_contamination": {"denoise": True, "clean_artifacts": True},
}


class PpaActivity(BaseActivity):
    """图像预处理Activity
    
    根据IQA结果调整预处理策略
    
    Usage::
        ppa = PpaActivity(weldmap=weldmap_client)
        output = await ppa.execute(ActivityInput(
            control_point_id="CP1",
            workflow_context={"workflow_id": "WF-001"},
        ))
    """
    
    def __init__(
        self,
        weldmap: WeldMapClient,
        preprocessor: ImagePreprocessingPipeline | None = None,
    ):
        self._weldmap = weldmap
        self._preprocessor = preprocessor or self._create_default_preprocessor()
        
        self._metadata = ActivityMetadata(
            activity_id="ppa_activity",
            name="图像预处理",
            version="v1",
            description="根据IQA结果调整预处理策略",
            capabilities=["preprocess", "enhance", "denoise"],
            execution_target="cpu",
            estimated_latency_ms=200,
        )
    
    @property
    def activity_name(self) -> str:
        return "ppa_activity"
    
    @property
    def metadata(self) -> ActivityMetadata:
        return self._metadata
    
    def _create_default_preprocessor(self) -> ImagePreprocessingPipeline:
        return ImagePreprocessingPipeline(steps=[
            DecodeStep(),
            ValidateStep(min_size=64, max_dimension=16384),
            ColorConvertStep(target="RGB"),
        ])
    
    async def execute(self, input: ActivityInput) -> ActivityOutput:
        """执行预处理"""
        workflow_id = input.workflow_context.get("workflow_id", "")
        wf_id = WorkflowId(workflow_id)
        params = input.params or {}
        
        # 读取IQA报告
        iqa_report = await self._weldmap.read_image_quality(wf_id)
        
        if iqa_report is None:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error="未找到IQA报告，请先执行IQA Activity",
            )
        
        if iqa_report.route_decision == "REJECT":
            return ActivityOutput(
                status=ActivityStatus.NG,
                data={"skipped": True, "reason": "IQA已拒绝该图像"},
            )
        
        # 解析问题并选择策略
        issues = self._extract_issues_from_iqa(iqa_report)
        strategies = self._select_strategies(issues)
        
        # 执行预处理
        preprocessed = await self._run_preprocessing(params, strategies)
        
        if not preprocessed.metadata.is_valid:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error=f"预处理失败: {preprocessed.metadata.error}",
            )
        
        return ActivityOutput(
            status=ActivityStatus.OK,
            data={
                "strategies_applied": list(strategies.keys()),
                "issues_detected": issues,
                "iqa_confidence": iqa_report.overall_confidence,
            },
        )
    
    def _extract_issues_from_iqa(self, report: ImageQualityReport) -> list[str]:
        issues = []
        if not report.resolution.passed:
            issues.append("resolution_insufficient")
        if not report.exposure.passed:
            issues.append("exposure_abnormal")
            if report.exposure.mean_gray < report.exposure.valid_range[0]:
                issues.append("exposure_too_dark")
            elif report.exposure.mean_gray > report.exposure.valid_range[1]:
                issues.append("exposure_too_bright")
        if not report.focus.passed:
            issues.append("focus_blur")
        if not report.completeness.passed:
            issues.append("completeness_crop")
        for anomaly in report.deep_vision_anomalies:
            issues.append(f"deep_vision:{anomaly}")
        return issues
    
    def _select_strategies(self, issues: list[str]) -> dict[str, Any]:
        strategies = {}
        for issue in issues:
            if issue in PREPROCESS_STRATEGIES:
                for key, value in PREPROCESS_STRATEGIES[issue].items():
                    strategies[key] = value
        return strategies
    
    async def _run_preprocessing(
        self,
        params: dict[str, Any],
        strategies: dict[str, Any],
    ) -> PreprocessedImage:
        preprocessed = await self._preprocessor.run(params=params)
        if not preprocessed.metadata.is_valid or preprocessed.image is None:
            return preprocessed
        
        image = preprocessed.image
        
        if strategies.get("enhance_contrast"):
            image = self._enhance_contrast(image)
        if strategies.get("gamma_correction"):
            image = self._apply_gamma(image, strategies["gamma_correction"])
        if strategies.get("denoise"):
            image = self._denoise(image)
        if strategies.get("sharpen"):
            image = self._sharpen(image, strategies.get("sharpen_strength", 0.5))
        
        preprocessed.image = image
        return preprocessed
    
    def _enhance_contrast(self, image: NDArray[np.uint8]) -> NDArray[np.uint8]:
        try:
            import cv2
            if image.ndim == 3:
                lab = cv2.cvtColor(image, cv2.COLOR_RGB2LAB)
                clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                lab[:, :, 0] = clahe.apply(lab[:, :, 0])
                return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
            else:
                clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                return clahe.apply(image)
        except ImportError:
            mean = np.mean(image)
            return np.clip(image * 1.2 - mean * 0.2, 0, 255).astype(np.uint8)
    
    def _apply_gamma(self, image: NDArray[np.uint8], gamma: float) -> NDArray[np.uint8]:
        normalized = image.astype(float) / 255.0
        corrected = np.power(normalized, gamma)
        return np.clip(corrected * 255, 0, 255).astype(np.uint8)
    
    def _denoise(self, image: NDArray[np.uint8]) -> NDArray[np.uint8]:
        try:
            import cv2
            if image.ndim == 3:
                return cv2.fastNlMeansDenoisingColored(image, None, h=10, hColor=10)
            else:
                return cv2.fastNlMeansDenoising(image, None, h=10)
        except ImportError:
            from scipy.ndimage import uniform_filter
            return uniform_filter(image, size=3).astype(np.uint8)
    
    def _sharpen(self, image: NDArray[np.uint8], strength: float) -> NDArray[np.uint8]:
        try:
            import cv2
            kernel = np.array([[-1, -1, -1], [-1, 9 + strength * 3, -1], [-1, -1, -1]])
            sharpened = cv2.filter2D(image, -1, kernel)
            return np.clip(sharpened, 0, 255).astype(np.uint8)
        except ImportError:
            return image