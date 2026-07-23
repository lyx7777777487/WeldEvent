"""MEA 几何测量引擎 - 确定性 CV 几何尺寸测量。

Source: docs/INDUSTRIAL_AGENT_BENCHMARK.md 前沿共识
  - AgentsCAD (arXiv 2607.02448): 确定性几何检测 + LLM 仅做推理
  - 前沿共识：测量用确定性算法，LLM 只负责编排/报告，不负责测量本身

设计原则（与 IQA 规则层一致）:
  - 确定性 CV 函数（Canny + Hough 直线拟合），相同输入相同输出
  - 无数据依赖（Level 1，零训练样本）- 后续可升级 YOLO/SAM
  - 诚实置信度：边缘检测质量决定置信度，低置信走 MARGINAL 而非假合格
  - 像素级测量恒可得；毫米换算仅在提供标定（pixels_per_mm）时给出

测量对象（角焊缝/T 型接头）:
  - 焊脚 K1 / K2（两条焊脚长度，沿母材表面从理论根部到焊趾）
  - 焊喉（焊缝截面理论等腰三角形的高）
  - 两焊脚差 |K1 - K2|

标定来源（params，按优先级）:
  1. pixels_per_mm          - 直接标定
  2. plate_thickness_mm + plate_thickness_px - 用已知板厚做参考尺
  无标定时 -> calibrated=False，仅返回像素值，activity 走 MARGINAL

注意：单张 2D 照片做精确几何测量本质受限（无深度、无标定则无 mm）。
本引擎提供确定性像素测量 + 可选 mm 换算 + 诚实置信度，
不伪造精度。真正亚毫米级测量需 3D/结构光，属未来工作。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)


# 板夹角判定：角焊缝理想 90°，允许 ±25° 偏差仍认作有效 T/角接头
_PLATE_ANGLE_DEG_MIN = 65.0
_PLATE_ANGLE_DEG_MAX = 115.0
_HOUGH_THRESHOLD = 40
_HOUGH_MIN_LINE_LENGTH = 30
_HOUGH_MAX_LINE_GAP = 10
_TOE_SCAN_MAX_PX = 400


@dataclass
class GeometryMeasurement:
    """单次几何测量结果。所有 *_mm 字段在未标定时为 None。"""

    weld_leg_1_px: float = 0.0
    weld_leg_2_px: float = 0.0
    throat_px: float = 0.0
    leg_difference_px: float = 0.0

    weld_leg_1_mm: float | None = None
    weld_leg_2_mm: float | None = None
    throat_mm: float | None = None
    leg_difference_mm: float | None = None

    plate_angle_deg: float = 0.0
    calibrated: bool = False
    confidence: float = 0.0
    method: str = "hough_line_fit"
    root_point_px: tuple[float, float] | None = None
    toe_points_px: list[tuple[float, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _line_angle_deg(x1: float, y1: float, x2: float, y2: float) -> float:
    """直线方向角（度），0=水平，90=垂直，范围 [0,180)。"""
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return 0.0
    ang = float(np.degrees(np.arctan2(dy, dx)))
    if ang < 0:
        ang += 180.0
    return ang


def _line_intersection(
    l1: tuple[float, float, float, float],
    l2: tuple[float, float, float, float],
) -> tuple[float, float] | None:
    """两线段所在直线的交点。平行返回 None。"""
    (x1, y1, x2, y2) = l1
    (x3, y3, x4, y4) = l2
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-9:
        return None
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / denom
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / denom
    return float(px), float(py)


def _resolve_scale_mm(params: dict) -> float | None:
    """解析像素->毫米标定。返回 mm/px（pixels_per_mm 的倒数）或 None。"""
    ppm = params.get("pixels_per_mm")
    if ppm:
        try:
            v = float(ppm)
            if v > 0:
                return 1.0 / v
        except (TypeError, ValueError):
            pass
    thick_mm = params.get("plate_thickness_mm")
    thick_px = params.get("plate_thickness_px")
    if thick_mm and thick_px:
        try:
            mm, px = float(thick_mm), float(thick_px)
            if mm > 0 and px > 0:
                return mm / px
        except (TypeError, ValueError):
            pass
    return None


class GeometryEngine:
    """确定性角焊缝几何测量引擎。

    算法:
      1. 灰度 + 高斯模糊 + Canny 边缘
      2. HoughLinesP 提取直线段，按方向角分簇（近水平 / 近垂直）
      3. 各取最长代表线作为两母材面，求交点 = 理论根部
      4. 沿各板面方向从根部扫描焊缝区域边缘密度，取最大延伸 = 焊趾距离 = 焊脚(px)
      5. 夹角、线拟合残差、扫描清晰度 -> 置信度
      6. 可选 px->mm 换算
    """

    def measure(self, image: NDArray[np.uint8], params: dict) -> GeometryMeasurement:
        result = GeometryMeasurement()
        if image is None or image.size == 0:
            result.notes.append("empty_image")
            return result

        gray = self._to_gray(image)
        if gray is None or gray.size == 0:
            result.notes.append("gray_convert_failed")
            return result

        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 50, 150)

        lines = cv2.HoughLinesP(
            edges, rho=1, theta=np.pi / 180,
            threshold=_HOUGH_THRESHOLD,
            minLineLength=_HOUGH_MIN_LINE_LENGTH,
            maxLineGap=_HOUGH_MAX_LINE_GAP,
        )
        if lines is None or len(lines) == 0:
            result.notes.append("no_lines_detected")
            return result

        segments = [tuple(map(float, ln[0])) for ln in lines]
        h_line = self._strongest_by_orientation(segments, near_horizontal=True)
        v_line = self._strongest_by_orientation(segments, near_horizontal=False)

        if h_line is None or v_line is None:
            result.notes.append("missing_plate_edge")
            return result

        angle = self._angle_between_lines(h_line, v_line)
        result.plate_angle_deg = angle

        root = _line_intersection(h_line, v_line)
        if root is None:
            result.notes.append("plate_edges_parallel_no_intersection")
            return result
        result.root_point_px = root

        # 焊脚 = 根部到焊趾的距离。焊趾 = 焊缝面线（三角形斜边）与两板边的交点。
        # 优先用几何交点法（精确）；找不到焊缝面线时回退到边缘带扫描（粗略）。
        face_line = self._find_weld_face(segments, h_line, v_line, root, edges)
        face_found = face_line is not None
        if face_found:
            leg1_px, leg2_px = self._legs_from_face(face_line, h_line, v_line, root)
            result.notes.append("legs_from_weld_face_intersection")
        else:
            # 焊缝面线未检出：带扫描不可靠(易沿板边延伸过估)，置零 + 低置信，路由复核
            leg1_px = 0.0
            leg2_px = 0.0
            result.notes.append("weld_face_not_found_measurement_unavailable")
        result.weld_leg_1_px = leg1_px
        result.weld_leg_2_px = leg2_px
        result.leg_difference_px = abs(leg1_px - leg2_px)
        if _PLATE_ANGLE_DEG_MIN <= angle <= _PLATE_ANGLE_DEG_MAX:
            half = np.radians(angle / 2.0)
            result.throat_px = float(min(leg1_px, leg2_px)) * float(np.sin(half if half > 0 else np.pi / 4))
        else:
            result.throat_px = float(min(leg1_px, leg2_px)) * 0.7071

        mm_per_px = _resolve_scale_mm(params)
        if mm_per_px is not None and mm_per_px > 0:
            result.calibrated = True
            result.weld_leg_1_mm = round(leg1_px * mm_per_px, 3)
            result.weld_leg_2_mm = round(leg2_px * mm_per_px, 3)
            result.throat_mm = round(result.throat_px * mm_per_px, 3)
            result.leg_difference_mm = round(result.leg_difference_px * mm_per_px, 3)
        else:
            result.notes.append("uncalibrated_px_only")

        result.confidence = self._confidence(angle, len(segments), leg1_px, leg2_px, mm_per_px, face_found)
        result.toe_points_px = self._toe_points(h_line, v_line, root, leg1_px, leg2_px)
        return result


    def _find_weld_face(
        self,
        segments: list[tuple[float, float, float, float]],
        h_line: tuple[float, float, float, float],
        v_line: tuple[float, float, float, float],
        root: tuple[float, float],
        edges: NDArray,
    ) -> tuple[float, float, float, float] | None:
        """找焊缝面线（三角形斜边）：一条近似 45° 的对角线段，且其两端在两板边附近。

        选择标准：方向角既不近水平也不近垂直（即对角），且离根部较近（焊缝区域）。
        先用主 Hough 段；若未命中，再跑一次更敏感的 Hough（低阈值/短最小长度）以捕获小焊缝面线。
        """
        best = None
        best_score = -1.0
        rx, ry = root
        # 敏感二次 Hough：阈值更低、最小线长更短，专为小焊缝面线
        sens = cv2.HoughLinesP(
            edges, rho=1, theta=np.pi / 180, threshold=18,
            minLineLength=15, maxLineGap=6,
        )
        cand = list(segments)
        if sens is not None:
            cand = cand + [tuple(map(float, ln[0])) for ln in sens]
        for seg in cand:
            x1, y1, x2, y2 = seg
            ang = _line_angle_deg(x1, y1, x2, y2)
            # 排除近水平(<30)与近垂直(>150 或 >60 且 <120 视情况)。
            # 对角线：方向角落在 [30,60] 或 [120,150]
            is_diagonal = (30.0 <= ang <= 60.0) or (120.0 <= ang <= 150.0)
            if not is_diagonal:
                continue
            mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            dist_root = float(np.hypot(mx - rx, my - ry))
            # 越靠近根部（焊缝）且越长越优先，但要求在合理焊缝尺寸内
            if dist_root < 1.0 or dist_root > 600:
                continue
            length = float(np.hypot(x2 - x1, y2 - y1))
            # 分数：长线 + 离根部适中（太近可能是噪声）
            score = length / (1.0 + abs(dist_root - 60.0) / 60.0)
            if score > best_score:
                best_score = score
                best = seg
        return best

    def _legs_from_face(
        self,
        face_line: tuple[float, float, float, float],
        h_line: tuple[float, float, float, float],
        v_line: tuple[float, float, float, float],
        root: tuple[float, float],
    ) -> tuple[float, float]:
        """焊脚 = 根部到（焊缝面线 ∩ 板边）交点的距离。"""
        toe1 = _line_intersection(face_line, h_line)
        toe2 = _line_intersection(face_line, v_line)
        leg1 = float(np.hypot(toe1[0] - root[0], toe1[1] - root[1])) if toe1 else 0.0
        leg2 = float(np.hypot(toe2[0] - root[0], toe2[1] - root[1])) if toe2 else 0.0
        return leg1, leg2

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

    def _strongest_by_orientation(
        self,
        segments: list[tuple[float, float, float, float]],
        near_horizontal: bool,
    ) -> tuple[float, float, float, float] | None:
        best = None
        best_score = -1.0
        for seg in segments:
            x1, y1, x2, y2 = seg
            ang = _line_angle_deg(x1, y1, x2, y2)
            if near_horizontal:
                horiz = min(ang, 180.0 - ang)
                orient = 1.0 - (horiz / 90.0)
            else:
                vert = abs(ang - 90.0)
                orient = 1.0 - (vert / 90.0)
            if orient < 0.5:
                continue
            length = float(np.hypot(x2 - x1, y2 - y1))
            score = orient * length
            if score > best_score:
                best_score = score
                best = seg
        return best

    def _angle_between_lines(
        self,
        l1: tuple[float, float, float, float],
        l2: tuple[float, float, float, float],
    ) -> float:
        a1 = _line_angle_deg(*l1)
        a2 = _line_angle_deg(*l2)
        d = abs(a1 - a2)
        d = min(d, 180.0 - d)
        return round(d, 2)

    def _scan_leg(
        self,
        edges: NDArray,
        plate_line: tuple[float, float, float, float],
        root: tuple[float, float],
    ) -> float:
        x1, y1, x2, y2 = plate_line
        dx, dy = x2 - x1, y2 - y1
        norm = float(np.hypot(dx, dy))
        if norm < 1e-6:
            return 0.0
        ux, uy = dx / norm, dy / norm
        h, w = edges.shape[:2]
        band = max(8, int(0.04 * min(h, w)))
        max_extent = 0.0
        for step in range(1, _TOE_SCAN_MAX_PX):
            cx = root[0] + ux * step
            cy = root[1] + uy * step
            if not (0 <= cx < w and 0 <= cy < h):
                break
            if self._band_has_edge(edges, cx, cy, -uy, ux, band):
                max_extent = float(step)
        return max_extent

    def _band_has_edge(
        self,
        edges: NDArray,
        cx: float,
        cy: float,
        nx: float,
        ny: float,
        band: int,
    ) -> bool:
        h, w = edges.shape[:2]
        for r in range(-band, band + 1, 2):
            px = int(cx + nx * r)
            py = int(cy + ny * r)
            if 0 <= px < w and 0 <= py < h and edges[py, px] > 0:
                return True
        return False

    def _confidence(
        self,
        angle: float,
        n_lines: int,
        leg1: float,
        leg2: float,
        mm_per_px: float | None,
        face_found: bool = True,
    ) -> float:
        # 焊缝面线未检出 -> 无法可靠测量，置信度封顶 0.4（低于 OK 阈值，路由复核）
        if not face_found:
            return 0.35
        score = 0.0
        angle_score = max(0.0, 1.0 - abs(angle - 90.0) / 45.0)
        score += 0.4 * angle_score
        line_score = min(1.0, n_lines / 12.0)
        score += 0.2 * line_score
        leg_score = 1.0 if (leg1 > 5.0 and leg2 > 5.0) else 0.2
        score += 0.25 * leg_score
        score += 0.15 if mm_per_px else 0.0
        return round(max(0.0, min(1.0, score)), 3)

    def _toe_points(
        self,
        h_line: tuple[float, float, float, float],
        v_line: tuple[float, float, float, float],
        root: tuple[float, float],
        leg1: float,
        leg2: float,
    ) -> list[tuple[float, float]]:
        pts = []
        for line, leg in ((h_line, leg1), (v_line, leg2)):
            x1, y1, x2, y2 = line
            dx, dy = x2 - x1, y2 - y1
            norm = float(np.hypot(dx, dy))
            if norm < 1e-6 or leg <= 0:
                continue
            pts.append((root[0] + (dx / norm) * leg, root[1] + (dy / norm) * leg))
        return pts
