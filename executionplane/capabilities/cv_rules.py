"""CV 规则引擎 — 确定性图像质量检查。

Source: Complete_architecture_V1.docx §3.1 (IQA Agent — 规则层)

规则层使用 OpenCV 确定性函数，延迟 <10ms:
  - 分辨率检查: 像素计数
  - 曝光检查: 灰度均值
  - 对焦检查: Laplacian 方差
  - 完整性检查: 焊缝区域是否被裁切

所有规则输入相同输出必然相同，不引入非确定性因素。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
from numpy.typing import NDArray


# ---------------------------------------------------------------------------
# 规则结果
# ---------------------------------------------------------------------------

class RuleSeverity(str, Enum):
    """规则违反严重程度。"""
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass
class RuleResult:
    """单条规则的执行结果。"""
    rule_id: str
    passed: bool
    severity: RuleSeverity = RuleSeverity.INFO
    value: float = 0.0
    threshold: float = 0.0
    detail: str = ""


@dataclass
class RuleEngineOutput:
    """规则引擎综合输出。"""
    results: list[RuleResult] = field(default_factory=list)
    overall_confidence: float = 0.0      # 综合置信度 [0, 1]
    route_decision: str = "AUTO_PASS"   # AUTO_PASS / SUGGEST_REVIEW / MANDATORY_REVIEW / REJECT
    
    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)
    
    @property
    def error_count(self) -> int:
        return sum(1 for r in self.results if not r.passed and r.severity == RuleSeverity.ERROR)
    
    @property
    def warning_count(self) -> int:
        return sum(1 for r in self.results if not r.passed and r.severity == RuleSeverity.WARNING)


# ---------------------------------------------------------------------------
# 规则定义 (可配置)
# ---------------------------------------------------------------------------

@dataclass
class ResolutionRule:
    """分辨率检查规则。"""
    rule_id: str = "IQA-RES"
    min_width: int = 2048
    min_height: int = 1536


@dataclass
class ExposureRule:
    """曝光检查规则。"""
    rule_id: str = "IQA-EXP"
    min_mean_gray: float = 45.0
    max_mean_gray: float = 90.0


@dataclass
class FocusRule:
    """对焦检查规则（Laplacian 方差）。"""
    rule_id: str = "IQA-FOCUS"
    laplacian_threshold: float = 50.0


@dataclass
class CompletenessRule:
    """完整性检查规则。"""
    rule_id: str = "IQA-COMP"
    min_weld_region_ratio: float = 0.3  # 焊缝区域占比下限


# ---------------------------------------------------------------------------
# 规则引擎接口与默认实现
# ---------------------------------------------------------------------------

class CVRuleChecker(ABC):
    """CV 规则检查器抽象 — 可替换底层实现。
    
    默认实现用 OpenCV/numpy，测试可用 Mock 替代。
    """

    @abstractmethod
    async def check_resolution(
        self, image: NDArray[np.uint8], rule: ResolutionRule | None = None
    ) -> RuleResult:
        """检查图像分辨率是否满足要求。"""
        ...

    @abstractmethod
    async def check_exposure(
        self, image: NDArray[np.uint8], rule: ExposureRule | None = None
    ) -> RuleResult:
        """检查曝光是否在合理范围。"""
        ...

    @abstractmethod
    async def check_focus(
        self, image: NDArray[np.uint8], rule: FocusRule | None = None
    ) -> RuleResult:
        """检查对焦质量（Laplacian 方差）。"""
        ...

    @abstractmethod
    async def check_completeness(
        self, image: NDArray[np.uint8], rule: CompletenessRule | None = None
    ) -> RuleResult:
        """检查焊缝区域完整性（未被过度裁切/遮挡）。"""
        ...

    @abstractmethod
    async def run_all_rules(
        self, image: NDArray[np.uint8],
        confidence_threshold: float = 0.85,
    ) -> RuleEngineOutput:
        """执行全部规则并返回综合结果。"""
        ...
