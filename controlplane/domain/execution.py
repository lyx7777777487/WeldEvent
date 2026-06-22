from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .activity import ActivityOutput


class WorkflowStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TERMINATED = "TERMINATED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


class ControlPointStatus(str, Enum):
    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    WAITING_GATE = "WAITING_GATE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass
class WorkflowContext:
    template_id: str
    template_version: str
    workflow_id: str
    metadata: dict = field(default_factory=dict)


@dataclass
class StateTransition:
    from_cp: str
    to_cp: str | None
    condition: str
    timestamp: datetime
    activity_output: ActivityOutput | None = None


@dataclass
class ControlPointInstance:
    cp_id: str
    status: ControlPointStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    activity_output: ActivityOutput | None = None


@dataclass
class WorkflowState:
    workflow_context: WorkflowContext
    current_cp: str | None = None
    status: WorkflowStatus = WorkflowStatus.RUNNING
    cp_instances: dict[str, ControlPointInstance] = field(default_factory=dict)
    transitions: list[StateTransition] = field(default_factory=list)
