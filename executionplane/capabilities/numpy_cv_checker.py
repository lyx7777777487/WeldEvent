"""默认 CV 规则检查器 — 使用 numpy 实现（无 OpenCV 依赖的轻量版）。

用于开发和测试环境。生产环境应替换为基于 OpenCV 的实现。
"""

import asyncio

import numpy as np
from numpy.typing import NDArray

from .cv_rules import (
    CVRuleChecker,
    CompletenessRule,
    ExposureRule,
    FocusRule,
    ResolutionRule,
    RuleEngineOutput,
    RuleResult,
    RuleSeverity,
)


class NumpyCVRuleChecker(CVRuleChecker):
    """基于 numpy 的默认规则检查器。
    
    不依赖 OpenCV，使用纯 numpy 计算:
      - 分辨率: array.shape
      - 曝光: np.mean()
      - 对焦: Laplacian 方差 (scipy-like 或手动卷积近似)
      - 完整性: 简单的边缘区域占比估算
    """

    async def check_resolution(
        self, image: NDArray[np.uint8], rule: ResolutionRule | None = None
    ) -> RuleResult:
        rule = rule or ResolutionRule()
        h, w = image.shape[:2]
        passed = w >= rule.min_width and h >= rule.min_height
        return RuleResult(
            rule_id=rule.rule_id,
            passed=passed,
            severity=RuleSeverity.ERROR if not passed else RuleSeverity.INFO,
            value=float(w * h),
            threshold=float(rule.min_width * rule.min_height),
            detail=f"{w}x{h}" + ("" if passed else f" < {rule.min_width}x{rule.min_height}"),
        )

    async def check_exposure(
        self, image: NDArray[np.uint8], rule: ExposureRule | None = None
    ) -> RuleResult:
        rule = rule or ExposureRule()
        gray = self._to_grayscale(image)
        mean_val = float(np.mean(gray))
        passed = rule.min_mean_gray <= mean_val <= rule.max_mean_gray
        return RuleResult(
            rule_id=rule.rule_id,
            passed=passed,
            severity=RuleSeverity.WARNING if not passed else RuleSeverity.INFO,
            value=mean_val,
            threshold=(rule.min_mean_gray + rule.max_mean_gray) / 2,
            detail=f"mean_gray={mean_val:.1f}" + (
                "" if passed
                else f" ∉ [{rule.min_mean_gray}, {rule.max_mean_gray}]"
            ),
        )

    async def check_focus(
        self, image: NDArray[np.uint8], rule: FocusRule | None = None
    ) -> RuleResult:
        rule = rule or FocusRule()
        gray = self._to_grayscale(image)
        laplacian_var = float(self._laplacian_variance(gray))
        passed = laplacian_var >= rule.laplacian_threshold
        return RuleResult(
            rule_id=rule.rule_id,
            passed=passed,
            severity=RuleSeverity.WARNING if not passed else RuleSeverity.INFO,
            value=laplacian_var,
            threshold=rule.laplacian_threshold,
            detail=f"Laplacian_var={laplacian_var:.1f}" + (
                "" if passed else f" < {rule.laplacian_threshold}"
            ),
        )

    async def check_completeness(
        self, image: NDArray[np.uint8], rule: CompletenessRule | None = None
    ) -> RuleResult:
        rule = rule or CompletenessRule()
        h, w = image.shape[:2]
        
        # 简单启发式: 检查边缘暗区占比（模拟焊缝区域检测）
        gray = self._to_grayscale(image)
        dark_ratio = float(np.mean(gray < 30))  # 近似暗像素比例
        
        passed = dark_ratio >= rule.min_weld_region_ratio
        return RuleResult(
            rule_id=rule.rule_id,
            passed=passed,
            severity=RuleSeverity.WARNING if not passed else RuleSeverity.INFO,
            value=dark_ratio,
            threshold=rule.min_weld_region_ratio,
            detail=f"weld_region_ratio={dark_ratio:.2%}" + (
                "" if passed
                else f" < {rule.min_weld_region_ratio:.0%} (可能被裁切)"
            ),
        )

    # ------------------------------------------------------------------
    # 综合规则
    # ------------------------------------------------------------------

    async def run_all_rules(
        self, image: NDArray[np.uint8],
        confidence_threshold: float = 0.85,
    ) -> RuleEngineOutput:
        """执行全部规则并返回综合结果。"""
        results = list(await asyncio.gather(
            self.check_resolution(image),
            self.check_exposure(image),
            self.check_focus(image),
            self.check_completeness(image),
        ))
        all_passed = all(r.passed for r in results)
        avg_confidence = float(np.mean([
            getattr(r, 'confidence', 1.0) for r in results
        ]))
        if avg_confidence >= confidence_threshold and all_passed:
            route = "AUTO_PASS"
        elif all_passed:
            route = "SUGGEST_REVIEW"
        else:
            route = "MANDATORY_REVIEW"
        return RuleEngineOutput(
            results=results,
            overall_confidence=avg_confidence,
            route_decision=route,
        )

    # ------------------------------------------------------------------
    # 私有工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _to_grayscale(image: NDArray[np.uint8]) -> NDArray[np.floating]:
        """转灰度图。支持 RGB/RGBA/Gray 输入。"""
        if image.ndim == 2:
            return image.astype(float)
        if image.shape[2] >= 3:
            # ITU-R BT.601 标准灰度转换
            return (0.299 * image[:, :, 0].astype(float)
                  + 0.587 * image[:, :, 1].astype(float)
                  + 0.114 * image[:, :, 2].astype(float))
        return image[:, :, 0].astype(float)

    @staticmethod
    def _laplacian_variance(gray: NDArray[np.floating]) -> float:
        """计算 Laplacian 方差（对焦度量）。"""
        # 手动 Laplacian 卷积核
        kernel = np.array([
            [0,  1, 0],
            [1, -4, 1],
            [0,  1, 0],
        ], dtype=float)
        
        from scipy.ndimage import convolve as scipy_convolve
        try:
            laplacian = scipy_convolve(gray, kernel, mode='reflect')
        except ImportError:
            # 无 scipy 时用简单差分近似
            laplacian = (
                -4 * gray
                + np.roll(gray, 1, axis=0) + np.roll(gray, -1, axis=0)
                + np.roll(gray, 1, axis=1) + np.roll(gray, -1, axis=1)
            )
        
        return float(np.var(laplacian))
