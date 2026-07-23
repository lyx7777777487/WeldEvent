"""统计与 CV 特征分析器 — 2.1 传统可量化指标。

纯计算模块，使用 OpenCV + numpy，不依赖 LLM。
覆盖指标：
  - 图片尺寸分布 / 高宽比分布
  - 灰度分布（32-bin 像素直方图）/ 亮度 / 对比度
  - 清晰度（Laplacian 方差）/ 模糊样本
  - 噪声估计
  - 重复样本检测（感知哈希）
  - 异常样本检测（过暗/过亮/模糊）
  - 采集来源分布（如有元数据）
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Sequence

try:  # OpenCV/numpy 是可选运行时依赖；缺失时工具应降级而不是导入失败。
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    from numpy.typing import NDArray  # type: ignore
except Exception:  # pragma: no cover - 取决于部署环境
    cv2 = None
    np = None
    NDArray = Any  # type: ignore

from cognitiveplane.control.data_understanding.models import (
    DatasetCVReport,
    ImageCVStats,
)

logger = logging.getLogger(__name__)


# 阈值常量（可配置）
BLURRY_SHARPNESS_THRESHOLD = 50.0    # Laplacian 方差低于此值判为模糊
DARK_BRIGHTNESS_THRESHOLD = 30.0     # 灰度均值低于此值判为过暗
BRIGHT_BRIGHTNESS_THRESHOLD = 220.0  # 灰度均值高于此值判为过亮
DUPLICATE_HASH_THRESHOLD = 5         # 感知哈士距离 <= 此值判为重复
MAX_IMAGES_FOR_DUPLICATE_CHECK = 500 # 重复检测的图片上限（防止 OOM）


class StatisticalCVAnalyzer:
    """统计与 CV 特征分析器。

    用法：
        analyzer = StatisticalCVAnalyzer()
        report = analyzer.analyze(Path("/data/weld_images"))
        print(report.blurry_count)
    """

    def __init__(
        self,
        blurry_threshold: float = BLURRY_SHARPNESS_THRESHOLD,
        dark_threshold: float = DARK_BRIGHTNESS_THRESHOLD,
        bright_threshold: float = BRIGHT_BRIGHTNESS_THRESHOLD,
    ) -> None:
        self._blurry_threshold = blurry_threshold
        self._dark_threshold = dark_threshold
        self._bright_threshold = bright_threshold

    def analyze(self, image_dir: Path | str) -> DatasetCVReport:
        """分析目录下所有图片，返回数据集级 CV 报告。"""
        image_dir = Path(image_dir)
        if not image_dir.is_dir():
            return DatasetCVReport(total_images=0, valid_images=0)
        if cv2 is None or np is None:
            logger.warning("[DSA] OpenCV/numpy unavailable; returning file-count-only CV report")
            image_files = sorted(self._find_images(image_dir))
            return DatasetCVReport(total_images=len(image_files), valid_images=0)

        image_files = sorted(self._find_images(image_dir))
        if not image_files:
            return DatasetCVReport(total_images=0, valid_images=0)

        logger.info("[DSA] Analyzing %d images in %s", len(image_files), image_dir)

        # ── 逐图分析 ──
        per_image: list[ImageCVStats] = []
        for img_path in image_files:
            stats = self._analyze_single(img_path)
            if stats is not None:
                per_image.append(stats)

        # ── 重复检测 ──
        if len(per_image) <= MAX_IMAGES_FOR_DUPLICATE_CHECK:
            self._detect_duplicates(per_image, image_dir)

        # ── 聚合统计 ──
        report = self._aggregate(per_image)
        logger.info(
            "[DSA] Done: %d valid, %d blurry, %d dark, %d bright, %d duplicates",
            report.valid_images, report.blurry_count,
            report.dark_count, report.bright_count, report.duplicate_count,
        )
        return report

    def analyze_labeled(
        self,
        image_dir: Path | str,
        labels: dict[str, str] | None = None,
        annotations: dict[str, Any] | None = None,
    ) -> DatasetCVReport:
        """分析有标注数据的 CV 特征（前景/背景分离）。

        Args:
            image_dir: 图片目录
            labels: {filename: label} 映射，用于按标签分组统计
            annotations: 结构化标注，支持 bbox/mask/OCR 等字段
        """
        report = self.analyze(image_dir)
        if labels and report.per_image:
            # 按标签分组的质量统计
            for stats in report.per_image:
                label = labels.get(stats.filename, "")
                if label:
                    stats.filename = f"{stats.filename} [{label}]"
        if annotations and cv2 is not None and np is not None:
            self._analyze_foreground_background(Path(image_dir), report, annotations)
        return report

    # ------------------------------------------------------------------
    # 单图分析
    # ------------------------------------------------------------------

    def _analyze_single(self, img_path: Path) -> ImageCVStats | None:
        """分析单张图。"""
        try:
            img = cv2.imread(str(img_path))
            if img is None:
                logger.warning("[DSA] Failed to read: %s", img_path.name)
                return None
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            h, w = img.shape[:2]

            # 灰度统计
            mean_brightness = float(np.mean(gray))
            std_brightness = float(np.std(gray))
            # RMS 对比度
            contrast = float(np.sqrt(np.mean(gray.astype(np.float64) ** 2)))
            # 清晰度（Laplacian 方差）
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            # 噪声估计（高频能量占比）
            noise = self._estimate_noise(gray)
            # 灰度直方图（32 bin，归一化为比例）
            hist = cv2.calcHist([gray], [0], None, [32], [0, 256]).flatten()
            hist_total = float(hist.sum())
            grayscale_hist = (hist / hist_total).tolist() if hist_total > 0 else [0.0] * 32

            stats = ImageCVStats(
                filename=img_path.name,
                source_id=self._infer_source_id(img_path),
                width=w,
                height=h,
                aspect_ratio=round(w / h, 3) if h > 0 else 0.0,
                mean_brightness=round(mean_brightness, 2),
                std_brightness=round(std_brightness, 2),
                contrast=round(contrast, 2),
                sharpness=round(sharpness, 2),
                noise_level=round(noise, 4),
                grayscale_histogram=[round(v, 4) for v in grayscale_hist],
                is_blurry=sharpness < self._blurry_threshold,
                is_dark=mean_brightness < self._dark_threshold,
                is_bright=mean_brightness > self._bright_threshold,
                is_anomaly=(
                    sharpness < self._blurry_threshold
                    or mean_brightness < self._dark_threshold
                    or mean_brightness > self._bright_threshold
                ),
                acquisition_view=self._classify_view(w, h),
            )
            return stats
        except Exception as e:
            logger.warning("[DSA] Error analyzing %s: %s", img_path.name, e)
            return None

    @staticmethod
    def _estimate_noise(gray: NDArray[np.uint8]) -> float:
        """估计噪声水平：Laplacian 后高频能量占比。"""
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        noise = float(np.std(laplacian))
        return noise / 255.0

    # ------------------------------------------------------------------
    # 重复检测
    # ------------------------------------------------------------------

    def _detect_duplicates(
        self,
        per_image: list[ImageCVStats],
        image_dir: Path,
    ) -> None:
        """用感知哈希检测重复样本。"""
        hashes: dict[str, str] = {}  # hash_hex -> first filename
        for stats in per_image:
            img_path = image_dir / stats.filename
            try:
                img = cv2.imread(str(img_path))
                if img is None:
                    continue
                # 缩小到 8x8 灰度图计算均值哈希
                small = cv2.resize(img, (8, 8))
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                avg = np.mean(gray)
                bits = (gray > avg).flatten()
                hash_hex = "".join("1" if b else "0" for b in bits)

                # 汉明距离比较
                for existing_hash, existing_name in hashes.items():
                    distance = sum(a != b for a, b in zip(hash_hex, existing_hash))
                    if distance <= DUPLICATE_HASH_THRESHOLD:
                        stats.is_duplicate = True
                        stats.duplicate_of = existing_name
                        break
                if not stats.is_duplicate:
                    hashes[hash_hex] = stats.filename
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 聚合统计
    # ------------------------------------------------------------------

    def _aggregate(self, per_image: list[ImageCVStats]) -> DatasetCVReport:
        """将逐图统计聚合为数据集级报告。"""
        if not per_image:
            return DatasetCVReport()

        # 尺寸分布
        width_dist: dict[str, int] = {}
        height_dist: dict[str, int] = {}
        aspect_dist: dict[str, int] = {}
        source_dist: dict[str, int] = {}
        view_dist: dict[str, int] = {}
        for s in per_image:
            w_key = str(s.width)
            h_key = str(s.height)
            width_dist[w_key] = width_dist.get(w_key, 0) + 1
            height_dist[h_key] = height_dist.get(h_key, 0) + 1
            # 高宽比归类
            ar = s.aspect_ratio
            if 1.2 <= ar <= 1.4:
                ar_key = "4:3"
            elif 1.7 <= ar <= 1.9:
                ar_key = "16:9"
            elif 0.7 <= ar <= 0.8:
                ar_key = "3:4"
            elif 1.0 <= ar <= 1.1:
                ar_key = "1:1"
            else:
                ar_key = f"other({ar:.2f})"
            aspect_dist[ar_key] = aspect_dist.get(ar_key, 0) + 1
            if s.source_id:
                source_dist[s.source_id] = source_dist.get(s.source_id, 0) + 1
            if s.acquisition_view:
                view_dist[s.acquisition_view] = view_dist.get(s.acquisition_view, 0) + 1

        # 质量分布统计
        brightness_vals = [s.mean_brightness for s in per_image]
        contrast_vals = [s.contrast for s in per_image]
        sharpness_vals = [s.sharpness for s in per_image]
        noise_vals = [s.noise_level for s in per_image]

        # 灰度直方图聚合（跨图平均，输出 bin 区间 -> 平均占比）
        grayscale_hist_agg: dict[str, float] = {}
        hist_lists = [s.grayscale_histogram for s in per_image if s.grayscale_histogram]
        if hist_lists:
            num_bins = len(hist_lists[0])
            for i in range(num_bins):
                bin_values = [h[i] for h in hist_lists if i < len(h)]
                avg = sum(bin_values) / len(bin_values)
                lo = i * 8
                hi = min((i + 1) * 8 - 1, 255)
                grayscale_hist_agg[f"{lo}-{hi}"] = round(avg, 4)

        # 异常样本
        blurry_files = [s.filename for s in per_image if s.is_blurry]
        dark_files = [s.filename for s in per_image if s.is_dark]
        bright_files = [s.filename for s in per_image if s.is_bright]
        anomaly_files = blurry_files + dark_files + bright_files

        # 重复样本组
        duplicate_groups: list[list[str]] = []
        dup_map: dict[str, list[str]] = {}
        for s in per_image:
            if s.is_duplicate and s.duplicate_of:
                dup_map.setdefault(s.duplicate_of, [s.duplicate_of]).append(s.filename)
        duplicate_groups = list(dup_map.values())

        return DatasetCVReport(
            total_images=len(per_image),
            valid_images=len(per_image),
            width_distribution=width_dist,
            height_distribution=height_dist,
            aspect_ratio_distribution=aspect_dist,
            brightness_stats=self._percentile_stats(brightness_vals),
            contrast_stats=self._percentile_stats(contrast_vals),
            sharpness_stats=self._percentile_stats(sharpness_vals),
            noise_stats=self._percentile_stats(noise_vals),
            grayscale_histogram=grayscale_hist_agg,
            blurry_count=len(blurry_files),
            dark_count=len(dark_files),
            bright_count=len(bright_files),
            duplicate_count=len([s for s in per_image if s.is_duplicate]),
            anomaly_files=anomaly_files,
            blurry_files=blurry_files,
            duplicate_groups=duplicate_groups,
            source_distribution=source_dist,
            acquisition_view_distribution=view_dist,
            per_image=per_image,
        )

    def _analyze_foreground_background(
        self,
        image_dir: Path,
        report: DatasetCVReport,
        annotations: dict[str, Any],
    ) -> None:
        """基于 bbox 标注做前景/背景 CV 特征分析。

        支持最常见结构：
          {"img.jpg": [{"bbox": [x, y, w, h], "label": "defect"}]}
          {"img.jpg": {"bboxes": [...], "label": "..."}}
        mask/OCR 的质量统计由 orchestrator 负责，这里聚焦前景/背景像素。
        """
        fg_brightness: list[float] = []
        bg_brightness: list[float] = []
        contrast_delta: dict[str, float] = {}

        for filename, ann in annotations.items():
            img_path = image_dir / filename
            img = cv2.imread(str(img_path)) if cv2 is not None else None
            if img is None:
                continue
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            h, w = gray.shape[:2]
            boxes = self._extract_bboxes(ann)
            for box in boxes:
                x, y, bw, bh = self._normalize_bbox(box, w, h)
                if bw <= 0 or bh <= 0:
                    continue
                mask = np.zeros((h, w), dtype=np.uint8)
                mask[y:y + bh, x:x + bw] = 1
                fg = gray[mask == 1]
                bg = gray[mask == 0]
                if len(fg) == 0 or len(bg) == 0:
                    continue
                fg_mean = float(np.mean(fg))
                bg_mean = float(np.mean(bg))
                fg_brightness.append(fg_mean)
                bg_brightness.append(bg_mean)
                contrast_delta[filename] = round(abs(fg_mean - bg_mean), 2)

        report.foreground_stats["brightness"] = self._percentile_stats(fg_brightness)
        report.background_stats["brightness"] = self._percentile_stats(bg_brightness)
        report.foreground_background_contrast = contrast_delta

    @staticmethod
    def _extract_bboxes(annotation: Any) -> list[Any]:
        if isinstance(annotation, dict):
            if "bbox" in annotation:
                return [annotation["bbox"]]
            for key in ("bboxes", "boxes", "annotations", "regions"):
                value = annotation.get(key)
                if isinstance(value, list):
                    return [
                        item.get("bbox", item)
                        for item in value
                        if isinstance(item, (dict, list, tuple))
                    ]
        if isinstance(annotation, list):
            return [
                item.get("bbox", item)
                for item in annotation
                if isinstance(item, (dict, list, tuple))
            ]
        return []

    @staticmethod
    def _normalize_bbox(box: Any, width: int, height: int) -> tuple[int, int, int, int]:
        """兼容 [x,y,w,h] / [x1,y1,x2,y2]，并裁剪到图像范围。"""
        if not isinstance(box, (list, tuple)) or len(box) < 4:
            return (0, 0, 0, 0)
        x, y, a, b = [float(v) for v in box[:4]]
        # 如果第三/四个值看起来像右下角坐标，则转为宽高。
        bw = a - x if a > x and a <= width else a
        bh = b - y if b > y and b <= height else b
        x_i = max(0, min(width - 1, int(round(x))))
        y_i = max(0, min(height - 1, int(round(y))))
        bw_i = max(0, min(width - x_i, int(round(bw))))
        bh_i = max(0, min(height - y_i, int(round(bh))))
        return x_i, y_i, bw_i, bh_i

    @staticmethod
    def _percentile_stats(values: Sequence[float]) -> dict[str, float]:
        """计算均值/标准差/最小/最大/分位数。"""
        if not values or np is None:
            return {}
        arr = np.array(values)
        return {
            "mean": round(float(np.mean(arr)), 2),
            "std": round(float(np.std(arr)), 2),
            "min": round(float(np.min(arr)), 2),
            "max": round(float(np.max(arr)), 2),
            "p25": round(float(np.percentile(arr, 25)), 2),
            "p50": round(float(np.percentile(arr, 50)), 2),
            "p75": round(float(np.percentile(arr, 75)), 2),
        }

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _find_images(directory: Path) -> list[Path]:
        """查找目录下的图片文件。"""
        extensions = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}
        return [
            p for p in directory.rglob("*")
            if p.suffix.lower() in extensions and p.is_file()
        ]

    @staticmethod
    def _infer_source_id(img_path: Path) -> str:
        """从上级目录粗略推断来源；真实部署可替换为元数据读取。"""
        parent = img_path.parent.name
        return "" if parent in ("", ".") else parent

    @staticmethod
    def _classify_view(width: int, height: int) -> str:
        if width <= 0 or height <= 0:
            return "unknown"
        ratio = width / height
        if 0.95 <= ratio <= 1.05:
            return "square"
        if ratio > 1.05:
            return "landscape"
        if ratio < 0.95:
            return "portrait"
        return "other"
