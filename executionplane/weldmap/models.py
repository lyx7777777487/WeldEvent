"""WeldMap 数据模型 — 黑板模式的核心数据结构。

Source: Complete_architecture_V1.docx 第二章 (WeldMap黑板模式)

WeldMap 是 Agent 间唯一通信介质，采用层级路径结构:
    weldmap:///{workflow_id}/
        ├── image/quality/     ← IQA 写入
        ├── mask/             ← PPA 写入
        ├── annotations/      ← MEA 写入
        ├── validation/       ← VDA / 规则引擎写入
        ├── rendering/        ← RDA 写入
        └── decision/         ← VDA 判定结果
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, NewType

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 基础类型
# ---------------------------------------------------------------------------

WorkflowId = NewType("WorkflowId", str)  # 工作流唯一标识。
WeldMapPath = NewType("WeldMapPath", str)  # WeldMap 内部路径，如 'image/quality/resolution'。
Version = NewType("Version", int)  # WeldMap 乐观锁版本号，每次写入自增。


# ---------------------------------------------------------------------------
# 图像质量 (IQA 输出)
# ---------------------------------------------------------------------------

class ResolutionCheck(BaseModel):
    """分辨率检查结果。"""
    width: int
    height: int
    min_required: tuple[int, int] = (2048, 1536)
    passed: bool = False
    detail: str = ""


class ExposureCheck(BaseModel):
    """曝光检查结果。"""
    mean_gray: float
    valid_range: tuple[float, float] = (45.0, 90.0)
    passed: bool = False
    detail: str = ""


class FocusCheck(BaseModel):
    """对焦检查结果（Laplacian 方差）。"""
    laplacian_variance: float
    threshold: float = 50.0
    passed: bool = False
    detail: str = ""


class CompletenessCheck(BaseModel):
    """完整性检查 — 焊缝区域是否被裁切/遮挡。"""
    weld_region_present: bool = True
    crop_ratio: float = 0.0
    occlusion_detected: bool = False
    passed: bool = True
    detail: str = ""


class ImageQualityReport(BaseModel):
    """IQA 综合质量报告 — 写入 weldmap:///{wf_id}/image/quality/"""
    
    resolution: ResolutionCheck
    exposure: ExposureCheck
    focus: FocusCheck
    completeness: CompletenessCheck
    
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    
    # 深度视觉层结果（可选，仅当 overall_confidence < 阈值时填充）
    deep_vision_findings: list[str] = Field(default_factory=list)
    deep_vision_anomalies: list[str] = Field(default_factory=list)
    
    # 路由决策
    route_decision: str = "AUTO_PASS"  # AUTO_PASS / SUGGEST_REVIEW / MANDATORY_REVIEW / REJECT
    
    inspected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    inspected_by: str = "iqa_agent"


# ---------------------------------------------------------------------------
# 掩码 (PPA 输出)
# ---------------------------------------------------------------------------

class MaskConfidenceStats(BaseModel):
    """掩码置信度统计。"""
    mean: float = 0.0
    boundary_mean: float = 0.0
    low_conf_ratio: float = 0.0


class MaskMetrics(BaseModel):
    """掩码质量指标。"""
    area_mm2: float = 0.0
    connected_components: int = 0
    boundary_smoothness: float = 0.0
    internal_holes: int = 0


class ConsensusReport(BaseModel):
    """U-Net/SAM 分歧报告（Consensus Engine）。"""
    iou: float = 1.0
    hausdorff_px: float = 0.0
    disagreement_regions: list[dict[str, Any]] = Field(default_factory=list)
    consensus_level: str = "HIGH"  # HIGH / MEDIUM / LOW


class MaskData(BaseModel):
    """掩码数据 — 写入 weldmap:///{wf_id}/mask/"""
    
    mask_path: str = ""
    confidence_map_path: str = ""
    confidence: float = 0.0
    confidence_stats: MaskConfidenceStats = Field(default_factory=MaskConfidenceStats)
    source_model: str = ""           # unet_v2.1.0 / sam_vitl / otsu
    boundary_refined: bool = False
    is_fallback: bool = False         # 是否为降级产物
    consensus: ConsensusReport | None = None
    metrics: MaskMetrics = Field(default_factory=MaskMetrics)


# ---------------------------------------------------------------------------
# 标注元素 (MEA 输出)
# ---------------------------------------------------------------------------

class AnnotationElement(BaseModel):
    """通用标注元素。"""
    element_id: str                    # L1, L2, O, Fz1, Fz2, P1, P2, Gap, triangle
    geometry_px: dict[str, float] = Field(default_factory=dict)   # 像素坐标 {x, y, ...}
    geometry_mm: dict[str, float] = Field(default_factory=dict)   # 毫米坐标
    value: float | None = None          # 测量值 (mm)
    uncertainty: float = 0.0           # 不确定性估计 (mm)
    confidence: float = 0.0            # 检测置信度
    source: str = ""                   # 检测算法名
    validation_status: str = "unchecked"  # unchecked / valid / warning / rejected


class AnnotationsData(BaseModel):
    """标注集 — 写入 weldmap:///{wf_id}/annotations/"""
    elements: dict[str, AnnotationElement] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 校验与判定 (VDA 输出)
# ---------------------------------------------------------------------------

class RuleViolation(BaseModel):
    """规则违反记录。"""
    rule_id: str                       # CONS-01 ~ CONS-15
    severity: str = "LOW"              # HIGH / MED / LOW
    message: str = ""
    affected_elements: list[str] = Field(default_factory=list)


class ValidationData(BaseModel):
    """一致性校验结果 — 写入 weldmap:///{wf_id}/validation/"""
    
    status: str = "valid"              # valid / warning / rejected
    violations: list[RuleViolation] = Field(default_factory=list)


class RiskScore(BaseModel):
    """风险评分。"""
    level: str = "LOW"                 # LOW / ELEVATED / CRITICAL
    snr_min: float = 0.0               # 最小信噪比 (margin/sigma)
    worst_margin_sigma: float = 0.0
    flip_risk: float = 0.0
    reason: str = ""


class DecisionData(BaseModel):
    """判定结果 — 写入 weldmap:///{wf_id}/decision/"""
    
    verdict: str = "PENDING"            # OK / NG / MARGINAL / PENDING
    margins: list[dict[str, Any]] = Field(default_factory=list)
    root_cause: str | None = None       # NG 根因 (LLM 生成)
    risk_score: RiskScore = Field(default_factory=RiskScore)
    confidence_route: str = "AUTO_PASS" # AUTO_PASS / SUGGEST_REVIEW / MANDATORY_REVIEW


# ---------------------------------------------------------------------------
# WeldMap 完整快照
# ---------------------------------------------------------------------------

class WeldMapSnapshot(BaseModel):
    """WeldMap 完整状态快照。"""
    
    workflow_id: str
    version: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    image_quality: ImageQualityReport | None = None
    image_preprocess: dict[str, Any] | None = None  # PPA 写入 weldmap:///{wf_id}/image/preprocess
    mask: MaskData | None = None
    annotations: AnnotationsData | None = None
    validation: ValidationData | None = None
    decision: DecisionData | None = None
    
    # Event sourcing history
    events: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# WeldMap 事件 (Event Sourcing)
# ---------------------------------------------------------------------------

class WeldMapEvent(BaseModel):
    """不可变写事件 — 每次 WeldMap 写入产生一条。"""
    
    event_type: str                   # image_quality_updated / mask_updated / ...
    path: str                         # 被修改的 WeldMap 路径
    old_value: Any | None = None
    new_value: Any | None = None
    source: str = ""                  # 写入者 Agent 标识
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
