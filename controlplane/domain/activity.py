"""Activity 契约定义。

注意: executionplane/activities/contracts.py 有此文件的独立副本。
两份定义必须保持字段一致（独立 pyproject 解耦原则）。
修改本文件时必须同步修改 executionplane 副本。
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
