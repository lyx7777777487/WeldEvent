# L2 Control Plane Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the L2 Control Plane Runtime skeleton for the Industrial Agent OS using Temporal Python SDK.

**Architecture:** Data-driven interpreter workflow (`TemplateWorkflow`) that loads workflow templates by reference, executes Control Points as states, dispatches Activities through structured bindings, and supports Human Gates via Update/Query/Await. All orchestration via Temporal-native primitives.

**Tech Stack:** Python 3.12+, temporalio 1.27+, pydantic 2.0+, pytest

---

## File Structure

```text
controlplane/
├── __init__.py
├── domain/
│   ├── __init__.py
│   ├── template.py          # WorkflowTemplate, ControlPointDefinition, TransitionDefinition, BranchDefinition, ActivityBinding, TransitionType, MigrationStrategy
│   ├── execution.py         # WorkflowStatus, ControlPointStatus, WorkflowContext, StateTransition, ControlPointInstance, WorkflowState
│   ├── activity.py          # ActivityStatus, ActivityInput, ActivityOutput
│   ├── policy.py            # RetryPolicy, TimeoutPolicy, ExecutionPolicy
│   └── human_gate.py        # GateAction, HumanGateDefinition
├── runtime/
│   ├── __init__.py
│   ├── template_workflow.py # TemplateWorkflow (core interpreter)
│   ├── control_point.py     # Control Point execution helpers
│   ├── transition.py        # Transition resolver
│   └── human_gate.py        # Human Gate runtime helpers
├── adapter/
│   ├── __init__.py
│   └── base.py              # ActivityAdapter ABC
├── infrastructure/
│   ├── __init__.py
│   └── registry.py          # ActivityRegistry
├── port/
│   ├── __init__.py
│   ├── event.py             # EventPort ABC
│   ├── data.py              # DataPort ABC
│   └── command.py           # CommandPort ABC
├── repo/
│   ├── __init__.py
│   ├── state.py             # StateRepository ABC
│   ├── audit.py             # AuditRepository ABC
│   └── template.py          # TemplateRepository ABC
├── worker.py                # Worker bootstrap
├── config.py                # Configuration
├── tests/
│   ├── __init__.py
│   ├── test_domain_template.py
│   ├── test_domain_execution.py
│   ├── test_domain_activity.py
│   ├── test_registry.py
│   ├── test_transition.py
│   ├── test_control_point.py
│   ├── test_human_gate.py
│   └── test_template_workflow.py
├── pyproject.toml
└── requirements.txt
```

---

### Task 1: Project Scaffolding

**Files:**
- Create: `controlplane/__init__.py`
- Create: `controlplane/domain/__init__.py`
- Create: `controlplane/runtime/__init__.py`
- Create: `controlplane/adapter/__init__.py`
- Create: `controlplane/infrastructure/__init__.py`
- Create: `controlplane/port/__init__.py`
- Create: `controlplane/repo/__init__.py`
- Create: `controlplane/tests/__init__.py`
- Create: `controlplane/pyproject.toml`
- Create: `controlplane/requirements.txt`

- [ ] **Step 1: Create directory structure and empty __init__.py files**

```bash
mkdir -p controlplane/{domain,runtime,adapter,infrastructure,port,repo,tests}
touch controlplane/__init__.py
touch controlplane/domain/__init__.py
touch controlplane/runtime/__init__.py
touch controlplane/adapter/__init__.py
touch controlplane/infrastructure/__init__.py
touch controlplane/port/__init__.py
touch controlplane/repo/__init__.py
touch controlplane/tests/__init__.py
```

- [ ] **Step 2: Create pyproject.toml**

```toml
[project]
name = "industrial-agent-controlplane"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "temporalio>=1.27.0",
    "pydantic>=2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 3: Create requirements.txt**

```
temporalio>=1.27.0
pydantic>=2.0
```

- [ ] **Step 4: Install dependencies and verify**

Run: `cd controlplane && pip install -e ".[dev]" && python -c "import temporalio; print(temporalio.__version__)"`

Expected: temporalio version printed, no errors

- [ ] **Step 5: Commit**

```bash
git add controlplane/
git commit -m "feat(controlplane): scaffold project structure with pyproject.toml"
```

---

### Task 2: Domain Model — activity.py, policy.py, human_gate.py

**Files:**
- Create: `controlplane/domain/activity.py`
- Create: `controlplane/domain/policy.py`
- Create: `controlplane/domain/human_gate.py`
- Test: `controlplane/tests/test_domain_activity.py`

- [ ] **Step 1: Write failing test for activity domain models**

Create `controlplane/tests/test_domain_activity.py`:

```python
from controlplane.domain.activity import ActivityStatus, ActivityInput, ActivityOutput


def test_activity_status_values():
    assert ActivityStatus.OK.value == "OK"
    assert ActivityStatus.MARGINAL.value == "MARGINAL"
    assert ActivityStatus.NG.value == "NG"
    assert ActivityStatus.ERROR.value == "ERROR"


def test_activity_input_creation():
    inp = ActivityInput(
        control_point_id="CP0",
        workflow_context={"template_id": "t1"},
        params={"key": "val"},
    )
    assert inp.control_point_id == "CP0"
    assert inp.params["key"] == "val"


def test_activity_input_default_params():
    inp = ActivityInput(control_point_id="CP0", workflow_context={})
    assert inp.params is None


def test_activity_output_creation():
    out = ActivityOutput(status=ActivityStatus.OK, data={"result": 42})
    assert out.status == ActivityStatus.OK
    assert out.data["result"] == 42
    assert out.error is None


def test_activity_output_error():
    out = ActivityOutput(status=ActivityStatus.ERROR, error="timeout")
    assert out.status == ActivityStatus.ERROR
    assert out.data is None
    assert out.error == "timeout"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd controlplane && python -m pytest tests/test_domain_activity.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'controlplane.domain.activity'`

- [ ] **Step 3: Create activity.py**

Create `controlplane/domain/activity.py`:

```python
from dataclasses import dataclass
from enum import Enum


class ActivityStatus(Enum):
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd controlplane && python -m pytest tests/test_domain_activity.py -v`

Expected: 5 passed

- [ ] **Step 5: Create policy.py**

Create `controlplane/domain/policy.py`:

```python
from dataclasses import dataclass, field
from datetime import timedelta


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_interval: timedelta = timedelta(seconds=1)
    backoff_coefficient: float = 2.0
    maximum_interval: timedelta = timedelta(seconds=100)
    non_retryable_error_types: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TimeoutPolicy:
    schedule_to_close: timedelta | None = None
    schedule_to_start: timedelta | None = None
    start_to_close: timedelta | None = None
    heartbeat: timedelta | None = None


@dataclass(frozen=True)
class ExecutionPolicy:
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_policy: TimeoutPolicy = field(default_factory=TimeoutPolicy)
```

- [ ] **Step 6: Create human_gate.py**

Create `controlplane/domain/human_gate.py`:

```python
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum


class GateAction(Enum):
    CONTINUE = "CONTINUE"
    REDIRECT = "REDIRECT"
    TERMINATE = "TERMINATE"


@dataclass(frozen=True)
class HumanGateDefinition:
    timeout: timedelta | None = None
    redirect_target: str | None = None
```

- [ ] **Step 7: Verify all domain leaf modules import cleanly**

Run: `cd controlplane && python -c "from controlplane.domain.activity import ActivityStatus, ActivityInput, ActivityOutput; from controlplane.domain.policy import ExecutionPolicy, RetryPolicy, TimeoutPolicy; from controlplane.domain.human_gate import GateAction, HumanGateDefinition; print('OK')"`

Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add controlplane/domain/activity.py controlplane/domain/policy.py controlplane/domain/human_gate.py controlplane/tests/test_domain_activity.py
git commit -m "feat(controlplane): add domain models for activity, policy, and human_gate"
```

---

### Task 3: Domain Model — template.py and execution.py

**Files:**
- Create: `controlplane/domain/template.py`
- Create: `controlplane/domain/execution.py`
- Test: `controlplane/tests/test_domain_template.py`
- Test: `controlplane/tests/test_domain_execution.py`

