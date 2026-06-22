"""配置模块 — Execution Plane配置管理。

包含:
  - QualityStandard: 图像质量标准配置
  - QualityStandardRegistry: 质量标准注册表
"""

from .quality_standard import (
    QualityStandard,
    QualityStandardRegistry,
)

__all__ = [
    "QualityStandard",
    "QualityStandardRegistry",
]