"""Activity 契约定义（executionplane 独立副本）。

本文件是 controlplane/domain/activity.py 的独立副本，用于打破
L3 → L2 的直接 import（端口/适配器隔离违规）。

独立 pyproject 解耦原则:
  - executionplane 有自己的 pyproject.toml，不声明对 controlplane 的依赖
  - 两份定义必须保持字段一致
  - 修改 controlplane/domain/activity.py 时必须同步修改本文件

字段来源: controlplane/domain/activity.py
"""

from dataclasses import dataclass
from enum import Enum


class ActivityStatus(str, Enum):
    OK = "OK"
    MARGINAL = "MARGINAL"
    NG = "NG"
    ERROR = "ERROR"


@dataclass
class ActivityInput:
    control_point_id: str
    workflow_context: dict
    params: dict | None = None


@dataclass
class ActivityOutput:
    status: ActivityStatus
    data: dict | None = None
    error: str | None = None


__all__ = [
    "ActivityStatus",
    "ActivityInput",
    "ActivityOutput",
]
