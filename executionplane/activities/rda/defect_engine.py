"""RDA 缺陷识别引擎 - 确定性 CV 表面缺陷检测。

Source: docs/INDUSTRIAL_AGENT_BENCHMARK.md
  - Conformal Segmentation (arXiv 2504.17721)：保守置信度，宁可复核不假合格
  - Hybrid Vision-Language (arXiv 2605.26533)：确定性检测 + LLM 仅出报告
  - 前沿共识：Level 1 传统 CV（零数据依赖）先行，后续可升级 YOLO/SAM

设计原则（与 IQA/MEA 一致）:
  - 确定性 CV 函数，相同输入相同输出
  - 无数据依赖
  - 保守路由：弱信号绝不判合格；裂纹等不允许缺陷一旦强检出即 NG
  - 逐缺陷置信度 + 整体 route_decision

精度策略（降低误检）:
  - 焊缝区(bead)掩膜：检测限定在焊缝加强高区域内，排除全图结构边
  - 裂纹必须暗(绝对灰度低)：结构边(母材边/焊缝面线)是亮的，不判为裂纹
  - 咬边/气孔/焊瘤均限定在 bead 邻域

检测对象（GB/T 19418 表面缺陷）: 气孔/裂纹/咬边/焊瘤

注意：单张 2D 照片的表面缺陷检测本质受限。本引擎提供确定性候选检测 +
保守置信度，不伪造精度。真正高召回需训练模型，属未来工作。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

# ── 检测参数 ──────────────────────────────────────────────────────
_POROSITY_MIN_AREA = 8
_POROSITY_MAX_AREA = 2000
_POROSITY_MIN_CIRCULARITY = 0.45
_SPATTER_MIN_AREA = 5
_SPATTER_MAX_AREA = 800
_CRACK_MIN_LENGTH = 25
_CRACK_DARK_MARGIN = 0.30   # 裂纹线灰度须低于 (mean - 0.30*std)
_BRIGHT_BEAD_PERCENTILE = 88  # bead 亮阈值百分位
_UNDERCUT_RATIO_MIN = 0.40    # 咬边暗谷比阈值
_UNDERCUT_DARK_ABS = 90       # 咬边暗谷绝对灰度上限


@dataclass
class DefectFinding:
    """单个缺陷检出。"""
    defect_type: str            # porosity / crack / undercut / spatter
    centroid_px: tuple[float, float]
    bbox_px: tuple[int, int, int, int]   # x, y, w, h
    area_px: float = 0.0
    length_px: float = 0.0
    depth_px: float = 0.0
    confidence: float = 0.0
    severity: str = "LOW"
    detail: str = ""


@dataclass
class DefectReport:
    """缺陷检测综合报告。"""
    defects: list[DefectFinding] = field(default_factory=list)
    defect_types: list[str] = field(default_factory=list)
    confidence: float = 0.0
    method: str = "deterministic_cv"
    notes: list[str] = field(default_factory=list)

    @property
    def has_crack(self) -> bool:
        return any(d.defect_type == "crack" for d in self.defects)

    @property
    def porosity_count(self) -> int:
        return sum(1 for d in self.defects if d.defect_type == "porosity")


class DefectEngine:
    """确定性表面缺陷检测引擎。"""

    def detect(self, image: NDArray[np.uint8], params: dict) -> DefectReport:
        report = DefectReport()
        if image is None or image.size == 0:
            report.notes.append("empty_image")
            return report

        gray = self._to_gray(image)
        if gray is None or gray.size == 0:
            report.notes.append("gray_convert_failed")
            return report

        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        bead_mask, bead_dilated = self._bead_mask(gray)

        report.defects.extend(self._detect_porosity(blurred, bead_dilated))
        report.defects.extend(self._detect_cracks(blurred, gray, bead_dilated))
        report.defects.extend(self._detect_undercut(blurred, bead_mask))
        report.defects.extend(self._detect_spatter(blurred, bead_dilated))

        report.defect_types = sorted({d.defect_type for d in report.defects})
        report.confidence = self._overall_confidence(report.defects, gray)
        return report

    # ── 焊缝区掩膜 ──────────────────────────────────────────────────

    def _bead_mask(self, gray: NDArray) -> tuple[NDArray, NDArray]:
        """焊缝加强高区域掩膜。

        取最亮像素构成连通域中面积最大者（焊缝加强高），排除细长结构边。
        bead_dilated 为其膨胀版（含焊趾邻域），用于限定缺陷搜索范围。
        """
        thr = int(np.percentile(gray, _BRIGHT_BEAD_PERCENTILE))
        thr = max(thr, int(gray.mean()))
        _, binary = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        if n <= 1:
            empty = np.zeros_like(gray, dtype=np.uint8)
            return empty, empty
        # 跳过背景(0)，取面积最大连通域
        largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        bead = np.where(labels == largest, 255, 0).astype(np.uint8)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
        bead_dilated = cv2.dilate(bead, kernel, iterations=1)
        return bead, bead_dilated

    # ── 气孔：bead 内暗色近圆斑点 ─────────────────────────────────

    def _detect_porosity(self, gray: NDArray, bead_dilated: NDArray) -> list[DefectFinding]:
        findings: list[DefectFinding] = []
        if bead_dilated.sum() == 0:
            return findings
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
            cv2.THRESH_BINARY_INV, blockSize=51, C=5,
        )
        binary = cv2.bitwise_and(binary, bead_dilated)
        n, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if not (_POROSITY_MIN_AREA <= area <= _POROSITY_MAX_AREA):
                continue
            x, y, w, h = (int(stats[i, j]) for j in range(4))
            peri = 2 * (w + h)
            circularity = (4 * np.pi * area) / (peri * peri) if peri > 0 else 0.0
            if circularity < _POROSITY_MIN_CIRCULARITY:
                continue
            conf = round(max(0.0, min(0.9, circularity)), 3)
            findings.append(DefectFinding(
                defect_type="porosity",
                centroid_px=(float(centroids[i, 0]), float(centroids[i, 1])),
                bbox_px=(x, y, w, h),
                area_px=float(area),
                confidence=conf,
                severity="LOW" if area < 40 else "MED",
                detail=f"area={area}px circularity={conf:.2f}",
            ))
        return findings

    # ── 裂纹：bead 内暗色细长线 ───────────────────────────────────

    def _detect_cracks(
        self, blurred: NDArray, gray: NDArray, bead_dilated: NDArray
    ) -> list[DefectFinding]:
        findings: list[DefectFinding] = []
        if bead_dilated.sum() == 0:
            return findings
        rect = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 3))
        blackhat = cv2.morphologyEx(blurred, cv2.MORPH_BLACKHAT, rect)
        blackhat = cv2.bitwise_and(blackhat, blackhat, mask=bead_dilated)
        _, binary = cv2.threshold(blackhat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        lines = cv2.HoughLinesP(
            binary, rho=1, theta=np.pi / 180, threshold=20,
            minLineLength=_CRACK_MIN_LENGTH, maxLineGap=8,
        )
        if lines is None:
            return findings
        mean_gray = float(gray.mean())
        std_gray = float(gray.std())
        dark_limit = mean_gray - _CRACK_DARK_MARGIN * std_gray
        for ln in lines:
            x1, y1, x2, y2 = map(float, ln[0])
            length = float(np.hypot(x2 - x1, y2 - y1))
            if length < _CRACK_MIN_LENGTH:
                continue
            line_med = self._line_median_intensity(gray, x1, y1, x2, y2)
            if line_med > dark_limit:
                continue  # 亮的结构边，非暗裂纹
            conf = round(max(0.0, min(0.9, (length - _CRACK_MIN_LENGTH) / 80.0)), 3)
            findings.append(DefectFinding(
                defect_type="crack",
                centroid_px=((x1 + x2) / 2.0, (y1 + y2) / 2.0),
                bbox_px=(int(min(x1, x2)), int(min(y1, y2)),
                         int(abs(x2 - x1)) + 1, int(abs(y2 - y1)) + 1),
                length_px=length,
                confidence=conf,
                severity="HIGH",
                detail=f"length={length:.0f}px line_gray={line_med:.0f}",
            ))
        return findings

    def _line_median_intensity(
        self, gray: NDArray, x1: float, y1: float, x2: float, y2: float
    ) -> float:
        h, w = gray.shape[:2]
        n = max(2, int(np.hypot(x2 - x1, y2 - y1)))
        xs = np.linspace(x1, x2, n).astype(int)
        ys = np.linspace(y1, y2, n).astype(int)
        xs = np.clip(xs, 0, w - 1)
        ys = np.clip(ys, 0, h - 1)
        return float(np.median(gray[ys, xs]))

    # ── 咬边：bead 边界暗谷 ───────────────────────────────────────

    def _detect_undercut(self, gray: NDArray, bead_mask: NDArray) -> list[DefectFinding]:
        """焊趾咬边代理：在 bead 边界(toe_band)内找连通暗斑(局部凹槽)。

        仅检测形成连通暗块(面积>=阈值)的局部凹槽，排除孤立暗像素/均匀边缘过渡。
        注：单张 2D 照片无法精确测咬边深度，此处为代理检测，置信度上限 0.6。
        """
        if bead_mask.sum() == 0:
            return []
        # 填充 bead 内部孔洞(气孔/裂纹形成的暗洞)，使 toe_band 仅含外缘环
        close_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))
        bead_filled = cv2.morphologyEx(bead_mask, cv2.MORPH_CLOSE, close_k)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        dilated = cv2.dilate(bead_filled, kernel, iterations=1)
        toe_band = cv2.subtract(dilated, bead_filled)
        if toe_band.sum() == 0:
            return []
        band_vals = gray[toe_band > 0]
        if band_vals.size == 0:
            return []
        band_mean = float(band_vals.mean())
        if band_mean <= 0:
            return []
        # 连通暗斑：toe_band 内低于 dark_abs 的像素聚类
        dark_mask = np.where((toe_band > 0) & (gray < _UNDERCUT_DARK_ABS), 255, 0).astype(np.uint8)
        n, labels, stats, centroids = cv2.connectedComponentsWithStats(dark_mask, connectivity=8)
        best = None
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if area < 10:  # 排除孤立噪声像素
                continue
            cx, cy = centroids[i]
            local_min = self._region_min_in_mask(gray, labels, i)
            depth_ratio = (band_mean - local_min) / band_mean
            if depth_ratio > _UNDERCUT_RATIO_MIN:
                if best is None or area > best[1]:
                    best = (i, area, (cx, cy), local_min, depth_ratio)
        if best is None:
            return []
        _, area, (cx, cy), valley_min, depth_ratio = best
        conf = round(max(0.0, min(0.6, depth_ratio)), 3)
        return [DefectFinding(
            defect_type="undercut",
            centroid_px=(float(cx), float(cy)),
            bbox_px=(int(cx) - 6, int(cy) - 6, 12, 12),
            depth_px=round(band_mean - valley_min, 2),
            confidence=conf,
            severity="MED",
            detail=f"dark_notch area={area}px ratio={depth_ratio:.2f} valley_gray={valley_min:.0f} proxy_depth_px={band_mean - valley_min:.1f}",
        )]

    def _region_min_in_mask(self, gray: NDArray, labels: NDArray, label_id: int) -> float:
        vals = gray[labels == label_id]
        return float(vals.min()) if vals.size else 0.0

    # ── 焊瘤：bead 内亮色凸起小斑 ─────────────────────────────────

    def _detect_spatter(self, gray: NDArray, bead_dilated: NDArray) -> list[DefectFinding]:
        findings: list[DefectFinding] = []
        if bead_dilated.sum() == 0:
            return findings
        bright_thr = int(np.percentile(gray[bead_dilated > 0], 96))
        _, binary = cv2.threshold(gray, bright_thr, 255, cv2.THRESH_BINARY)
        binary = cv2.bitwise_and(binary, bead_dilated)
        n, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if not (_SPATTER_MIN_AREA <= area <= _SPATTER_MAX_AREA):
                continue
            x, y, w, h = (int(stats[i, j]) for j in range(4))
            conf = round(max(0.0, min(0.5, area / 400.0)), 3)
            findings.append(DefectFinding(
                defect_type="spatter",
                centroid_px=(float(centroids[i, 0]), float(centroids[i, 1])),
                bbox_px=(x, y, w, h),
                area_px=float(area),
                confidence=conf,
                severity="LOW",
                detail=f"bright_blob area={area}px",
            ))
        return findings

    # ── 置信度 ────────────────────────────────────────────────────

    def _overall_confidence(self, defects: list[DefectFinding], gray: NDArray) -> float:
        if not defects:
            std = float(np.std(gray))
            contrast_score = min(1.0, std / 60.0)
            return round(max(0.3, min(0.7, contrast_score)), 3)
        avg = float(np.mean([d.confidence for d in defects]))
        return round(max(0.0, min(1.0, avg)), 3)

    def _to_gray(self, image: NDArray) -> NDArray | None:
        try:
            if image.ndim == 2:
                return image
            if image.ndim == 3:
                if image.shape[2] == 4:
                    image = cv2.cvtColor(image, cv2.COLOR_RGBA2RGB)
                return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        except cv2.error as e:
            logger.warning("gray convert failed: %s", e)
            return None
        return None