- [ ] **Step 1: Write failing tests for template domain models**

Create `controlplane/tests/test_domain_template.py`:

```python
from datetime import timedelta

from controlplane.domain.template import (
    ActivityBinding,
    BranchDefinition,
    ControlPointDefinition,
    MigrationStrategy,
    TransitionDefinition,
    TransitionType,
    WorkflowTemplate,
)
from controlplane.domain.human_gate import HumanGateDefinition
from controlplane.domain.policy import ExecutionPolicy


def test_activity_binding_defaults():
    b = ActivityBinding(activity_name="mea_activity")
    assert b.activity_name == "mea_activity"
    assert b.activity_version == "latest"
    assert b.task_queue is None
    assert b.execution_target is None


def test_activity_binding_full():
    b = ActivityBinding(
        activity_name="mea_activity",
        activity_version="v2",
        task_queue="gpu-queue",
        execution_target="gpu",
    )
    assert b.activity_version == "v2"
    assert b.task_queue == "gpu-queue"
    assert b.execution_target == "gpu"


def test_control_point_with_binding():
    cp = ControlPointDefinition(
        id="CP0",
        name="Incoming Quality Assessment",
        activity_binding=ActivityBinding(activity_name="iqa_activity"),
        execution_policy=ExecutionPolicy(),
    )
    assert cp.id == "CP0"
    assert cp.activity_binding.activity_name == "iqa_activity"
    assert cp.human_gate is None


def test_control_point_without_binding():
    cp = ControlPointDefinition(
        id="CP_DECISION",
        name="Decision Point",
        activity_binding=None,
        execution_policy=ExecutionPolicy(),
    )
    assert cp.activity_binding is None


def test_control_point_with_human_gate():
    cp = ControlPointDefinition(
        id="CP4",
        name="Visual Inspection Review",
        activity_binding=ActivityBinding(activity_name="vda_activity"),
        execution_policy=ExecutionPolicy(),
        human_gate=HumanGateDefinition(timeout=timedelta(hours=1), redirect_target="CP5"),
    )
    assert cp.human_gate.timeout == timedelta(hours=1)
    assert cp.human_gate.redirect_target == "CP5"


def test_transition_type_enum():
    assert TransitionType.SEQUENTIAL.value == "SEQUENTIAL"
    assert TransitionType.PARALLEL.value == "PARALLEL"
    assert TransitionType.JOIN.value == "JOIN"


def test_transition_definition_sequential():
    td = TransitionDefinition(
        from_cp="CP0",
        transition_type=TransitionType.SEQUENTIAL,
        branches=[
            BranchDefinition(condition="OK", to_cp="CP1"),
            BranchDefinition(condition="default", to_cp="CP5"),
        ],
    )
    assert td.from_cp == "CP0"
    assert len(td.branches) == 2
    assert td.parallel_targets == []


def test_transition_definition_parallel():
    td = TransitionDefinition(
        from_cp="CP2",
        transition_type=TransitionType.PARALLEL,
        parallel_targets=["CP2A", "CP2B", "CP2C"],
    )
    assert td.transition_type == TransitionType.PARALLEL
    assert td.parallel_targets == ["CP2A", "CP2B", "CP2C"]


def test_workflow_template_creation():
    template = WorkflowTemplate(
        id="weld_inspection",
        version="1.0",
        control_points=[
            ControlPointDefinition(
                id="CP0",
                name="IQA",
                activity_binding=ActivityBinding(activity_name="iqa_activity"),
                execution_policy=ExecutionPolicy(),
            ),
        ],
        transitions=[
            TransitionDefinition(
                from_cp="CP0",
                branches=[BranchDefinition(condition="OK", to_cp=None)],
            ),
        ],
        entry_point="CP0",
    )
    assert template.id == "weld_inspection"
    assert template.version == "1.0"
    assert template.entry_point == "CP0"
    assert template.migration_strategy is None


def test_workflow_template_with_migration():
    template = WorkflowTemplate(
        id="weld_inspection",
        version="2.0",
        control_points=[],
        transitions=[],
        entry_point="CP0",
        migration_strategy=MigrationStrategy.ROLLING,
    )
    assert template.migration_strategy == MigrationStrategy.ROLLING


def test_branch_definition_end_workflow():
    b = BranchDefinition(condition="OK", to_cp=None)
    assert b.to_cp is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd controlplane && python -m pytest tests/test_domain_template.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'controlplane.domain.template'`

- [ ] **Step 3: Create template.py**

Create `controlplane/domain/template.py`:

```python
from dataclasses import dataclass, field
from enum import Enum

from .human_gate import HumanGateDefinition
from .policy import ExecutionPolicy


class MigrationStrategy(Enum):
    ROLLING = "rolling"
    DUAL_WRITE = "dual_write"
    STOP_AND_REPLACE = "stop_and_replace"


class TransitionType(Enum):
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd controlplane && python -m pytest tests/test_domain_template.py -v`

Expected: 12 passed

- [ ] **Step 5: Write failing tests for execution domain models**

Create `controlplane/tests/test_domain_execution.py`:

```python
from datetime import datetime, timezone

from controlplane.domain.execution import (
    ControlPointInstance,
    ControlPointStatus,
    StateTransition,
    WorkflowContext,
    WorkflowState,
    WorkflowStatus,
)


def test_workflow_status_values():
    assert WorkflowStatus.RUNNING.value == "RUNNING"
    assert WorkflowStatus.COMPLETED.value == "COMPLETED"
    assert WorkflowStatus.FAILED.value == "FAILED"
    assert WorkflowStatus.TERMINATED.value == "TERMINATED"


def test_control_point_status_values():
    assert ControlPointStatus.PENDING.value == "PENDING"
    assert ControlPointStatus.EXECUTING.value == "EXECUTING"
    assert ControlPointStatus.WAITING_GATE.value == "WAITING_GATE"
    assert ControlPointStatus.COMPLETED.value == "COMPLETED"


def test_workflow_context():
    ctx = WorkflowContext(
        template_id="weld_inspection",
        template_version="1.0",
        workflow_id="wf-001",
    )
    assert ctx.template_id == "weld_inspection"
    assert ctx.metadata == {}


def test_state_transition():
    t = StateTransition(
        from_cp="CP0",
        to_cp="CP1",
        condition="OK",
        timestamp=datetime.now(timezone.utc),
    )
    assert t.from_cp == "CP0"
    assert t.to_cp == "CP1"
    assert t.activity_output is None


def test_control_point_instance():
    cpi = ControlPointInstance(cp_id="CP0", status=ControlPointStatus.EXECUTING)
    assert cpi.started_at is None
    assert cpi.activity_output is None


def test_workflow_state():
    ctx = WorkflowContext(
        template_id="weld_inspection",
        template_version="1.0",
        workflow_id="wf-001",
    )
    state = WorkflowState(workflow_context=ctx)
    assert state.current_cp is None
    assert state.status == WorkflowStatus.RUNNING
    assert state.cp_instances == {}
    assert state.transitions == []
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd controlplane && python -m pytest tests/test_domain_execution.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'controlplane.domain.execution'`

- [ ] **Step 7: Create execution.py**

Create `controlplane/domain/execution.py`:

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .activity import ActivityOutput


