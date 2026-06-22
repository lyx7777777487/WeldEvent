"""质量标准配置系统 — 可扩展的图像质量标准定义。

Source: WeldEvent架构设计讨论

设计原则:
  - 纯数据模型，无硬编码枚举
  - 通过Registry注册，支持动态添加
  - 支持从YAML/JSON/数据库加载
  - 支持继承覆盖（parent_standard_id）

使用场景:
  - 不同产线使用不同质量标准
  - Brain在Pipeline配置阶段选择标准
  - 支持运行时动态切换标准
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
    resolution_aspect_ratio: float | None = None  # 如 4:3 = 1.33
    
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
        "occlusion",           # 遮挡
        "lens_contamination",  # 镜头污染
        "foreign_object",      # 异物
        "reflection",          # 反光
        "abnormal_texture",    # 异常纹理
    ])
    deep_vision_max_latency_ms: int = 3000
    deep_vision_on_error: str = "fallback_to_rule"  # fallback_to_rule / mandatory_review / reject
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
    parent_standard_id: str | None = None  # 支持继承覆盖
    
    # ------------------------------------------------------------------
    # 序列化方法
    # ------------------------------------------------------------------
    
    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于存储/传输）"""
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
# 预置标准（可选默认配置）
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
        deep_vision_enabled=False,  # 禁用MLLM
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
    """质量标准注册表 — 可扩展的核心机制。
    
    支持多种注册方式:
      1. 代码注册: registry.register(standard)
      2. YAML加载: registry.load_from_yaml(path)
      3. JSON加载: registry.load_from_json(path)
      4. 数据库加载: registry.load_from_storage(storage_port)
      5. 动态API: registry.register_from_dict(data)
    
    查询方式:
      - 按ID: registry.get("macro_weld")
      - 按标签: registry.find_by_tags(["weld", "macro"])
      - 按版本: registry.get("macro_weld", version="v2")
      - 继承覆盖: registry.get_derived("custom_001")
    
    Usage::
        # 创建注册表
        registry = QualityStandardRegistry()
        
        # 获取预置标准
        standard = registry.get("macro_weld")
        
        # 注册自定义标准
        registry.register(QualityStandard(
            standard_id="my_custom",
            name="我的自定义标准",
            tags=["custom"],
            focus_laplacian_min=60.0,
        ))
        
        # 从YAML加载
        registry.load_from_yaml("/config/standards.yaml")
    """
    
    def __init__(self) -> None:
        # {standard_id: {version: QualityStandard}}
        self._standards: dict[str, dict[str, QualityStandard]] = {}
        # 标签索引: {tag: [standard_id]}
        self._tag_index: dict[str, list[str]] = {}
        
        # 加载预置标准
        self._load_defaults()
    
    def _load_defaults(self) -> None:
        """加载预置标准"""
        for standard in DEFAULT_STANDARDS.values():
            self.register(standard)
    
    # ------------------------------------------------------------------
    # 注册方法
    # ------------------------------------------------------------------
    
    def register(self, standard: QualityStandard) -> None:
        """注册一个质量标准"""
        sid = standard.standard_id
        ver = standard.version
        
        if sid not in self._standards:
            self._standards[sid] = {}
        self._standards[sid][ver] = standard
        
        # 更新标签索引
        for tag in standard.tags:
            if tag not in self._tag_index:
                self._tag_index[tag] = []
            if sid not in self._tag_index[tag]:
                self._tag_index[tag].append(sid)
    
    def register_from_dict(self, data: dict[str, Any]) -> None:
        """从字典注册"""
        standard = QualityStandard.from_dict(data)
        self.register(standard)
    
    def register_batch(self, standards: list[QualityStandard]) -> None:
        """批量注册"""
        for std in standards:
            self.register(std)
    
    # ------------------------------------------------------------------
    # 配置文件加载
    # ------------------------------------------------------------------
    
    def load_from_yaml(self, path: str) -> int:
        """从YAML文件加载标准配置
        
        YAML格式示例:
        ```yaml
        standards:
          - standard_id: macro_weld
            name: 宏观焊缝检测
            version: v1
            tags: [weld, macro, GMAW]
            resolution:
              min_width: 2048
              min_height: 1536
            exposure:
              min_gray: 45.0
              max_gray: 90.0
            focus:
              laplacian_min: 50.0
            thresholds:
              auto_pass: 0.85
              reject: 0.25
        ```
        
        Returns:
            注册的标准数量
        """
        try:
            import yaml
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            
            standards_data = data.get("standards", [])
            count = 0
            for std_data in standards_data:
                self.register_from_dict(std_data)
                count += 1
            return count
        except ImportError:
            raise RuntimeError("需要安装 PyYAML: pip install pyyaml")
    
    def load_from_json(self, path: str) -> int:
        """从JSON文件加载标准配置"""
        import json
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        standards_data = data.get("standards", [])
        count = 0
        for std_data in standards_data:
            self.register_from_dict(std_data)
            count += 1
        return count
    
    # ------------------------------------------------------------------
    # 查询方法
    # ------------------------------------------------------------------
    
    def get(
        self,
        standard_id: str,
        version: str = "latest",
    ) -> QualityStandard:
        """获取标准配置
        
        Args:
            standard_id: 标准ID
            version: 版本号，"latest" 返回最新版本
        
        Raises:
            KeyError: 标准不存在
        """
        if standard_id not in self._standards:
            raise KeyError(f"质量标准不存在: {standard_id}")
        
        versions = self._standards[standard_id]
        
        if version == "latest":
            # 返回版本号最大的（假设版本格式为 v1, v2, v3...）
            latest_ver = max(versions.keys(), key=lambda v: self._parse_version(v))
            return versions[latest_ver]
        
        if version not in versions:
            raise KeyError(f"标准版本不存在: {standard_id}@{version}")
        
        return versions[version]
    
    def find_by_tags(self, tags: list[str]) -> list[QualityStandard]:
        """按标签查找标准"""
        result_ids: set[str] = set()
        
        for tag in tags:
            if tag in self._tag_index:
                result_ids.update(self._tag_index[tag])
        
        # 返回每个ID的最新版本
        results = []
        for sid in result_ids:
            try:
                results.append(self.get(sid, version="latest"))
            except KeyError:
                pass
        return results
    
    def list_all(self, active_only: bool = True) -> list[QualityStandard]:
        """列出所有标准"""
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
        """列出所有标准ID"""
        return list(self._standards.keys())
    
    def list_versions(self, standard_id: str) -> list[str]:
        """列出某标准的所有版本"""
        if standard_id not in self._standards:
            return []
        return list(self._standards[standard_id].keys())
    
    # ------------------------------------------------------------------
    # 继承/覆盖机制
    # ------------------------------------------------------------------
    
    def get_derived(self, standard_id: str) -> QualityStandard:
        """获取继承后的标准（合并父标准配置）
        
        场景：用户基于 "macro_weld" 创建自定义标准 "custom_001",
              只覆盖部分参数，其余继承父标准。
        """
        standard = self.get(standard_id)
        
        if standard.parent_standard_id is None:
            return standard
        
        # 递归获取父标准
        parent = self.get_derived(standard.parent_standard_id)
        
        # 合并配置（子标准覆盖父标准）
        merged = self._merge_standards(parent, standard)
        return merged
    
    def _merge_standards(
        self,
        parent: QualityStandard,
        child: QualityStandard,
    ) -> QualityStandard:
        """合并父子标准配置"""
        parent_dict = parent.to_dict()
        child_dict = child.to_dict()
        
        # 深度合并：child的非None值覆盖parent
        merged = self._deep_merge(parent_dict, child_dict)
        
        # 保持child的标识信息
        merged["standard_id"] = child.standard_id
        merged["name"] = child.name
        merged["version"] = child.version
        
        return QualityStandard.from_dict(merged)
    
    def _deep_merge(self, base: dict, override: dict) -> dict:
        """深度合并字典"""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            elif value is not None:
                result[key] = value
        return result
    
    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    
    def _parse_version(self, version: str) -> int:
        """解析版本号为整数（v1 → 1, v2 → 2）"""
        if version.startswith("v"):
            try:
                return int(version[1:])
            except ValueError:
                return 0
        return 0
    
    def has(self, standard_id: str) -> bool:
        """检查标准是否存在"""
        return standard_id in self._standards
    
    def count(self) -> int:
        """统计标准数量"""
        return len(self._standards)
    
    def clear(self) -> None:
        """清空所有标准"""
        self._standards.clear()
        self._tag_index.clear()
        self._load_defaults()  # 重新加载预置标准