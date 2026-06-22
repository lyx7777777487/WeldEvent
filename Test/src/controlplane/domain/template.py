from dataclasses import dataclass, field
from enum import Enum

from .human_gate import HumanGateDefinition
from .policy import ExecutionPolicy


class MigrationStrategy(str, Enum):
    ROLLING = "rolling"
    DUAL_WRITE = "dual_write"
    STOP_AND_REPLACE = "stop_and_replace"


class TransitionType(str, Enum):
    SEQUENTIAL = "SEQUENTIAL"
    PARALLEL = "PARALLEL"
    JOIN = "JOIN"


@dataclass(frozen=True)
class ActivityBinding:
    activity_name: str
    activity_version: str = "latest"
    task_queue: str | None = None
    execution_target: str | None = None


@dataclass(frozen=True)
class WorkflowTemplate:
    id: str
    version: str
    control_points: list["ControlPointDefinition"]
    transitions: list["TransitionDefinition"]
    entry_point: str
    migration_strategy: MigrationStrategy | None = None


@dataclass(frozen=True)
class ControlPointDefinition:
    id: str
    name: str
    activity_binding: ActivityBinding | None
    execution_policy: ExecutionPolicy
    human_gate: HumanGateDefinition | None = None
    params: dict | None = None


@dataclass(frozen=True)
class TransitionDefinition:
    from_cp: str
    transition_type: TransitionType = TransitionType.SEQUENTIAL
    branches: list["BranchDefinition"] = field(default_factory=list)
    parallel_targets: list[str] = field(default_factory=list)
    join_condition: str | None = None


@dataclass(frozen=True)
class BranchDefinition:
    condition: str
    to_cp: str | None
