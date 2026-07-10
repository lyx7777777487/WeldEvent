"""数据理解编排器 — 有标注/无标注两条管线的协调。

无标注数据管线：
  1. 统计和 CV 特征分析
  2. 模型推理（已有业务模型/SAM 类模型给定 prompt 推理）
  3. 多模态模型理解（在图上可视化后进一步拆解）

有标注数据管线：
  1. 标签统计
  2. 标签 + 图的 CV 特征分析（前景和背景）
  3. 多模态模型理解（在图上可视化后进一步拆解）
"""

from __future__ import annotations

import logging
import inspect
from pathlib import Path
from typing import Any, TYPE_CHECKING

from cognitiveplane.control.data_understanding.models import (
    DataKind,
    DataUnderstandingReport,
    LabelStats,
    ModelInferenceResult,
)
from cognitiveplane.control.data_understanding.statistical_analyzer import (
    StatisticalCVAnalyzer,
)
from cognitiveplane.control.data_understanding.semantic_analyzer import (
    MultimodalSemanticAnalyzer,
)

if TYPE_CHECKING:
    from cognitiveplane.capability.ports import LLMProvider

logger = logging.getLogger(__name__)


class DataUnderstandingOrchestrator:
    """数据理解编排器 — 根据数据类型自动选择管线。

    用法：
        orchestrator = DataUnderstandingOrchestrator(llm_provider)
        report = await orchestrator.analyze(
            image_dir="/data/weld_images",
            data_kind=DataKind.UNLABELED,
            labels={"img1.jpg": "气孔"},  # 有标注时传
        )
        print(report.to_dict())
    """

    def __init__(
        self,
        llm_provider: "LLMProvider | None" = None,
        cv_analyzer: StatisticalCVAnalyzer | None = None,
        semantic_analyzer: MultimodalSemanticAnalyzer | None = None,
    ) -> None:
        self._llm = llm_provider
        self._cv_analyzer = cv_analyzer or StatisticalCVAnalyzer()
        self._semantic_analyzer = semantic_analyzer or MultimodalSemanticAnalyzer(llm_provider)

    async def analyze(
        self,
        image_dir: Path | str,
        data_kind: DataKind = DataKind.UNLABELED,
        labels: dict[str, str] | None = None,
        annotations: dict[str, Any] | None = None,
        depth: str = "full",
        model_inference_fn: Any | None = None,
        model_inference: ModelInferenceResult | dict[str, Any] | None = None,
    ) -> DataUnderstandingReport:
        """执行数据理解管线。

        Args:
            image_dir: 图片目录路径
            data_kind: 数据类型（有标注/无标注）
            labels: 标签映射 {filename: label}（有标注数据必传）
            annotations: 结构化标注 {filename: annotation}，支持 bbox/mask/OCR
            depth: 分析深度 "cv_only" / "cv+model" / "full"
            model_inference_fn: 模型推理回调函数 (image_dir) -> ModelInferenceResult
                                用于无标注数据的第二步模型推理
            model_inference: 外部传入的已有模型/SAM 推理结果

        Returns:
            DataUnderstandingReport 综合报告
        """
        image_dir = Path(image_dir)
        report = DataUnderstandingReport(
            data_kind=data_kind,
            dataset_name=image_dir.name,
            analysis_depth=depth,
        )

        if data_kind == DataKind.UNLABELED:
            await self._run_unlabeled_pipeline(
                image_dir, report, depth, model_inference_fn, model_inference
            )
        else:
            await self._run_labeled_pipeline(image_dir, report, depth, labels, annotations)

        # ── 生成总结和建议 ──
        self._generate_summary(report)
        return report

    # ------------------------------------------------------------------
    # 无标注数据管线
    # ------------------------------------------------------------------

    async def _run_unlabeled_pipeline(
        self,
        image_dir: Path,
        report: DataUnderstandingReport,
        depth: str,
        model_inference_fn: Any | None,
        model_inference: ModelInferenceResult | dict[str, Any] | None,
    ) -> None:
        """无标注数据管线：CV 统计 → 模型推理 → 多模态理解。"""
        # Step 1: 统计和 CV 特征分析
        logger.info("[DSA] Unlabeled pipeline step 1: CV statistical analysis")
        try:
            cv_report = self._cv_analyzer.analyze(image_dir)
            report.cv_report = cv_report
        except Exception as e:
            logger.warning("[DSA] CV analysis failed: %s", e)
            report.errors.append(f"CV 分析失败: {e}")

        if depth == "cv_only":
            return

        # Step 2: 模型推理（已有业务模型 / SAM 类模型）
        if model_inference is not None:
            report.model_inference = self._coerce_model_inference(model_inference)
        elif model_inference_fn is not None:
            logger.info("[DSA] Unlabeled pipeline step 2: Model inference")
            try:
                inference_result = model_inference_fn(image_dir)
                if inspect.isawaitable(inference_result):
                    inference_result = await inference_result
                if isinstance(inference_result, ModelInferenceResult):
                    report.model_inference = inference_result
                elif isinstance(inference_result, dict):
                    report.model_inference = self._coerce_model_inference(inference_result)
            except Exception as e:
                logger.warning("[DSA] Model inference failed: %s", e)
                report.errors.append(f"模型推理失败: {e}")

        if depth == "cv+model":
            return

        # Step 3: 多模态模型理解
        logger.info("[DSA] Unlabeled pipeline step 3: Multimodal semantic understanding")
        try:
            semantic = await self._semantic_analyzer.analyze(
                image_dir=image_dir,
                data_kind=DataKind.UNLABELED,
                cv_report=report.cv_report,
                model_inference=report.model_inference,
            )
            report.semantic = semantic
        except Exception as e:
            logger.warning("[DSA] Semantic analysis failed: %s", e)
            report.errors.append(f"多模态理解失败: {e}")

    # ------------------------------------------------------------------
    # 有标注数据管线
    # ------------------------------------------------------------------

    async def _run_labeled_pipeline(
        self,
        image_dir: Path,
        report: DataUnderstandingReport,
        depth: str,
        labels: dict[str, str] | None,
        annotations: dict[str, Any] | None,
    ) -> None:
        """有标注数据管线：标签统计 → 标签+图 CV 特征 → 多模态理解。"""
        # Step 1: 标签统计
        logger.info("[DSA] Labeled pipeline step 1: Label statistics")
        label_stats = self._analyze_labels(image_dir, labels, annotations)
        report.label_stats = label_stats

        # Step 2: 标签 + 图的 CV 特征分析（前景/背景）
        logger.info("[DSA] Labeled pipeline step 2: CV feature analysis with labels")
        try:
            cv_report = self._cv_analyzer.analyze_labeled(image_dir, labels, annotations)
            report.cv_report = cv_report
        except Exception as e:
            logger.warning("[DSA] CV analysis failed: %s", e)
            report.errors.append(f"CV 分析失败: {e}")

        if depth == "cv_only":
            return

        # Step 3: 多模态模型理解
        logger.info("[DSA] Labeled pipeline step 3: Multimodal semantic understanding")
        try:
            semantic = await self._semantic_analyzer.analyze(
                image_dir=image_dir,
                data_kind=DataKind.LABELED,
                cv_report=report.cv_report,
                labels=labels,
                annotations=annotations,
            )
            report.semantic = semantic
        except Exception as e:
            logger.warning("[DSA] Semantic analysis failed: %s", e)
            report.errors.append(f"多模态理解失败: {e}")

    # ------------------------------------------------------------------
    # 标签统计分析
    # ------------------------------------------------------------------

    def _analyze_labels(
        self,
        image_dir: Path,
        labels: dict[str, str] | None,
        annotations: dict[str, Any] | None,
    ) -> LabelStats:
        """分析标签统计：类别分布、覆盖率、划分建议等。"""
        stats = LabelStats()

        labels = labels or self._labels_from_annotations(annotations)
        if not labels:
            return stats

        stats.total_samples = len(labels)
        stats.labeled_samples = len([v for v in labels.values() if v])
        stats.unlabeled_samples = stats.total_samples - stats.labeled_samples
        stats.label_coverage = (
            stats.labeled_samples / stats.total_samples if stats.total_samples > 0 else 0.0
        )

        # 类别分布
        class_dist: dict[str, int] = {}
        for label in labels.values():
            if label:
                class_dist[label] = class_dist.get(label, 0) + 1
        stats.class_distribution = class_dist
        stats.defect_type_distribution = dict(class_dist)

        # 归一化比例
        total_labeled = sum(class_dist.values())
        stats.class_ratio = {
            k: round(v / total_labeled, 4) for k, v in class_dist.items()
        } if total_labeled > 0 else {}

        # 一致性检查：检测可能的标签问题
        if total_labeled > 0:
            # 类别不平衡检测
            max_count = max(class_dist.values())
            min_count = min(class_dist.values())
            if max_count > min_count * 5:
                stats.consistency_issues.append(
                    f"类别不平衡: 最多 '{max(class_dist, key=class_dist.get)}' "
                    f"({max_count}张) vs 最少 '{min(class_dist, key=class_dist.get)}' "
                    f"({min_count}张)"
                )

        if annotations:
            self._analyze_annotation_quality(stats, annotations)

        # 训练/验证/测试划分建议 (70/15/15)
        stats.split_suggestion = {
            "train": int(stats.labeled_samples * 0.7),
            "val": int(stats.labeled_samples * 0.15),
            "test": stats.labeled_samples - int(stats.labeled_samples * 0.7) - int(stats.labeled_samples * 0.15),
        }

        return stats

    @staticmethod
    def _coerce_model_inference(
        value: ModelInferenceResult | dict[str, Any],
    ) -> ModelInferenceResult:
        if isinstance(value, ModelInferenceResult):
            return value
        allowed = {
            "model_name",
            "model_type",
            "prompt",
            "inferred_labels",
            "confidence_scores",
            "anomaly_samples",
            "semantic_clusters",
            "suspected_regions",
            "visualization_refs",
            "analysis_summary",
        }
        return ModelInferenceResult(**{k: v for k, v in value.items() if k in allowed})

    @staticmethod
    def _labels_from_annotations(
        annotations: dict[str, Any] | None,
    ) -> dict[str, str] | None:
        if not annotations:
            return None
        labels: dict[str, str] = {}
        for filename, ann in annotations.items():
            label = DataUnderstandingOrchestrator._extract_primary_label(ann)
            if label:
                labels[filename] = label
        return labels

    @staticmethod
    def _extract_primary_label(annotation: Any) -> str:
        if isinstance(annotation, str):
            return annotation
        if isinstance(annotation, dict):
            for key in ("label", "class", "category", "defect_type", "text"):
                value = annotation.get(key)
                if isinstance(value, str) and value:
                    return value
            for key in ("annotations", "regions", "bboxes", "boxes", "masks", "ocr"):
                value = annotation.get(key)
                if isinstance(value, list) and value:
                    return DataUnderstandingOrchestrator._extract_primary_label(value[0])
        if isinstance(annotation, list) and annotation:
            return DataUnderstandingOrchestrator._extract_primary_label(annotation[0])
        return ""

    @staticmethod
    def _iter_annotation_items(annotation: Any) -> list[dict[str, Any]]:
        if isinstance(annotation, dict):
            for key in ("annotations", "regions", "bboxes", "boxes", "masks", "ocr"):
                value = annotation.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            return [annotation]
        if isinstance(annotation, list):
            return [item for item in annotation if isinstance(item, dict)]
        return []

    def _analyze_annotation_quality(
        self,
        stats: LabelStats,
        annotations: dict[str, Any],
    ) -> None:
        bbox_count = 0
        bbox_area_ratios: list[float] = []
        images_with_annotations = 0
        annotation_type_dist: dict[str, int] = {}

        for filename, ann in annotations.items():
            items = self._iter_annotation_items(ann)
            if items:
                images_with_annotations += 1
            for item in items:
                if "bbox" in item:
                    annotation_type_dist["bbox"] = annotation_type_dist.get("bbox", 0) + 1
                    bbox_count += 1
                    area_ratio = self._bbox_area_ratio(item.get("bbox"))
                    if area_ratio is not None:
                        bbox_area_ratios.append(area_ratio)
                    else:
                        stats.bbox_quality_issues.append(f"{filename}: bbox 格式无效")
                if "mask" in item or "segmentation" in item:
                    annotation_type_dist["mask"] = annotation_type_dist.get("mask", 0) + 1
                    mask = item.get("mask") or item.get("segmentation")
                    if not mask:
                        stats.mask_quality_issues.append(f"{filename}: mask 为空")
                if "text" in item or "ocr" in item:
                    annotation_type_dist["ocr"] = annotation_type_dist.get("ocr", 0) + 1
                    text = item.get("text") or item.get("ocr")
                    if not isinstance(text, str) or not text.strip():
                        stats.ocr_quality_issues.append(f"{filename}: OCR 标签为空")

        stats.annotation_type_distribution = annotation_type_dist
        if stats.total_samples > 0:
            stats.label_coverage = max(stats.label_coverage, images_with_annotations / stats.total_samples)
        if stats.total_samples > 0:
            stats.avg_bbox_count = round(bbox_count / stats.total_samples, 3)
        if bbox_area_ratios:
            stats.avg_bbox_area_ratio = round(sum(bbox_area_ratios) / len(bbox_area_ratios), 4)

    @staticmethod
    def _bbox_area_ratio(bbox: Any) -> float | None:
        if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
            return None
        try:
            _, _, w, h = [float(v) for v in bbox[:4]]
            if w <= 0 or h <= 0:
                return None
            # 没有图像尺寸时，只记录相对 bbox 合法性；若本身是 0-1 归一化则可估算占比。
            if w <= 1.0 and h <= 1.0:
                return w * h
            return 0.0
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # 总结生成
    # ------------------------------------------------------------------

    def _generate_summary(self, report: DataUnderstandingReport) -> None:
        """生成数据理解总结和建议。"""
        parts: list[str] = []
        recommendations: list[str] = []

        kind_str = "有标注" if report.data_kind == DataKind.LABELED else "无标注"
        parts.append(f"数据集 '{report.dataset_name}' ({kind_str}数据) 分析完成。")

        # CV 统计摘要
        if report.cv_report:
            cv = report.cv_report
            parts.append(
                f"共 {cv.total_images} 张图片，"
                f"其中 {cv.blurry_count} 张模糊、{cv.dark_count} 张过暗、{cv.bright_count} 张过亮、"
                f"{cv.duplicate_count} 张重复。"
            )
            if cv.blurry_count > cv.total_images * 0.1:
                recommendations.append("模糊样本占比超过 10%，建议先做图像增强或剔除。")
            if cv.duplicate_count > 0:
                recommendations.append(f"发现 {cv.duplicate_count} 张重复样本，建议去重。")
            if cv.bright_count > 0 or cv.dark_count > 0:
                recommendations.append("存在过暗/过亮样本，建议做曝光校正预处理。")

        # 标签统计摘要
        if report.label_stats:
            ls = report.label_stats
            parts.append(
                f"标签覆盖率 {ls.label_coverage:.1%}，"
                f"类别分布: {ls.class_distribution}。"
            )
            if ls.consistency_issues:
                parts.append(f"一致性问题: {'; '.join(ls.consistency_issues)}。")
                recommendations.append("类别不平衡严重，建议做数据增强或重采样。")
            if ls.unlabeled_samples > 0:
                recommendations.append(f"{ls.unlabeled_samples} 张图片缺少标签，建议补标注。")

        # 语义理解摘要
        if report.semantic:
            sem = report.semantic
            if sem.scene_type:
                parts.append(f"场景类型: {sem.scene_type}。")
            if sem.defect_categories:
                parts.append(f"识别到的缺陷类型: {', '.join(sem.defect_categories)}。")
            if sem.data_bias:
                parts.append(f"数据偏差: {'; '.join(sem.data_bias)}。")
            if sem.quality_assessment:
                parts.append(f"质量评估: {sem.quality_assessment}")
            if sem.preannotation_suggestions:
                recommendations.append(
                    f"多模态模型建议了 {len(sem.preannotation_suggestions)} 个预标注标签，"
                    "可作为人工标注参考。"
                )
            if sem.needs_manual_annotation:
                recommendations.append(
                    f"{len(sem.needs_manual_annotation)} 类样本需要人工标注确认。"
                )

        # 错误信息
        if report.errors:
            parts.append(f"分析过程中出现 {len(report.errors)} 个错误。")

        report.summary = " ".join(parts)
        report.recommendations = recommendations
