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
