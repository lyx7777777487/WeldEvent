"""IQA质量标准配置 — 图像质量评估特有配置。

Source: WeldEvent架构设计讨论

设计原则:
  - 纯数据模型，无硬编码枚举
  - 通过Registry注册，支持动态添加
  - 支持从YAML/JSON/数据库加载
  - 支持继承覆盖（parent_standard_id）

注意:
  - 此配置仅用于IQA Activity
  - 其他Activity的配置应在各自的子模块中定义
"""

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# 质量标准定义（纯数据模型）
# ---------------------------------------------------------------------------

@dataclass
class QualityStandard:
    """质量标准配置 — 纯数据模型，无硬编码枚举。
    
    所有标准通过Registry注册，支持:
      - 代码注册（开发阶段）
      - YAML/JSON配置加载（部署阶段）
      - 数据库动态加载（运行阶段）
    
    Attributes:
        standard_id: 标准唯一标识，如 "macro_weld", "micro_metallography"
        name: 显示名称，如 "宏观焊缝检测"
        version: 版本号，如 "v1", "v2"
        tags: 分类标签，用于检索，如 ["weld", "macro", "GMAW"]
        
        resolution_min_width: 分辨率最小宽度
        resolution_min_height: 分辨率最小高度
        
        exposure_min_gray: 曝光灰度最小值
        exposure_max_gray: 曝光灰度最大值
        
        focus_laplacian_min: 对焦Laplacian方差最小值
        
        completeness_weld_region_min_ratio: 焊缝区域占比最小值
        
        auto_pass_threshold: 自动放行置信度阈值
        suggest_review_threshold: 建议审查阈值
        mandatory_review_threshold: 强制审查阈值
        reject_threshold: 拒绝阈值
        
        deep_vision_enabled: 是否启用MLLM深度视觉
        deep_vision_trigger_threshold: MLLM触发阈值
        deep_vision_check_items: MLLM检测项目
        deep_vision_max_latency_ms: MLLM最大延迟
        deep_vision_on_error: MLLM出错时的降级策略
        
        weights: 各检查项权重（用于综合置信度计算）
        
        is_active: 是否激活
        parent_standard_id: 父标准ID（支持继承覆盖）
    """
    
    # 标识信息
    standard_id: str
    name: str
    version: str = "v1"
    description: str = ""
    tags: list[str] = field(default_factory=list)
    
    # 分辨率标准
    resolution_min_width: int = 2048
    resolution_min_height: int = 1536
    resolution_max_width: int | None = None
    resolution_max_height: int | None = None
    resolution_aspect_ratio: float | None = None
    
    # 曝光标准
    exposure_min_gray: float = 45.0
    exposure_max_gray: float = 90.0
    exposure_min_contrast: float | None = None
    exposure_max_contrast: float | None = None
    
    # 对焦标准
    focus_laplacian_min: float = 50.0
    focus_edge_density_min: float | None = None
    focus_local_blur_max_ratio: float | None = None
    
    # 完整性标准
    completeness_weld_region_min_ratio: float = 0.3
    completeness_required_regions: list[str] = field(default_factory=lambda: ["weld_zone"])
    completeness_max_crop_ratio: float = 0.1
    
    # 路由决策阈值
    auto_pass_threshold: float = 0.85
    suggest_review_threshold: float = 0.70
    mandatory_review_threshold: float = 0.50
    reject_threshold: float = 0.25
    
    # MLLM深度视觉配置
    deep_vision_enabled: bool = True
    deep_vision_trigger_threshold: float = 0.85
    deep_vision_check_items: list[str] = field(default_factory=lambda: [
        "occlusion",
        "lens_contamination",
        "foreign_object",
        "reflection",
        "abnormal_texture",
    ])
    deep_vision_max_latency_ms: int = 3000
    deep_vision_on_error: str = "fallback_to_rule"
    deep_vision_force_trigger_conditions: list[str] = field(default_factory=list)
    
    # 权重配置
    weights: dict[str, float] = field(default_factory=lambda: {
        "resolution": 0.25,
        "exposure": 0.25,
        "focus": 0.30,
        "completeness": 0.20,
    })
    
    # 元数据
    created_by: str = ""
    created_at: str | None = None
    is_active: bool = True
    parent_standard_id: str | None = None
    
    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        return {
            "standard_id": self.standard_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "tags": self.tags,
            "resolution": {
                "min_width": self.resolution_min_width,
                "min_height": self.resolution_min_height,
                "max_width": self.resolution_max_width,
                "max_height": self.resolution_max_height,
                "aspect_ratio": self.resolution_aspect_ratio,
            },
            "exposure": {
                "min_gray": self.exposure_min_gray,
                "max_gray": self.exposure_max_gray,
                "min_contrast": self.exposure_min_contrast,
                "max_contrast": self.exposure_max_contrast,
            },
            "focus": {
                "laplacian_min": self.focus_laplacian_min,
                "edge_density_min": self.focus_edge_density_min,
                "local_blur_max_ratio": self.focus_local_blur_max_ratio,
            },
            "completeness": {
                "weld_region_min_ratio": self.completeness_weld_region_min_ratio,
                "required_regions": self.completeness_required_regions,
                "max_crop_ratio": self.completeness_max_crop_ratio,
            },
            "thresholds": {
                "auto_pass": self.auto_pass_threshold,
                "suggest_review": self.suggest_review_threshold,
                "mandatory_review": self.mandatory_review_threshold,
                "reject": self.reject_threshold,
            },
            "deep_vision": {
                "enabled": self.deep_vision_enabled,
                "trigger_threshold": self.deep_vision_trigger_threshold,
                "check_items": self.deep_vision_check_items,
                "max_latency_ms": self.deep_vision_max_latency_ms,
                "on_error": self.deep_vision_on_error,
                "force_trigger_conditions": self.deep_vision_force_trigger_conditions,
            },
            "weights": self.weights,
            "metadata": {
                "created_by": self.created_by,
                "created_at": self.created_at,
                "is_active": self.is_active,
                "parent_standard_id": self.parent_standard_id,
            },
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QualityStandard":
        """从字典反序列化"""
        return cls(
            standard_id=data["standard_id"],
            name=data["name"],
            version=data.get("version", "v1"),
            description=data.get("description", ""),
            tags=data.get("tags", []),
            resolution_min_width=data.get("resolution", {}).get("min_width", 2048),
            resolution_min_height=data.get("resolution", {}).get("min_height", 1536),
            resolution_max_width=data.get("resolution", {}).get("max_width"),
            resolution_max_height=data.get("resolution", {}).get("max_height"),
            resolution_aspect_ratio=data.get("resolution", {}).get("aspect_ratio"),
            exposure_min_gray=data.get("exposure", {}).get("min_gray", 45.0),
            exposure_max_gray=data.get("exposure", {}).get("max_gray", 90.0),
            exposure_min_contrast=data.get("exposure", {}).get("min_contrast"),
            exposure_max_contrast=data.get("exposure", {}).get("max_contrast"),
            focus_laplacian_min=data.get("focus", {}).get("laplacian_min", 50.0),
            focus_edge_density_min=data.get("focus", {}).get("edge_density_min"),
            focus_local_blur_max_ratio=data.get("focus", {}).get("local_blur_max_ratio"),
            completeness_weld_region_min_ratio=data.get("completeness", {}).get("weld_region_min_ratio", 0.3),
            completeness_required_regions=data.get("completeness", {}).get("required_regions", ["weld_zone"]),
            completeness_max_crop_ratio=data.get("completeness", {}).get("max_crop_ratio", 0.1),
            auto_pass_threshold=data.get("thresholds", {}).get("auto_pass", 0.85),
            suggest_review_threshold=data.get("thresholds", {}).get("suggest_review", 0.70),
            mandatory_review_threshold=data.get("thresholds", {}).get("mandatory_review", 0.50),
            reject_threshold=data.get("thresholds", {}).get("reject", 0.25),
            deep_vision_enabled=data.get("deep_vision", {}).get("enabled", True),
            deep_vision_trigger_threshold=data.get("deep_vision", {}).get("trigger_threshold", 0.85),
            deep_vision_check_items=data.get("deep_vision", {}).get("check_items", []),
            deep_vision_max_latency_ms=data.get("deep_vision", {}).get("max_latency_ms", 3000),
            deep_vision_on_error=data.get("deep_vision", {}).get("on_error", "fallback_to_rule"),
            deep_vision_force_trigger_conditions=data.get("deep_vision", {}).get("force_trigger_conditions", []),
            weights=data.get("weights", {}),
            created_by=data.get("metadata", {}).get("created_by", ""),
            created_at=data.get("metadata", {}).get("created_at"),
            is_active=data.get("metadata", {}).get("is_active", True),
            parent_standard_id=data.get("metadata", {}).get("parent_standard_id"),
        )


# ---------------------------------------------------------------------------
# 预置标准
# ---------------------------------------------------------------------------

DEFAULT_STANDARDS: dict[str, QualityStandard] = {
    "macro_weld": QualityStandard(
        standard_id="macro_weld",
        name="宏观焊缝检测",
        description="GMAW T型角焊缝常规检测标准",
        tags=["weld", "macro", "GMAW", "T-joint"],
    ),
    "micro_metallography": QualityStandard(
        standard_id="micro_metallography",
        name="微观金相分析",
        description="高倍率金相显微镜图像标准",
        tags=["micro", "metallography", "microscope"],
        resolution_min_width=4096,
        resolution_min_height=3072,
        exposure_min_gray=60.0,
        exposure_max_gray=120.0,
        focus_laplacian_min=80.0,
        completeness_weld_region_min_ratio=0.5,
    ),
    "high_speed_line": QualityStandard(
        standard_id="high_speed_line",
        name="高速产线检测",
        description="高速产线快速检测，宽松标准",
        tags=["high_speed", "production", "fast"],
        resolution_min_width=1024,
        resolution_min_height=768,
        exposure_min_gray=30.0,
        exposure_max_gray=80.0,
        focus_laplacian_min=30.0,
        completeness_weld_region_min_ratio=0.2,
        auto_pass_threshold=0.75,
        deep_vision_enabled=False,
    ),
    "night_inspection": QualityStandard(
        standard_id="night_inspection",
        name="夜间检测",
        description="低光照环境检测标准",
        tags=["night", "low_light"],
        exposure_min_gray=20.0,
        exposure_max_gray=60.0,
        focus_laplacian_min=40.0,
    ),
}


# ---------------------------------------------------------------------------
# 质量标准注册表
# ---------------------------------------------------------------------------

class QualityStandardRegistry:
    """质量标准注册表 — IQA特有配置管理。
    
    支持多种注册方式:
      1. 代码注册: registry.register(standard)
      2. YAML加载: registry.load_from_yaml(path)
      3. JSON加载: registry.load_from_json(path)
      4. 动态API: registry.register_from_dict(data)
    
    Usage::
        registry = QualityStandardRegistry()
        standard = registry.get("macro_weld")
        registry.register(QualityStandard(
            standard_id="my_custom",
            name="我的自定义标准",
        ))
    """
    
    def __init__(self) -> None:
        self._standards: dict[str, dict[str, QualityStandard]] = {}
        self._tag_index: dict[str, list[str]] = {}
        self._load_defaults()
    
    def _load_defaults(self) -> None:
        for standard in DEFAULT_STANDARDS.values():
            self.register(standard)
    
    def register(self, standard: QualityStandard) -> None:
        sid = standard.standard_id
        ver = standard.version
        if sid not in self._standards:
            self._standards[sid] = {}
        self._standards[sid][ver] = standard
        for tag in standard.tags:
            if tag not in self._tag_index:
                self._tag_index[tag] = []
            if sid not in self._tag_index[tag]:
                self._tag_index[tag].append(sid)
    
    def register_from_dict(self, data: dict[str, Any]) -> None:
        self.register(QualityStandard.from_dict(data))
    
    def get(self, standard_id: str, version: str = "latest") -> QualityStandard:
        if standard_id not in self._standards:
            raise KeyError(f"质量标准不存在: {standard_id}")
        versions = self._standards[standard_id]
        if version == "latest":
            latest_ver = max(versions.keys(), key=lambda v: self._parse_version(v))
            return versions[latest_ver]
        if version not in versions:
            raise KeyError(f"标准版本不存在: {standard_id}@{version}")
        return versions[version]
    
    def find_by_tags(self, tags: list[str]) -> list[QualityStandard]:
        result_ids: set[str] = set()
        for tag in tags:
            if tag in self._tag_index:
                result_ids.update(self._tag_index[tag])
        results = []
        for sid in result_ids:
            try:
                results.append(self.get(sid, version="latest"))
            except KeyError:
                pass
        return results
    
    def list_all(self, active_only: bool = True) -> list[QualityStandard]:
        results = []
        for sid in self._standards:
            try:
                latest = self.get(sid, version="latest")
                if active_only and not latest.is_active:
                    continue
                results.append(latest)
            except KeyError:
                pass
        return results
    
    def list_ids(self) -> list[str]:
        return list(self._standards.keys())
    
    def has(self, standard_id: str) -> bool:
        return standard_id in self._standards
    
    def count(self) -> int:
        return len(self._standards)
    
    def clear(self) -> None:
        self._standards.clear()
        self._tag_index.clear()
        self._load_defaults()
    
    def _parse_version(self, version: str) -> int:
        if version.startswith("v"):
            try:
                return int(version[1:])
            except ValueError:
                return 0
        return 0