"""数据理解结果模型 — 统计 CV 特征 + 多模态语义理解的统一数据类。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DataKind(str, Enum):
    """数据集类型。"""
    UNLABELED = "unlabeled"   # 无标注
    LABELED = "labeled"       # 有标注


# ---------------------------------------------------------------------------
# 2.1 统计与 CV 特征分析结果
# ---------------------------------------------------------------------------

@dataclass
class ImageCVStats:
    """单张图的 CV 特征统计。"""
    filename: str
    source_id: str = ""              # 数据来源/产线/相机/批次（如能从路径或元数据推断）
    width: int = 0
    height: int = 0
    aspect_ratio: float = 0.0       # width / height
    mean_brightness: float = 0.0    # 灰度均值 [0, 255]
    std_brightness: float = 0.0     # 灰度标准差
    contrast: float = 0.0           # RMS 对比度
    sharpness: float = 0.0          # Laplacian 方差
    noise_level: float = 0.0        # 噪声估计（高频能量占比）
    is_blurry: bool = False         # 模糊样本（sharpness < 阈值）
    is_dark: bool = False           # 过暗样本
    is_bright: bool = False         # 过亮样本
    is_duplicate: bool = False      # 重复样本
    duplicate_of: str = ""          # 重复的源文件名
    is_anomaly: bool = False        # 统计/CV 规则判定的异常样本
    acquisition_view: str = ""      # 采集视角粗分组（landscape/portrait/square/other）


@dataclass
class DatasetCVReport:
    """数据集级 CV 特征统计报告。"""
    total_images: int = 0
    valid_images: int = 0
    # ── 尺寸分布 ──
    width_distribution: dict[str, int] = field(default_factory=dict)   # {"2048": 5, "1920": 3}
    height_distribution: dict[str, int] = field(default_factory=dict)
    aspect_ratio_distribution: dict[str, int] = field(default_factory=dict)  # {"4:3": 8, "16:9": 2}
    # ── 质量分布 ──
    brightness_stats: dict[str, float] = field(default_factory=dict)   # {mean, std, min, max, p25, p50, p75}
    contrast_stats: dict[str, float] = field(default_factory=dict)
    sharpness_stats: dict[str, float] = field(default_factory=dict)
    noise_stats: dict[str, float] = field(default_factory=dict)
    # ── 异常样本 ──
    blurry_count: int = 0
    dark_count: int = 0
    bright_count: int = 0
    duplicate_count: int = 0
    anomaly_files: list[str] = field(default_factory=list)             # 异常样本文件名
    blurry_files: list[str] = field(default_factory=list)
    # ── 重复样本组 ──
    duplicate_groups: list[list[str]] = field(default_factory=list)
    # ── 采集来源分布（如有元数据）──
    source_distribution: dict[str, int] = field(default_factory=dict)
    acquisition_view_distribution: dict[str, int] = field(default_factory=dict)
    # ── 有标注数据专用：标签 + 图的 CV 特征分析（前景/背景）──
    foreground_stats: dict[str, dict[str, float]] = field(default_factory=dict)
    background_stats: dict[str, dict[str, float]] = field(default_factory=dict)
    foreground_background_contrast: dict[str, float] = field(default_factory=dict)
    # ── 逐图明细 ──
    per_image: list[ImageCVStats] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 标签分析结果（有标注数据专用）
# ---------------------------------------------------------------------------

@dataclass
class LabelStats:
    """标签统计分析结果。"""
    total_samples: int = 0
    labeled_samples: int = 0
    unlabeled_samples: int = 0
    # ── 类别分布 ──
    class_distribution: dict[str, int] = field(default_factory=dict)   # {"气孔": 15, "夹渣": 8}
    class_ratio: dict[str, float] = field(default_factory=dict)        # 归一化比例
    # ── 标签质量 ──
    label_coverage: float = 0.0           # 标注覆盖率 = labeled / total
    avg_bbox_count: float = 0.0           # 平均每图 bbox 数
    avg_bbox_area_ratio: float = 0.0      # 平均 bbox 面积占比
    bbox_quality_issues: list[str] = field(default_factory=list)
    mask_quality_issues: list[str] = field(default_factory=list)
    ocr_quality_issues: list[str] = field(default_factory=list)
    defect_type_distribution: dict[str, int] = field(default_factory=dict)
    annotation_type_distribution: dict[str, int] = field(default_factory=dict)
    # ── 一致性 ──
    consistency_issues: list[str] = field(default_factory=list)        # 一致性问题
    # ── 划分建议 ──
    split_suggestion: dict[str, int] = field(default_factory=dict)     # {"train": 24, "val": 6, "test": 6}
    # ── 标签-图像匹配问题 ──
    mismatch_samples: list[str] = field(default_factory=list)          # 标签与图像内容不匹配的样本


# ---------------------------------------------------------------------------
# 2.2 多模态语义理解结果
# ---------------------------------------------------------------------------

@dataclass
class SemanticUnderstanding:
    """多模态语义理解结果。"""
    # ── 主要对象识别 ──
    main_objects: list[str] = field(default_factory=list)              # ["焊缝", "母材", "热影响区"]
    object_background_notes: list[str] = field(default_factory=list)   # 目标/背景关系的细化说明
    # ── 缺陷类别理解 ──
    defect_categories: list[str] = field(default_factory=list)         # 识别到的缺陷类型
    suspected_defect_regions: list[dict] = field(default_factory=list) # [{"type": "气孔", "confidence": 0.85, "location": "右上区域"}]
    # ── 场景语义 ──
    scene_type: str = ""                                                # "焊缝射线检测" / "焊缝表面检测"
    target_background_relation: str = ""                                # "焊缝居中，母材对称分布"
    # ── 潜在类别 ──
    potential_categories: list[str] = field(default_factory=list)      # 数据中可能存在但未定义的类别
    # ── 数据偏差 ──
    data_bias: list[str] = field(default_factory=list)                  # ["大部分样本来自同一视角", "缺陷类型分布不均"]
    # ── 复杂样本说明 ──
    complex_samples: list[dict] = field(default_factory=list)          # [{"file": "xxx.jpg", "reason": "多缺陷重叠"}]
    # ── 质量判断（难以量化的）──
    quality_assessment: str = ""                                        # 整体质量评语
    # ── 预标注建议（无标注数据专用）──
    preannotation_suggestions: list[dict] = field(default_factory=list) # [{"file": "xxx.jpg", "suggested_label": "气孔", "confidence": 0.7}]
    needs_manual_annotation: list[str] = field(default_factory=list)    # 需要人工标注的样本
    label_image_match_issues: list[str] = field(default_factory=list)   # 有标注数据：标签与图像内容不匹配
    foreground_background_findings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 模型推理结果（无标注数据 — 第二步）
# ---------------------------------------------------------------------------

@dataclass
class ModelInferenceResult:
    """已有模型推理结果（IQA/SAM 等）。"""
    model_name: str = ""                              # "IQA" / "SAM" / "缺陷检测模型"
    model_type: str = ""                              # "business_model" / "sam" / "detector" / "segmenter"
    prompt: str = ""                                  # SAM/开放词汇模型 prompt
    inferred_labels: dict[str, str] = field(default_factory=dict)   # {"file.jpg": "气孔"}
    confidence_scores: dict[str, float] = field(default_factory=dict)
    anomaly_samples: list[str] = field(default_factory=list)        # 模型认为异常的样本
    semantic_clusters: list[dict] = field(default_factory=list)     # [{"cluster_id": 0, "files": [...], "description": "..."}]
    suspected_regions: dict[str, list[dict]] = field(default_factory=dict)  # {"file.jpg": [{"bbox": [...], "label": "..."}]}
    visualization_refs: dict[str, str] = field(default_factory=dict)        # 推理可视化图路径/URL
    analysis_summary: str = ""


# ---------------------------------------------------------------------------
# 综合报告
# ---------------------------------------------------------------------------

@dataclass
class DataUnderstandingReport:
    """数据理解综合报告。"""
    data_kind: DataKind = DataKind.UNLABELED
    dataset_name: str = ""
    # ── 各阶段结果 ──
    cv_report: DatasetCVReport | None = None
    label_stats: LabelStats | None = None                 # 有标注数据专用
    model_inference: ModelInferenceResult | None = None   # 无标注数据专用
    semantic: SemanticUnderstanding | None = None
    # ── 总结 ──
    summary: str = ""
    recommendations: list[str] = field(default_factory=list)
    # ── 元信息 ──
    analysis_depth: str = "full"    # "cv_only" / "cv+model" / "full"
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """转为可序列化的字典（给 LLM 和前端用）。"""
        import dataclasses
        return dataclasses.asdict(self)
