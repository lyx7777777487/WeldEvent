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
import logging
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
from ...weldmap.client import WeldMapClient, WeldMapPath
from ...weldmap.models import WorkflowId, ImageQualityReport

logger = logging.getLogger(__name__)


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

        # 读取IQA报告 — 优先从 WeldMap（主路径），回退到 dependency_results（P5 fix）
        iqa_report = await self._weldmap.read_image_quality(wf_id)
        issues: list[str] = []
        route_decision = "AUTO_PASS"
        iqa_confidence = 0.0
        fallback_used = False

        if iqa_report is not None:
            # 主路径：WeldMap 成功读取
            route_decision = iqa_report.route_decision
            iqa_confidence = iqa_report.overall_confidence
            issues = self._extract_issues_from_iqa(iqa_report)
        else:
            # 回退路径：从 dag_runner 注入的 dependency_results 读取上游 IQA 节点输出
            # WeldMap InMemory 重启即丢，或生产 Redis 瞬时不可用时降级
            dep_results = input.workflow_context.get("dependency_results", {})
            fallback_data = self._extract_iqa_from_dep_results(dep_results)
            if fallback_data is None:
                return ActivityOutput(
                    status=ActivityStatus.ERROR,
                    error="未找到IQA报告，请先执行IQA Activity（WeldMap 和 dependency_results 均无数据）",
                )
            route_decision = fallback_data["route_decision"]
            iqa_confidence = fallback_data["confidence"]
            issues = fallback_data["issues"]
            fallback_used = True
            logger.warning(
                "PPA WeldMap 读取失败，回退到 dependency_results (workflow=%s, issues=%s)",
                workflow_id, issues,
            )

        if route_decision == "REJECT":
            return ActivityOutput(
                status=ActivityStatus.NG,
                data={"skipped": True, "reason": "IQA已拒绝该图像"},
            )

        # 选择策略（issues 已就绪）
        strategies = self._select_strategies(issues)
        
        # 执行预处理
        preprocessed = await self._run_preprocessing(params, strategies)
        
        if not preprocessed.metadata.is_valid:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error=f"预处理失败: {preprocessed.metadata.error}",
            )
        
        # 写入预处理结果到 WeldMap
        preprocess_data = {
            "strategies_applied": list(strategies.keys()),
            "issues_detected": issues,
            "iqa_confidence": iqa_confidence,
            "fallback_used": fallback_used,
            "metadata": preprocessed.metadata.model_dump() if hasattr(preprocessed.metadata, "model_dump") else {},
        }
        try:
            await self._weldmap.write_path(
                wf_id,
                WeldMapPath("image/preprocess"),
                preprocess_data,
                source=self.activity_name,
            )
        except Exception as e:
            logger.error("PPA WeldMap写入失败 (workflow=%s): %s: %s", wf_id, type(e).__name__, e)
            raise RuntimeError(f"WeldMap写入失败: {type(e).__name__}: {e}") from e

        return ActivityOutput(
            status=ActivityStatus.OK,
            data={
                "strategies_applied": list(strategies.keys()),
                "issues_detected": issues,
                "iqa_confidence": iqa_confidence,
                "fallback_used": fallback_used,
                "preprocess_path": "image/preprocess",
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

    @staticmethod
    def _extract_iqa_from_dep_results(
        dep_results: dict[str, Any],
    ) -> dict[str, Any] | None:
        """从 dag_runner 注入的 dependency_results 提取 IQA 节点输出。

        IQA 节点返回的 data 包含:
            route_decision: "AUTO_PASS" / "SUGGEST_REVIEW" / "MANDATORY_REVIEW" / "REJECT"
            confidence: float
            issues: list[str]  — 检测到的问题列表

        Returns:
            {"route_decision": ..., "confidence": ..., "issues": [...]} 或 None
        """
        for dep_id, dep_result in dep_results.items():
            if not isinstance(dep_result, dict):
                continue
            data = dep_result.get("data") or {}
            if not isinstance(data, dict):
                continue
            # IQA 节点的标志性字段
            if "route_decision" in data or "issues" in data:
                return {
                    "route_decision": data.get("route_decision", "AUTO_PASS"),
                    "confidence": data.get("confidence", 0.0),
                    "issues": list(data.get("issues", [])),
                }
        return None
    
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
        
        # 原有 4 个策略
        if strategies.get("enhance_contrast"):
            image = self._enhance_contrast(image)
        if strategies.get("gamma_correction"):
            image = self._apply_gamma(image, strategies["gamma_correction"])
        if strategies.get("denoise"):
            image = self._denoise(image)
        if strategies.get("sharpen"):
            image = self._sharpen(image, strategies.get("sharpen_strength", 0.5))
        
        # 新增 7 个策略
        if strategies.get("pad_border"):
            image = self._pad_border(image, size=32)
        if strategies.get("auto_level"):
            image = self._auto_level(image)
        if strategies.get("suppress_brightness"):
            image = self._suppress_brightness(image)
        if strategies.get("specular_threshold"):
            image = self._specular_threshold(image, int(strategies["specular_threshold"]))
        if strategies.get("remove_reflection"):
            threshold = int(strategies.get("specular_threshold", 240))
            image = self._remove_reflection(image, threshold=threshold)
        if strategies.get("inpaint_occlusion"):
            image = self._inpaint_occlusion(image)
        if strategies.get("clean_artifacts"):
            image = self._clean_artifacts(image)
        
        preprocessed.image = image
        return preprocessed
    
    # ------------------------------------------------------------------
    # 原有预处理方法
    # ------------------------------------------------------------------
    
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
            logger.warning("cv2 unavailable, denoise degraded to uniform_filter")
            from scipy.ndimage import uniform_filter
            return uniform_filter(image, size=3).astype(np.uint8)
    
    def _sharpen(self, image: NDArray[np.uint8], strength: float) -> NDArray[np.uint8]:
        try:
            import cv2
            kernel = np.array([[-1, -1, -1], [-1, 9 + strength * 3, -1], [-1, -1, -1]])
            sharpened = cv2.filter2D(image, -1, kernel)
            return np.clip(sharpened, 0, 255).astype(np.uint8)
        except ImportError:
            logger.warning("cv2 unavailable, sharpen skipped (no-op)")
            return image
    
    # ------------------------------------------------------------------
    # 新增预处理策略
    # ------------------------------------------------------------------
    
    def _pad_border(self, image: NDArray[np.uint8], size: int = 32) -> NDArray[np.uint8]:
        """给图像加黑色边框（padding size 默认 32px）。"""
        if image.ndim == 3:
            pad_width = ((size, size), (size, size), (0, 0))
        else:
            pad_width = ((size, size), (size, size))
        return np.pad(image, pad_width, mode="constant", constant_values=0).astype(np.uint8)
    
    def _auto_level(self, image: NDArray[np.uint8]) -> NDArray[np.uint8]:
        """Percentile-based 自动色阶（纯 numpy 实现）。"""
        p_low = float(np.percentile(image, 1))
        p_high = float(np.percentile(image, 99))
        if p_high <= p_low:
            return image
        # 线性拉伸到 [0, 255]
        stretched = (image.astype(float) - p_low) / (p_high - p_low) * 255.0
        return np.clip(stretched, 0, 255).astype(np.uint8)
    
    def _suppress_brightness(self, image: NDArray[np.uint8]) -> NDArray[np.uint8]:
        """对高亮度区域（>200）做 gamma correction 0.5。"""
        if image.ndim == 3:
            gray = np.mean(image, axis=2)
        else:
            gray = image
        bright_mask = gray > 200
        if not bright_mask.any():
            return image
        # 对高亮区域应用 gamma=0.5
        normalized = image.astype(float) / 255.0
        corrected = np.power(normalized, 0.5) * 255.0
        if image.ndim == 3:
            mask_broadcast = np.broadcast_to(bright_mask[..., None], image.shape)
        else:
            mask_broadcast = bright_mask
        result = np.where(mask_broadcast, corrected, image.astype(float))
        return np.clip(result, 0, 255).astype(np.uint8)
    
    def _specular_threshold(
        self, image: NDArray[np.uint8], threshold: int
    ) -> NDArray[np.uint8]:
        """标记镜面反光区域（仅记录，不修改原图）。"""
        if image.ndim == 3:
            gray = np.mean(image, axis=2)
        else:
            gray = image
        mask = gray > threshold
        if mask.any():
            logger.warning(
                "Specular reflection detected: %d pixels above threshold %d",
                int(mask.sum()), threshold,
            )
        # 不修改原图，仅记录
        return image
    
    def _remove_reflection(
        self, image: NDArray[np.uint8], threshold: int = 240
    ) -> NDArray[np.uint8]:
        """对高亮像素（>threshold）做中值滤波替换（纯 numpy fallback）。"""
        if image.ndim == 3:
            gray = np.mean(image, axis=2)
        else:
            gray = image
        bright_mask = gray > threshold
        if not bright_mask.any():
            return image
        try:
            import cv2
            blurred = cv2.medianBlur(image, 5)
        except ImportError:
            logger.warning(
                "cv2 unavailable, falling back to scipy median filter for remove_reflection"
            )
            from scipy.ndimage import median_filter
            if image.ndim == 3:
                blurred = np.stack([
                    median_filter(image[..., c], size=3, mode="nearest")
                    for c in range(image.shape[2])
                ], axis=-1).astype(np.uint8)
            else:
                blurred = median_filter(image, size=3, mode="nearest").astype(np.uint8)
        result = image.copy()
        if image.ndim == 3:
            mask_broadcast = np.broadcast_to(bright_mask[..., None], image.shape)
        else:
            mask_broadcast = bright_mask
        result[mask_broadcast] = blurred[mask_broadcast]
        return result
    
    def _inpaint_occlusion(self, image: NDArray[np.uint8]) -> NDArray[np.uint8]:
        """对遮挡区域做 inpaint（cv2.inpaint 可用时用，否则 log warning 跳过）。"""
        try:
            import cv2
        except ImportError:
            logger.warning("cv2 unavailable, skipping inpaint_occlusion")
            return image
        if image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        else:
            gray = image
        # 检测遮挡区域（极暗像素作为遮挡候选）
        mask = (gray < 30).astype(np.uint8) * 255
        if not mask.any():
            return image
        inpainted = cv2.inpaint(image, mask, 3, cv2.INPAINT_TELEA)
        return inpainted
    
    def _clean_artifacts(self, image: NDArray[np.uint8]) -> NDArray[np.uint8]:
        """用 morphological opening 清除小噪点（cv2 可用时用，否则用 median_filter fallback）。"""
        try:
            import cv2
            kernel = np.ones((3, 3), np.uint8)
            if image.ndim == 3:
                result = np.stack([
                    cv2.morphologyEx(image[..., c], cv2.MORPH_OPEN, kernel)
                    for c in range(image.shape[2])
                ], axis=-1)
                return result.astype(np.uint8)
            else:
                return cv2.morphologyEx(image, cv2.MORPH_OPEN, kernel)
        except ImportError:
            logger.warning(
                "cv2 unavailable, falling back to scipy median_filter for clean_artifacts"
            )
            from scipy.ndimage import median_filter
            if image.ndim == 3:
                result = np.stack([
                    median_filter(image[..., c], size=3, mode="nearest")
                    for c in range(image.shape[2])
                ], axis=-1)
            else:
                result = median_filter(image, size=3, mode="nearest")
            return result.astype(np.uint8)