class WorkflowStatus(Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TERMINATED = "TERMINATED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


class ControlPointStatus(Enum):
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
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd controlplane && python -m pytest tests/test_domain_execution.py -v`

Expected: 6 passed

- [ ] **Step 9: Commit**

```bash
git add controlplane/domain/template.py controlplane/domain/execution.py controlplane/tests/test_domain_template.py controlplane/tests/test_domain_execution.py
git commit -m "feat(controlplane): add domain models for template and execution"
```

---

### Task 4: Activity Adapter and Activity Registry

**Files:**
- Create: `controlplane/adapter/base.py`
- Create: `controlplane/infrastructure/registry.py`
- Test: `controlplane/tests/test_registry.py`

- [ ] **Step 1: Create adapter/base.py**

Create `controlplane/adapter/base.py`:

```python
from abc import ABC, abstractmethod

from controlplane.domain.activity import ActivityInput, ActivityOutput


class ActivityAdapter(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def version(self) -> str: ...

    @abstractmethod
    async def execute(self, input: ActivityInput) -> ActivityOutput: ...
```

- [ ] **Step 2: Write failing tests for ActivityRegistry**

Create `controlplane/tests/test_registry.py`:

```python
import pytest

from controlplane.adapter.base import ActivityAdapter
from controlplane.domain.activity import ActivityInput, ActivityOutput, ActivityStatus
from controlplane.domain.template import ActivityBinding
from controlplane.infrastructure.registry import ActivityRegistry


class MockAdapter(ActivityAdapter):
    def __init__(self, name: str, version: str, status: ActivityStatus = ActivityStatus.OK):
        self._name = name
        self._version = version
        self._status = status

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return self._version

    async def execute(self, input: ActivityInput) -> ActivityOutput:
        return ActivityOutput(status=self._status, data={"adapter": self._name})


def test_register_single_adapter():
    registry = ActivityRegistry()
    adapter = MockAdapter("iqa_activity", "v1")
    registry.register(adapter)
    found = registry.get("iqa_activity")
    assert found.name == "iqa_activity"
    assert found.version == "v1"


def test_register_multiple_versions():
    registry = ActivityRegistry()
    registry.register(MockAdapter("mea_activity", "v1"))
    registry.register(MockAdapter("mea_activity", "v2"))
    latest = registry.get("mea_activity")
    assert latest.version == "v2"


def test_get_specific_version():
    registry = ActivityRegistry()
    registry.register(MockAdapter("mea_activity", "v1"))
    registry.register(MockAdapter("mea_activity", "v2"))
    found = registry.get("mea_activity", "v1")
    assert found.version == "v1"


def test_get_nonexistent_raises():
    registry = ActivityRegistry()
    with pytest.raises(KeyError, match="not found"):
        registry.get("nonexistent")


def test_get_nonexistent_version_raises():
    registry = ActivityRegistry()
    registry.register(MockAdapter("mea_activity", "v1"))
    with pytest.raises(KeyError, match="version not found"):
        registry.get("mea_activity", "v99")


def test_bind_activity_binding():
    registry = ActivityRegistry()
    registry.register(MockAdapter("mea_activity", "v1"))
    registry.register(MockAdapter("mea_activity", "v2"))

    binding_v1 = ActivityBinding(activity_name="mea_activity", activity_version="v1")
    adapter = registry.bind(binding_v1)
    assert adapter.version == "v1"

    binding_latest = ActivityBinding(activity_name="mea_activity")
    adapter = registry.bind(binding_latest)
    assert adapter.version == "v2"


def test_bind_nonexistent_raises():
    registry = ActivityRegistry()
    binding = ActivityBinding(activity_name="nonexistent")
    with pytest.raises(KeyError):
        registry.bind(binding)


def test_all_returns_all_adapters():
    registry = ActivityRegistry()
    registry.register(MockAdapter("iqa_activity", "v1"))
    registry.register(MockAdapter("mea_activity", "v1"))
    registry.register(MockAdapter("mea_activity", "v2"))
    all_adapters = registry.all()
    assert len(all_adapters) == 3
    names = {a.name for a in all_adapters}
    assert names == {"iqa_activity", "mea_activity"}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd controlplane && python -m pytest tests/test_registry.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'controlplane.infrastructure.registry'`

- [ ] **Step 4: Create infrastructure/registry.py**

Create `controlplane/infrastructure/registry.py`:

```python
from controlplane.adapter.base import ActivityAdapter
from controlplane.domain.template import ActivityBinding


class ActivityRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, dict[str, ActivityAdapter]] = {}

    def register(self, adapter: ActivityAdapter) -> None:
        if adapter.name not in self._adapters:
            self._adapters[adapter.name] = {}
        self._adapters[adapter.name][adapter.version] = adapter

    def get(self, name: str, version: str = "latest") -> ActivityAdapter:
        versions = self._adapters.get(name)
        if not versions:
            raise KeyError(f"Activity adapter not found: {name}")
        if version == "latest":
            return max(versions.values(), key=lambda a: a.version)
        adapter = versions.get(version)
        if not adapter:
            raise KeyError(f"Activity adapter version not found: {name}@{version}")
        return adapter

    def bind(self, binding: ActivityBinding) -> ActivityAdapter:
        return self.get(binding.activity_name, binding.activity_version)

    def all(self) -> list[ActivityAdapter]:
        result = []
        for versions in self._adapters.values():
            result.extend(versions.values())
        return result
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd controlplane && python -m pytest tests/test_registry.py -v`

Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
git add controlplane/adapter/base.py controlplane/infrastructure/registry.py controlplane/tests/test_registry.py
git commit -m "feat(controlplane): add ActivityAdapter base class and ActivityRegistry"
```

---

### Task 5: External Ports and Repository Abstractions

**Files:**
- Create: `controlplane/port/event.py`
- Create: `controlplane/port/data.py`
- Create: `controlplane/port/command.py`
- Create: `controlplane/repo/state.py`
- Create: `controlplane/repo/audit.py`
- Create: `controlplane/repo/template.py`

- [ ] **Step 1: Create port/event.py**

```python
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any


class EventPort(ABC):
    @abstractmethod
    async def publish(self, event: dict[str, Any]) -> None: ...

    @abstractmethod
    async def subscribe(self, event_type: str) -> AsyncIterator[dict[str, Any]]: ...
```

- [ ] **Step 2: Create port/data.py**

```python
from abc import ABC, abstractmethod
from typing import Any


class DataPort(ABC):
    @abstractmethod
    async def read(self, key: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def write(self, key: str, value: dict[str, Any]) -> None: ...
```

- [ ] **Step 3: Create port/command.py**

```python
from abc import ABC, abstractmethod
from typing import Any


class CommandPort(ABC):
    @abstractmethod
    async def send(self, command: dict[str, Any]) -> dict[str, Any]: ...
```

- [ ] **Step 4: Create repo/state.py**

```python
from abc import ABC, abstractmethod
from typing import Any


class StateRepository(ABC):
    @abstractmethod
    async def get(self, key: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def put(self, key: str, value: dict[str, Any]) -> None: ...

    @abstractmethod
    async def query(self, criteria: dict[str, Any]) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...
```

- [ ] **Step 5: Create repo/audit.py**

```python
from abc import ABC, abstractmethod
from typing import Any


class AuditRepository(ABC):
    @abstractmethod
    async def record(self, entry: dict[str, Any]) -> None: ...

    @abstractmethod
    async def query(self, criteria: dict[str, Any]) -> list[dict[str, Any]]: ...
```

- [ ] **Step 6: Create repo/template.py**

```python
from abc import ABC, abstractmethod

from controlplane.domain.template import WorkflowTemplate


class TemplateRepository(ABC):
    @abstractmethod
    async def save(self, template: WorkflowTemplate) -> None: ...

    @abstractmethod
    async def load(self, template_id: str, version: str) -> WorkflowTemplate | None: ...

    @abstractmethod
    async def list_versions(self, template_id: str) -> list[str]: ...
```

- [ ] **Step 7: Verify all ports and repos import cleanly**

Run: `cd controlplane && python -c "from controlplane.port.event import EventPort; from controlplane.port.data import DataPort; from controlplane.port.command import CommandPort; from controlplane.repo.state import StateRepository; from controlplane.repo.audit import AuditRepository; from controlplane.repo.template import TemplateRepository; print('OK')"`

Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add controlplane/port/ controlplane/repo/
git commit -m "feat(controlplane): add external port and repository abstractions"
```

---

### Task 6: Transition Resolver

**Files:**
- Create: `controlplane/runtime/transition.py`
- Test: `controlplane/tests/test_transition.py`

- [ ] **Step 1: Write failing tests for transition resolver**

Create `controlplane/tests/test_transition.py`:

```python
import pytest

from controlplane.domain.activity import ActivityOutput, ActivityStatus
from controlplane.domain.template import (
    ActivityBinding,
    BranchDefinition,
    ControlPointDefinition,
    TransitionDefinition,
    TransitionType,
    WorkflowTemplate,
)
from controlplane.domain.policy import ExecutionPolicy
from controlplane.runtime.transition import TransitionResolver


def _make_template(transitions, entry="CP0"):
    return WorkflowTemplate(
        id="test", version="1.0", control_points=[], transitions=transitions, entry_point=entry,
    )


def test_resolve_sequential_ok():
    template = _make_template([
        TransitionDefinition(
            from_cp="CP0",
            branches=[
                BranchDefinition(condition="OK", to_cp="CP1"),
                BranchDefinition(condition="NG", to_cp="CP5"),
            ],
        ),
    ])
    resolver = TransitionResolver(template)
    assert resolver.resolve_next("CP0", "OK") == "CP1"


def test_resolve_sequential_ng():
    template = _make_template([
        TransitionDefinition(
            from_cp="CP0",
            branches=[
                BranchDefinition(condition="OK", to_cp="CP1"),
                BranchDefinition(condition="NG", to_cp="CP5"),
            ],
        ),
    ])
    resolver = TransitionResolver(template)
    assert resolver.resolve_next("CP0", "NG") == "CP5"


def test_resolve_default_branch():
    template = _make_template([
        TransitionDefinition(
            from_cp="CP4",
            branches=[
                BranchDefinition(condition="OK", to_cp="CP6"),
                BranchDefinition(condition="default", to_cp="CP5"),
            ],
        ),
    ])
    resolver = TransitionResolver(template)
    assert resolver.resolve_next("CP4", "MARGINAL") == "CP5"
    assert resolver.resolve_next("CP4", "NG") == "CP5"


def test_resolve_no_transition_returns_none():
    template = _make_template([])
    resolver = TransitionResolver(template)
    assert resolver.resolve_next("CP99", "OK") is None


def test_resolve_no_matching_branch_returns_none():
    template = _make_template([
        TransitionDefinition(
            from_cp="CP0",
            branches=[BranchDefinition(condition="OK", to_cp="CP1")],
        ),
    ])
    resolver = TransitionResolver(template)
    assert resolver.resolve_next("CP0", "NG") is None


def test_resolve_end_workflow():
    template = _make_template([
        TransitionDefinition(
            from_cp="CP6",
            branches=[BranchDefinition(condition="OK", to_cp=None)],
        ),
    ])
    resolver = TransitionResolver(template)
    assert resolver.resolve_next("CP6", "OK") is None


def test_resolve_parallel_raises_not_implemented():
    template = _make_template([
        TransitionDefinition(
            from_cp="CP2",
            transition_type=TransitionType.PARALLEL,
            parallel_targets=["CP2A", "CP2B"],
        ),
    ])
    resolver = TransitionResolver(template)
    with pytest.raises(NotImplementedError, match="Parallel transition"):
        resolver.resolve_next("CP2", "OK")


def test_result_to_condition_ok():
    resolver = TransitionResolver(_make_template([]))
    result = ActivityOutput(status=ActivityStatus.OK)
    assert resolver.result_to_condition(result) == "OK"


def test_result_to_condition_marginal():
    resolver = TransitionResolver(_make_template([]))
    result = ActivityOutput(status=ActivityStatus.MARGINAL)
    assert resolver.result_to_condition(result) == "MARGINAL"


def test_result_to_condition_none():
    resolver = TransitionResolver(_make_template([]))
    assert resolver.result_to_condition(None) == "default"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd controlplane && python -m pytest tests/test_transition.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'controlplane.runtime.transition'`

- [ ] **Step 3: Create transition.py**

Create `controlplane/runtime/transition.py`:

```python
from controlplane.domain.activity import ActivityOutput
from controlplane.domain.template import TransitionType, WorkflowTemplate


class TransitionResolver:
    def __init__(self, template: WorkflowTemplate) -> None:
        self._template = template

    def resolve_next(self, from_cp: str, condition: str) -> str | None:
        for transition in self._template.transitions:
            if transition.from_cp != from_cp:
                continue

            if transition.transition_type == TransitionType.PARALLEL:
                raise NotImplementedError(
                    "Parallel transition execution not yet implemented. "
                    "Domain model is ready for future extension."
                )

            for branch in transition.branches:
                if branch.condition == condition:
                    return branch.to_cp

            for branch in transition.branches:
                if branch.condition == "default":
                    return branch.to_cp

        return None

    def result_to_condition(self, result: ActivityOutput | None) -> str:
        if result is None:
            return "default"
        return result.status.value
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd controlplane && python -m pytest tests/test_transition.py -v`

Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add controlplane/runtime/transition.py controlplane/tests/test_transition.py
git commit -m "feat(controlplane): add TransitionResolver for sequential and parallel transitions"
```

---

### Task 7: Control Point Execution Helpers

**Files:**
- Create: `controlplane/runtime/control_point.py`
- Test: `controlplane/tests/test_control_point.py`

- [ ] **Step 1: Write failing tests for control point helpers**

Create `controlplane/tests/test_control_point.py`:

```python
from datetime import datetime, timezone

from controlplane.domain.activity import ActivityOutput, ActivityStatus
from controlplane.domain.execution import (
    ControlPointInstance,
    ControlPointStatus,
    WorkflowContext,
    WorkflowState,
    WorkflowStatus,
)
from controlplane.runtime.control_point import ControlPointExecutor


def _make_state() -> WorkflowState:
    ctx = WorkflowContext(template_id="t", template_version="1.0", workflow_id="w")
    return WorkflowState(workflow_context=ctx)


def test_enter_cp():
    state = _make_state()
    executor = ControlPointExecutor()
    executor.enter_cp(state, "CP0")
    assert "CP0" in state.cp_instances
    assert state.cp_instances["CP0"].status == ControlPointStatus.EXECUTING
    assert state.cp_instances["CP0"].started_at is not None
    assert state.current_cp == "CP0"


def test_complete_cp():
    state = _make_state()
    executor = ControlPointExecutor()
    executor.enter_cp(state, "CP0")
    result = ActivityOutput(status=ActivityStatus.OK, data={"val": 1})
    executor.complete_cp(state, "CP0", result)
    assert state.cp_instances["CP0"].status == ControlPointStatus.COMPLETED
    assert state.cp_instances["CP0"].completed_at is not None
    assert state.cp_instances["CP0"].activity_output == result


def test_fail_cp():
    state = _make_state()
    executor = ControlPointExecutor()
    executor.enter_cp(state, "CP0")
    result = ActivityOutput(status=ActivityStatus.ERROR, error="timeout")
    executor.fail_cp(state, "CP0", result)
    assert state.cp_instances["CP0"].status == ControlPointStatus.FAILED
    assert state.cp_instances["CP0"].activity_output == result


def test_record_transition():
    state = _make_state()
    executor = ControlPointExecutor()
    executor.record_transition(state, "CP0", "CP1", "OK")
    assert len(state.transitions) == 1
    assert state.transitions[0].from_cp == "CP0"
    assert state.transitions[0].to_cp == "CP1"
    assert state.transitions[0].condition == "OK"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd controlplane && python -m pytest tests/test_control_point.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'controlplane.runtime.control_point'`

- [ ] **Step 3: Create control_point.py**

Create `controlplane/runtime/control_point.py`:

```python
from datetime import datetime, timezone

from controlplane.domain.activity import ActivityOutput
from controlplane.domain.execution import (
    ControlPointInstance,
    ControlPointStatus,
    StateTransition,
    WorkflowState,
)


class ControlPointExecutor:
    def enter_cp(self, state: WorkflowState, cp_id: str) -> None:
        state.current_cp = cp_id
        state.cp_instances[cp_id] = ControlPointInstance(
            cp_id=cp_id,
            status=ControlPointStatus.EXECUTING,
            started_at=datetime.now(timezone.utc),
        )

    def complete_cp(self, state: WorkflowState, cp_id: str, result: ActivityOutput) -> None:
        instance = state.cp_instances.get(cp_id)
        if instance:
            instance.status = ControlPointStatus.COMPLETED
            instance.completed_at = datetime.now(timezone.utc)
            instance.activity_output = result

    def fail_cp(self, state: WorkflowState, cp_id: str, result: ActivityOutput) -> None:
        instance = state.cp_instances.get(cp_id)
        if instance:
            instance.status = ControlPointStatus.FAILED
            instance.completed_at = datetime.now(timezone.utc)
            instance.activity_output = result

    def record_transition(
        self, state: WorkflowState, from_cp: str, to_cp: str | None, condition: str
    ) -> None:
        state.transitions.append(
            StateTransition(
                from_cp=from_cp,
                to_cp=to_cp,
                condition=condition,
                timestamp=datetime.now(timezone.utc),
            )
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd controlplane && python -m pytest tests/test_control_point.py -v`

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add controlplane/runtime/control_point.py controlplane/tests/test_control_point.py
git commit -m "feat(controlplane): add ControlPointExecutor for CP lifecycle management"
```

---

### Task 8: Human Gate Runtime

**Files:**
- Create: `controlplane/runtime/human_gate.py`
- Test: `controlplane/tests/test_human_gate.py`

- [ ] **Step 1: Write failing tests for human gate helpers**

Create `controlplane/tests/test_human_gate.py`:

```python
from datetime import timedelta

from controlplane.domain.human_gate import GateAction, HumanGateDefinition
from controlplane.runtime.human_gate import HumanGateRuntime


def test_reset_state():
    runtime = HumanGateRuntime()
    runtime.set_action(GateAction.CONTINUE, None)
    assert runtime.pending_action is not None
    runtime.reset()
    assert runtime.pending_action is None
    assert runtime.redirect_target is None


def test_set_action_continue():
    runtime = HumanGateRuntime()
    runtime.reset()
    runtime.set_action(GateAction.CONTINUE, None)
    assert runtime.pending_action == GateAction.CONTINUE
    assert runtime.redirect_target is None


def test_set_action_redirect():
    runtime = HumanGateRuntime()
    runtime.reset()
    runtime.set_action(GateAction.REDIRECT, "CP5")
    assert runtime.pending_action == GateAction.REDIRECT
    assert runtime.redirect_target == "CP5"


def test_set_action_terminate():
    runtime = HumanGateRuntime()
    runtime.reset()
    runtime.set_action(GateAction.TERMINATE, None)
    assert runtime.pending_action == GateAction.TERMINATE


def test_is_awaiting():
    runtime = HumanGateRuntime()
    runtime.reset()
    assert runtime.is_awaiting is True
    runtime.set_action(GateAction.CONTINUE, None)
    assert runtime.is_awaiting is False


def test_validate_valid_actions():
    runtime = HumanGateRuntime()
    for action in (GateAction.CONTINUE, GateAction.REDIRECT, GateAction.TERMINATE):
        runtime.validate_action(action)  # should not raise


def test_validate_invalid_action_raises():
    from controlplane.domain.activity import ActivityStatus
    runtime = HumanGateRuntime()
    import pytest
    with pytest.raises(ValueError, match="Invalid gate action"):
        runtime.validate_action("INVALID")


def test_resolve_redirect_target_from_update():
    runtime = HumanGateRuntime()
    runtime.set_action(GateAction.REDIRECT, "CP5")
    gate_def = HumanGateDefinition(redirect_target="CP3")
    target = runtime.resolve_redirect_target(gate_def)
    assert target == "CP5"  # update-provided target takes precedence


def test_resolve_redirect_target_from_definition():
    runtime = HumanGateRuntime()
    runtime.set_action(GateAction.REDIRECT, None)
    gate_def = HumanGateDefinition(redirect_target="CP3")
    target = runtime.resolve_redirect_target(gate_def)
    assert target == "CP3"  # falls back to definition target


def test_resolve_redirect_target_none():
    runtime = HumanGateRuntime()
    runtime.set_action(GateAction.REDIRECT, None)
    gate_def = HumanGateDefinition()
    target = runtime.resolve_redirect_target(gate_def)
    assert target is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd controlplane && python -m pytest tests/test_human_gate.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'controlplane.runtime.human_gate'`

- [ ] **Step 3: Create human_gate.py**

Create `controlplane/runtime/human_gate.py`:

```python
from controlplane.domain.human_gate import GateAction, HumanGateDefinition


class HumanGateRuntime:
    def __init__(self) -> None:
        self._pending_action: GateAction | None = None
        self._redirect_target: str | None = None

    @property
    def pending_action(self) -> GateAction | None:
        return self._pending_action

    @property
    def redirect_target(self) -> str | None:
        return self._redirect_target

    @property
    def is_awaiting(self) -> bool:
        return self._pending_action is None

    def reset(self) -> None:
        self._pending_action = None
        self._redirect_target = None

    def set_action(self, action: GateAction, redirect_target: str | None) -> None:
        self._pending_action = action
        self._redirect_target = redirect_target

    def validate_action(self, action) -> None:
        if action not in (GateAction.CONTINUE, GateAction.REDIRECT, GateAction.TERMINATE):
            raise ValueError(f"Invalid gate action: {action}")

    def resolve_redirect_target(self, gate_def: HumanGateDefinition) -> str | None:
        if self._redirect_target is not None:
            return self._redirect_target
        return gate_def.redirect_target
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd controlplane && python -m pytest tests/test_human_gate.py -v`

Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add controlplane/runtime/human_gate.py controlplane/tests/test_human_gate.py
git commit -m "feat(controlplane): add HumanGateRuntime with generic gate actions"
```

---

### Task 9: TemplateWorkflow — Core Interpreter

**Files:**
- Create: `controlplane/runtime/template_workflow.py`
- Create: `controlplane/config.py`
- Test: `controlplane/tests/test_template_workflow.py`

This is the most complex task. The TemplateWorkflow integrates all previous components.

- [ ] **Step 1: Create config.py**

Create `controlplane/config.py`:

```python
from dataclasses import dataclass


@dataclass
class ControlPlaneConfig:
    temporal_host: str = "localhost:7233"
    namespace: str = "default"
    task_queue: str = "control-plane"
```

- [ ] **Step 2: Write failing integration test for TemplateWorkflow**

Create `controlplane/tests/test_template_workflow.py`:

```python
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.client import Client

from controlplane.domain.activity import ActivityInput, ActivityOutput, ActivityStatus
from controlplane.domain.human_gate import GateAction, HumanGateDefinition
from controlplane.domain.policy import ExecutionPolicy
from controlplane.domain.template import (
    ActivityBinding,
    BranchDefinition,
    ControlPointDefinition,
    TransitionDefinition,
    WorkflowTemplate,
)
from controlplane.runtime.template_workflow import TemplateWorkflow


# --- Mock Activities ---

TEMPLATES: dict[str, WorkflowTemplate] = {}


@activity.defn(name="load_template")
async def load_template(template_ref: dict) -> dict:
    key = f"{template_ref['template_id']}@{template_ref['template_version']}"
    template = TEMPLATES.get(key)
    if template is None:
        raise ValueError(f"Template not found: {key}")
    from dataclasses import asdict
    return asdict(template)


@activity.defn(name="iqa_activity")
async def iqa_activity(input: ActivityInput) -> ActivityOutput:
    return ActivityOutput(status=ActivityStatus.OK, data={"mock": "iqa"})


@activity.defn(name="vda_activity_ok")
async def vda_activity_ok(input: ActivityInput) -> ActivityOutput:
    return ActivityOutput(status=ActivityStatus.OK, data={"mock": "vda_ok"})


@activity.defn(name="vda_activity_ng")
async def vda_activity_ng(input: ActivityInput) -> ActivityOutput:
    return ActivityOutput(status=ActivityStatus.NG, data={"mock": "vda_ng"})


@activity.defn(name="error_activity")
async def error_activity(input: ActivityInput) -> ActivityOutput:
    return ActivityOutput(status=ActivityStatus.ERROR, error="simulated failure")


# --- Helper to build a simple 2-CP template ---

def make_simple_template(template_id="simple", version="1.0") -> WorkflowTemplate:
    return WorkflowTemplate(
        id=template_id,
        version=version,
        control_points=[
            ControlPointDefinition(
                id="CP0",
                name="IQA Check",
                activity_binding=ActivityBinding(activity_name="iqa_activity"),
                execution_policy=ExecutionPolicy(),
            ),
            ControlPointDefinition(
                id="CP1",
                name="Final",
                activity_binding=None,
                execution_policy=ExecutionPolicy(),
            ),
        ],
        transitions=[
            TransitionDefinition(
                from_cp="CP0",
                branches=[BranchDefinition(condition="OK", to_cp="CP1")],
            ),
            TransitionDefinition(
                from_cp="CP1",
                branches=[BranchDefinition(condition="default", to_cp=None)],
            ),
        ],
        entry_point="CP0",
    )


def make_branching_template() -> WorkflowTemplate:
    return WorkflowTemplate(
        id="branching",
        version="1.0",
        control_points=[
            ControlPointDefinition(
                id="CP0",
                name="VDA Check",
                activity_binding=ActivityBinding(activity_name="vda_activity_ok"),
                execution_policy=ExecutionPolicy(),
            ),
            ControlPointDefinition(
                id="CP1_OK",
                name="OK Path",
                activity_binding=None,
                execution_policy=ExecutionPolicy(),
            ),
            ControlPointDefinition(
                id="CP1_NG",
                name="NG Path",
                activity_binding=None,
                execution_policy=ExecutionPolicy(),
            ),
        ],
        transitions=[
            TransitionDefinition(
                from_cp="CP0",
                branches=[
                    BranchDefinition(condition="OK", to_cp="CP1_OK"),
                    BranchDefinition(condition="NG", to_cp="CP1_NG"),
                    BranchDefinition(condition="default", to_cp="CP1_NG"),
                ],
            ),
        ],
        entry_point="CP0",
    )


async def run_workflow_test(template: WorkflowTemplate, activities: list) -> dict:
    """Helper to run a workflow against an ephemeral Temporal environment."""
    from dataclasses import asdict

    key = f"{template.id}@{template.version}"
    TEMPLATES[key] = template

    async with await WorkflowEnvironment.start_local() as env:
        async with Client.connect(
            env.host,
            namespace=env.namespace,
        ) as client:
            result = await client.execute_workflow(
                workflow=TemplateWorkflow,
                arg={"template_id": template.id, "template_version": template.version},
                id=f"test-{template.id}",
                task_queue="test-queue",
            )
            return result
```

This test file sets up all infrastructure. The actual test cases will be added in the next step once TemplateWorkflow exists.

- [ ] **Step 3: Create template_workflow.py — initial skeleton**

Create `controlplane/runtime/template_workflow.py`:

```python
import dataclasses
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from controlplane.domain.activity import ActivityInput, ActivityOutput, ActivityStatus
    from controlplane.domain.execution import (
        WorkflowContext,
        WorkflowState,
        WorkflowStatus,
    )
    from controlplane.domain.human_gate import GateAction, HumanGateDefinition
    from controlplane.domain.template import (
        ControlPointDefinition,
        WorkflowTemplate,
    )
    from controlplane.runtime.control_point import ControlPointExecutor
    from controlplane.runtime.human_gate import HumanGateRuntime
    from controlplane.runtime.transition import TransitionResolver


@workflow.defn(name="TemplateWorkflow")
class TemplateWorkflow:
    def __init__(self):
        self._template: WorkflowTemplate | None = None
        self._state: WorkflowState | None = None
        self._cp_executor = ControlPointExecutor()
        self._gate_runtime = HumanGateRuntime()
        self._transition_resolver: TransitionResolver | None = None

    @workflow.run
    async def run(self, template_ref: dict) -> dict:
        # Load template through activity
        self._template = await workflow.execute_activity(
            "load_template",
            arg=template_ref,
            result_type=dict,
            start_to_close_timeout=timedelta(seconds=10),
        )
        # Reconstruct WorkflowTemplate from dict
        self._template = self._dict_to_template(self._template)

        self._transition_resolver = TransitionResolver(self._template)
        self._state = WorkflowState(
            workflow_context=WorkflowContext(
                template_id=template_ref["template_id"],
                template_version=template_ref["template_version"],
                workflow_id=workflow.info().workflow_id,
            )
        )

        current_cp = self._template.entry_point
        self._upsert_cp_attributes(current_cp)

        while current_cp is not None:
            cp_def = self._resolve_cp(current_cp)
            self._cp_executor.enter_cp(self._state, current_cp)

            # 1. Human Gate check
            if cp_def.human_gate:
                action, redirect_target = await self._wait_human_gate(cp_def)
                if action == GateAction.TERMINATE:
                    self._state.status = WorkflowStatus.TERMINATED
                    return self._build_result()

                if action == GateAction.REDIRECT:
                    target = redirect_target or cp_def.human_gate.redirect_target
                    self._cp_executor.record_transition(self._state, current_cp, target, "REDIRECT")
                    current_cp = target
                    self._upsert_cp_attributes(current_cp)
                    continue

            # 2. Execute Activity (if bound)
            result = None
            if cp_def.activity_binding:
                result = await self._execute_activity(cp_def)
                if result and result.status == ActivityStatus.ERROR:
                    self._cp_executor.fail_cp(self._state, current_cp, result)
                    self._state.status = WorkflowStatus.FAILED
                    return self._build_result()
                self._cp_executor.complete_cp(self._state, current_cp, result)
            else:
                self._cp_executor.complete_cp(self._state, current_cp, ActivityOutput(status=ActivityStatus.OK))

            # 3. Resolve transition
            condition = self._transition_resolver.result_to_condition(result)
            next_cp = self._transition_resolver.resolve_next(current_cp, condition)
            self._cp_executor.record_transition(self._state, current_cp, next_cp, condition)

            # 4. Observability
            self._upsert_cp_attributes(next_cp or "COMPLETED")

            current_cp = next_cp

        self._state.status = WorkflowStatus.COMPLETED
        return self._build_result()

    @workflow.update(name="submit_gate_action")
    def submit_gate_action(self, action: GateAction, redirect_target: str | None = None) -> None:
        self._gate_runtime.set_action(action, redirect_target)

    @submit_gate_action.validator
    def validate_gate_action(self, action: GateAction, redirect_target: str | None = None) -> None:
        self._gate_runtime.validate_action(action)

    @workflow.query(name="current_state")
    def query_current_state(self) -> dict:
        if not self._state:
            return {}
        return {
            "current_cp": self._state.current_cp,
            "status": self._state.status.value,
        }

    @workflow.query(name="awaiting_gate")
    def query_awaiting_gate(self) -> str | None:
        if self._gate_runtime.is_awaiting and self._state and self._state.current_cp:
            cp = self._resolve_cp(self._state.current_cp)
            if cp.human_gate:
                return self._state.current_cp
        return None

    # --- Private helpers ---

    def _resolve_cp(self, cp_id: str) -> ControlPointDefinition:
        for cp in self._template.control_points:
            if cp.id == cp_id:
                return cp
        raise ValueError(f"Control point not found: {cp_id}")

    async def _execute_activity(self, cp_def: ControlPointDefinition) -> ActivityOutput:
        binding = cp_def.activity_binding
        policy = cp_def.execution_policy
        from temporalio.common import RetryPolicy

        retry = RetryPolicy(
            initial_interval=policy.retry_policy.initial_interval,
            backoff_coefficient=policy.retry_policy.backoff_coefficient,
            maximum_interval=policy.retry_policy.maximum_interval,
            maximum_attempts=policy.retry_policy.max_attempts,
            non_retryable_error_types=policy.retry_policy.non_retryable_error_types,
        )

        activity_input = ActivityInput(
            control_point_id=cp_def.id,
            workflow_context=dataclasses.asdict(self._state.workflow_context),
            params={"execution_target": binding.execution_target} if binding.execution_target else None,
        )

        output = await workflow.execute_activity(
            binding.activity_name,
            arg=activity_input,
            result_type=ActivityOutput,
            task_queue=binding.task_queue or workflow.info().task_queue,
            retry_policy=retry,
            schedule_to_close_timeout=policy.timeout_policy.schedule_to_close,
            start_to_close_timeout=policy.timeout_policy.start_to_close,
            heartbeat_timeout=policy.timeout_policy.heartbeat,
        )

        return output

    async def _wait_human_gate(self, cp_def: ControlPointDefinition) -> tuple[GateAction, str | None]:
        self._gate_runtime.reset()
        timeout = cp_def.human_gate.timeout
        if timeout:
            await workflow.wait_condition(lambda: not self._gate_runtime.is_awaiting, timeout=timeout)
        else:
            await workflow.wait_condition(lambda: not self._gate_runtime.is_awaiting)

        action = self._gate_runtime.pending_action
        redirect_target = self._gate_runtime.resolve_redirect_target(cp_def.human_gate)
        return action, redirect_target

    def _upsert_cp_attributes(self, current_cp: str | None) -> None:
        from temporalio.common import SearchAttributeKey

        cp_key = SearchAttributeKey.for_text("ControlPlaneCurrentCP")
        status_key = SearchAttributeKey.for_text("ControlPlaneStatus")
        template_key = SearchAttributeKey.for_keyword("ControlPlaneTemplateID")

        workflow.upsert_search_attributes([
            cp_key.value_set(current_cp or "COMPLETED"),
            status_key.value_set(self._state.status.value if self._state else "RUNNING"),
            template_key.value_set(self._state.workflow_context.template_id if self._state else ""),
        ])

    def _build_result(self) -> dict:
        return {
            "status": self._state.status.value,
            "template_id": self._state.workflow_context.template_id,
            "transitions": [
                {"from": t.from_cp, "to": t.to_cp, "condition": t.condition}
                for t in self._state.transitions
            ],
        }

    @staticmethod
    def _dict_to_template(data: dict) -> WorkflowTemplate:
        """Reconstruct WorkflowTemplate from a deserialized dict."""
        from controlplane.domain.template import (
            ActivityBinding,
            BranchDefinition,
            ControlPointDefinition,
            MigrationStrategy,
            TransitionDefinition,
            TransitionType,
        )
        from controlplane.domain.human_gate import HumanGateDefinition
        from controlplane.domain.policy import ExecutionPolicy, RetryPolicy, TimeoutPolicy

        control_points = []
        for cp_data in data.get("control_points", []):
            binding_data = cp_data.get("activity_binding")
            binding = ActivityBinding(**binding_data) if binding_data else None

            gate_data = cp_data.get("human_gate")
            gate = HumanGateDefinition(**gate_data) if gate_data else None

            retry_data = cp_data.get("execution_policy", {}).get("retry_policy", {})
            timeout_data = cp_data.get("execution_policy", {}).get("timeout_policy", {})
            policy = ExecutionPolicy(
                retry_policy=RetryPolicy(
                    max_attempts=retry_data.get("max_attempts", 3),
                    backoff_coefficient=retry_data.get("backoff_coefficient", 2.0),
                ),
                timeout_policy=TimeoutPolicy(),
            )

            control_points.append(ControlPointDefinition(
                id=cp_data["id"],
                name=cp_data["name"],
                activity_binding=binding,
                execution_policy=policy,
                human_gate=gate,
            ))

        transitions = []
        for td_data in data.get("transitions", []):
            branches = [
                BranchDefinition(condition=b["condition"], to_cp=b.get("to_cp"))
                for b in td_data.get("branches", [])
            ]
            transitions.append(TransitionDefinition(
                from_cp=td_data["from_cp"],
                transition_type=TransitionType(td_data.get("transition_type", "SEQUENTIAL")),
                branches=branches,
                parallel_targets=td_data.get("parallel_targets", []),
                join_condition=td_data.get("join_condition"),
            ))

        ms_data = data.get("migration_strategy")
        migration = MigrationStrategy(ms_data) if ms_data else None

        return WorkflowTemplate(
            id=data["id"],
            version=data["version"],
            control_points=control_points,
            transitions=transitions,
            entry_point=data["entry_point"],
            migration_strategy=migration,
        )
```

- [ ] **Step 4: Add integration test cases to test_template_workflow.py**

Append to `controlplane/tests/test_template_workflow.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_simple_sequential_workflow():
    """CP0(iqa OK) -> CP1 -> end"""
    template = make_simple_template()
    TEMPLATES[f"{template.id}@{template.version}"] = template

    async with await WorkflowEnvironment.start_local() as env:
        async with Client.connect(env.host, namespace=env.namespace) as client:
            worker = temporalio.worker.Worker(
                client,
                task_queue="test-queue",
                workflows=[TemplateWorkflow],
                activities=[load_template, iqa_activity],
            )
            async with worker:
                result = await client.execute_workflow(
                    TemplateWorkflow,
                    arg={"template_id": "simple", "template_version": "1.0"},
                    id="test-simple",
                    task_queue="test-queue",
                )
                assert result["status"] == "COMPLETED"
                assert len(result["transitions"]) >= 1


@pytest.mark.asyncio
async def test_branching_workflow_ok():
    """CP0(vda OK) -> CP1_OK"""
    template = make_branching_template()
    TEMPLATES[f"{template.id}@{template.version}"] = template

    async with await WorkflowEnvironment.start_local() as env:
        async with Client.connect(env.host, namespace=env.namespace) as client:
            worker = temporalio.worker.Worker(
                client,
                task_queue="test-queue",
                workflows=[TemplateWorkflow],
                activities=[load_template, vda_activity_ok],
            )
            async with worker:
                result = await client.execute_workflow(
                    TemplateWorkflow,
                    arg={"template_id": "branching", "template_version": "1.0"},
                    id="test-branching-ok",
                    task_queue="test-queue",
                )
                assert result["status"] == "COMPLETED"
                ok_branch = [t for t in result["transitions"] if t["to"] == "CP1_OK"]
                assert len(ok_branch) == 1


@pytest.mark.asyncio
async def test_branching_workflow_ng():
    """CP0(vda NG) -> CP1_NG"""
    ng_template = WorkflowTemplate(
        id="branching-ng",
        version="1.0",
        control_points=[
            ControlPointDefinition(
                id="CP0",
                name="VDA Check",
                activity_binding=ActivityBinding(activity_name="vda_activity_ng"),
                execution_policy=ExecutionPolicy(),
            ),
            ControlPointDefinition(
                id="CP1_NG",
                name="NG Path",
                activity_binding=None,
                execution_policy=ExecutionPolicy(),
            ),
        ],
        transitions=[
            TransitionDefinition(
                from_cp="CP0",
                branches=[
                    BranchDefinition(condition="OK", to_cp="CP1_OK"),
                    BranchDefinition(condition="NG", to_cp="CP1_NG"),
                ],
            ),
        ],
        entry_point="CP0",
    )
    TEMPLATES[f"{ng_template.id}@{ng_template.version}"] = ng_template

    async with await WorkflowEnvironment.start_local() as env:
        async with Client.connect(env.host, namespace=env.namespace) as client:
            worker = temporalio.worker.Worker(
                client,
                task_queue="test-queue",
                workflows=[TemplateWorkflow],
                activities=[load_template, vda_activity_ng],
            )
            async with worker:
                result = await client.execute_workflow(
                    TemplateWorkflow,
                    arg={"template_id": "branching-ng", "template_version": "1.0"},
                    id="test-branching-ng",
                    task_queue="test-queue",
                )
                assert result["status"] == "COMPLETED"
                ng_branch = [t for t in result["transitions"] if t["to"] == "CP1_NG"]
                assert len(ng_branch) == 1


@pytest.mark.asyncio
async def test_error_activity_fails_workflow():
    """CP0(error) -> workflow FAILED"""
    err_template = WorkflowTemplate(
        id="error-test",
        version="1.0",
        control_points=[
            ControlPointDefinition(
                id="CP0",
                name="Will Fail",
                activity_binding=ActivityBinding(activity_name="error_activity"),
                execution_policy=ExecutionPolicy(
                    retry_policy=RetryPolicy(max_attempts=1),
                ),
            ),
        ],
        transitions=[],
        entry_point="CP0",
    )
    TEMPLATES[f"{err_template.id}@{err_template.version}"] = err_template

    async with await WorkflowEnvironment.start_local() as env:
        async with Client.connect(env.host, namespace=env.namespace) as client:
            worker = temporalio.worker.Worker(
                client,
                task_queue="test-queue",
                workflows=[TemplateWorkflow],
                activities=[load_template, error_activity],
            )
            async with worker:
                result = await client.execute_workflow(
                    TemplateWorkflow,
                    arg={"template_id": "error-test", "template_version": "1.0"},
                    id="test-error",
                    task_queue="test-queue",
                )
                assert result["status"] == "FAILED"
```

Note: the test file needs `import temporalio.worker` and `from controlplane.domain.policy import RetryPolicy` added at the top.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd controlplane && python -m pytest tests/test_template_workflow.py -v --timeout=120`

Expected: 4 passed (integration tests with Temporal test environment)

- [ ] **Step 6: Commit**

```bash
git add controlplane/runtime/template_workflow.py controlplane/config.py controlplane/tests/test_template_workflow.py
git commit -m "feat(controlplane): add TemplateWorkflow core interpreter with integration tests"
```

---

### Task 10: Worker Bootstrap and Mock Activity Adapters

**Files:**
- Create: `controlplane/worker.py`
- Create: `controlplane/adapter/mocks.py`

- [ ] **Step 1: Create mock activity adapters**

Create `controlplane/adapter/mocks.py`:

```python
from temporalio import activity

from controlplane.domain.activity import ActivityInput, ActivityOutput, ActivityStatus


@activity.defn(name="iqa_activity")
async def iqa_activity(input: ActivityInput) -> ActivityOutput:
    return ActivityOutput(status=ActivityStatus.OK, data={"mock": "iqa_result"})


@activity.defn(name="ppa_activity")
async def ppa_activity(input: ActivityInput) -> ActivityOutput:
    return ActivityOutput(status=ActivityStatus.OK, data={"mock": "ppa_result"})


@activity.defn(name="mea_activity")
async def mea_activity(input: ActivityInput) -> ActivityOutput:
    return ActivityOutput(status=ActivityStatus.OK, data={"mock": "mea_result"})


@activity.defn(name="rda_activity")
async def rda_activity(input: ActivityInput) -> ActivityOutput:
    return ActivityOutput(status=ActivityStatus.OK, data={"mock": "rda_result"})


@activity.defn(name="vda_activity")
async def vda_activity(input: ActivityInput) -> ActivityOutput:
    return ActivityOutput(status=ActivityStatus.OK, data={"mock": "vda_result"})


@activity.defn(name="rva_activity")
async def rva_activity(input: ActivityInput) -> ActivityOutput:
    return ActivityOutput(status=ActivityStatus.OK, data={"mock": "rva_result"})


@activity.defn(name="mta_activity")
async def mta_activity(input: ActivityInput) -> ActivityOutput:
    return ActivityOutput(status=ActivityStatus.OK, data={"mock": "mta_result"})


@activity.defn(name="hca_activity")
async def hca_activity(input: ActivityInput) -> ActivityOutput:
    return ActivityOutput(status=ActivityStatus.OK, data={"mock": "hca_result"})


ALL_MOCK_ACTIVITIES = [
    iqa_activity, ppa_activity, mea_activity, rda_activity,
    vda_activity, rva_activity, mta_activity, hca_activity,
]
```

- [ ] **Step 2: Create load_template activity**

Create `controlplane/adapter/template_loader.py`:

```python
from dataclasses import asdict

from temporalio import activity

from controlplane.domain.template import WorkflowTemplate


class TemplateLoaderActivity:
    def __init__(self, repository=None):
        self._repository = repository

    @property
    def name(self) -> str:
        return "load_template"

    @property
    def version(self) -> str:
        return "v1"


def create_load_template_activity(repository=None):
    """Creates a load_template activity function."""

    @activity.defn(name="load_template")
    async def load_template(template_ref: dict) -> dict:
        if repository:
            template = await repository.load(
                template_ref["template_id"],
                template_ref["template_version"],
            )
        else:
            template = None

        if template is None:
            raise ValueError(
                f"Template not found: {template_ref['template_id']}@{template_ref['template_version']}"
            )
        return asdict(template)

    return load_template
```

- [ ] **Step 3: Create worker.py**

Create `controlplane/worker.py`:

```python
import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from controlplane.adapter.mocks import ALL_MOCK_ACTIVITIES
from controlplane.adapter.template_loader import create_load_template_activity
from controlplane.config import ControlPlaneConfig
from controlplane.runtime.template_workflow import TemplateWorkflow


async def run_worker(config: ControlPlaneConfig | None = None) -> None:
    if config is None:
        config = ControlPlaneConfig()

    client = await Client.connect(
        config.temporal_host,
        namespace=config.namespace,
    )

    activities = list(ALL_MOCK_ACTIVITIES) + [create_load_template_activity()]

    worker = Worker(
        client,
        task_queue=config.task_queue,
        workflows=[TemplateWorkflow],
        activities=activities,
    )

    print(f"Starting Control Plane worker on task queue: {config.task_queue}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(run_worker())
```

- [ ] **Step 4: Verify worker module imports**

Run: `cd controlplane && python -c "from controlplane.worker import run_worker; print('Worker import OK')"`

Expected: `Worker import OK`

- [ ] **Step 5: Commit**

```bash
git add controlplane/adapter/mocks.py controlplane/adapter/template_loader.py controlplane/worker.py
git commit -m "feat(controlplane): add worker bootstrap with mock activities and template loader"
```

---

### Task 11: Run Full Test Suite

**Files:** None new — verification only

- [ ] **Step 1: Run all tests**

Run: `cd controlplane && python -m pytest tests/ -v --timeout=120`

Expected: All tests pass (domain unit tests + registry + transition + control_point + human_gate + template_workflow integration)

- [ ] **Step 2: Run with coverage check (optional)**

Run: `cd controlplane && pip install pytest-cov && python -m pytest tests/ --cov=controlplane --cov-report=term-missing`

Expected: Coverage report shows >80% for domain, runtime, adapter, infrastructure

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "chore(controlplane): verify full test suite passes"
```

---

## Self-Review Checklist

**1. Spec coverage:**

| Spec Section | Task |
|---|---|
| Domain: template.py (WorkflowTemplate, ActivityBinding, TransitionType, etc.) | Task 3 |
| Domain: execution.py (WorkflowState, ControlPointInstance, etc.) | Task 3 |
| Domain: activity.py (ActivityInput, ActivityOutput, ActivityStatus) | Task 2 |
| Domain: policy.py (RetryPolicy, TimeoutPolicy, ExecutionPolicy) | Task 2 |
| Domain: human_gate.py (GateAction, HumanGateDefinition) | Task 2 |
| Adapter: ActivityAdapter base class | Task 4 |
| Infrastructure: ActivityRegistry | Task 4 |
| Port: EventPort, DataPort, CommandPort | Task 5 |
| Repo: StateRepository, AuditRepository, TemplateRepository | Task 5 |
| Runtime: TransitionResolver | Task 6 |
| Runtime: ControlPointExecutor | Task 7 |
| Runtime: HumanGateRuntime | Task 8 |
| Runtime: TemplateWorkflow (core interpreter) | Task 9 |
| Worker bootstrap | Task 10 |
| Mock activities | Task 10 |
| Template loader activity | Task 10 |
| Config | Task 9 |
| Integration tests | Task 9 |

**2. Placeholder scan:** No TBD/TODO/fill-in-later found. All code steps contain complete implementations.

**3. Type consistency:**
- `ActivityOutput` used consistently (not `ActivityResult`) across all files
- `GateAction` (not `GateDecision`) used in human_gate.py, template_workflow.py, test files
- `ActivityBinding` (not `str`) used in template.py, control_point.py references
- `TransitionType` enum values match between template.py and transition.py
- `StateRepository` (not `WeldMapRepository`) in repo/state.py
- `WorkflowStatus.TERMINATED` (not `REJECTED`) in execution.py and template_workflow.py

Plan complete and saved to `docs/superpowers/plans/2026-06-03-l2-control-plane.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?