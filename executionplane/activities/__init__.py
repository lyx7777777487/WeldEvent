"""Activity模块 — Execution Plane核心执行单元。

Activity是被动调度的执行单元，不具备自主性：
  - 由L2 Control Plane调度执行
  - 通过WeldMap读写共享状态
  - 返回ActivityOutput给调度器

与Agent的区别：
  - Agent（L1 Brain）：自主决策、目标驱动、学习能力
  - Activity（L3 Execution）：被动执行、规则判断、无学习

Activity流水线:
  IQA → PPA → MEA → RDA → VDA → RVA

子模块结构:
  - iqa: 图像质量评估
  - ppa: 图像预处理
  - mea: 几何测量（已实现）
  - rda: 表面缺陷识别（已实现）
"""

from .base import (
    BaseActivity,
    ActivityInput,
    ActivityOutput,
    ActivityStatus,
    ActivityMetadata,
)

# 导入子模块
from .iqa import IqaActivity
from .mea import MeaActivity
from .ppa import PpaActivity
from .rda import RdaActivity

__all__ = [
    "BaseActivity",
    "ActivityInput",
    "ActivityOutput",
    "ActivityStatus",
    "ActivityMetadata",
    "IqaActivity",
    "MeaActivity",
    "PpaActivity",
    "RdaActivity",
]