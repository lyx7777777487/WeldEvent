# Industrial Agent OS — L2 Control Plane Runtime Skeleton Design

**Date:** 2026-06-03
**Status:** Revised (Post-Review)
**Scope:** L2 Control Plane Runtime only (L1/L3/L4 out of scope)

---

## Architectural Constraints

These constraints are invariant. They apply to all layers of the Control Plane.

1. Temporal is the only orchestration engine. Never build a custom workflow runtime, scheduler, retry engine, or state machine executor.
2. Control Points are workflow states. Control Points are NOT Activities. Control Points are NOT Agents.
3. Activities are pluggable execution handlers. Activities are attached to states through bindings. Activities are not workflow nodes.
4. Workflow Templates are versioned runtime artifacts, not runtime payloads.
5. Human Gates are generic workflow primitives, not business-specific actions.
6. L2 Control Plane has no dependency on L4 Data Plane implementations.
7. Workflow topology is independent from Activity implementation.
8. The domain model must support future parallel execution without redesign.
9. Activity implementations must be hot-swappable through bindings.
10. The Control Plane must remain stable while Cognitive Plane, Execution Plane, and Data Plane evolve independently.

---

## 1. Architecture Overview

### 1.1 Layer Context

```text
L1 Cognitive Plane (out of scope) → produces WorkflowTemplate references
L2 Control Plane (this design)    → orchestrates execution
L3 Execution Plane (out of scope) → black-box agents
L4 Data Plane (out of scope)      → accessed only through generic abstractions
```

### 1.2 Technology Stack

- **Language:** Python 3.12+
- **SDK:** temporalio 1.27+ (Python SDK)
- **Runtime:** Temporal Server (existing `go.temporal.io/server`)
- **Integration mode:** Python SDK Worker process, connects to Temporal Server via gRPC

### 1.3 Core Design Decision: Data-Driven Interpreter Workflow

A single `TemplateWorkflow` interprets `WorkflowTemplate` data at runtime. Control Points are workflow loop states. Activities are dispatched through structured bindings via the Activity Registry. No workflow code is generated per template.

**Why:** Principle 4 — "Workflow topology must never be hardcoded." A data-driven interpreter ensures new workflows require only a new template, not new code.

---

## 2. Module Structure

```text
controlplane/
├── __init__.py
├── domain/                    # Pure data models, zero external dependencies
│   ├── __init__.py
│   ├── template.py            # WorkflowTemplate, ControlPointDefinition, TransitionDefinition, BranchDefinition, ActivityBinding, TransitionType
│   ├── execution.py           # WorkflowInstance, ControlPointInstance, WorkflowState, StateTransition
│   ├── activity.py            # ActivityInput, ActivityOutput, ActivityStatus
│   ├── policy.py              # ExecutionPolicy, RetryPolicy, TimeoutPolicy
│   └── human_gate.py          # HumanGateDefinition, GateAction
│
├── runtime/                   # Temporal workflow runtime (core engine)
│   ├── __init__.py
│   ├── template_workflow.py   # TemplateWorkflow — template interpreter
│   ├── control_point.py       # Control Point execution logic
│   ├── transition.py          # State transition resolver
│   └── human_gate.py          # Human Gate runtime (Update/Query/Await)
│
├── adapter/                   # Activity abstraction layer
│   ├── __init__.py
│   └── base.py                # ActivityAdapter abstract base class
│
├── infrastructure/           # Reusable platform capabilities
│   ├── __init__.py
│   └── registry.py            # ActivityRegistry (register/discover/bind/version)
│
├── port/                      # External integration ports (pure interfaces)
│   ├── __init__.py
│   ├── event.py               # EventPort
│   ├── data.py                # DataPort
│   └── command.py             # CommandPort
│
├── repo/                      # Repository abstractions (pure interfaces)
│   ├── __init__.py
│   ├── state.py               # StateRepository (generic, no L4 domain knowledge)
│   ├── audit.py                # AuditRepository
│   └── template.py            # TemplateRepository
│
├── worker.py                  # Worker bootstrap entrypoint
├── config.py                  # Configuration
│
├── tests/                     # Tests
│   ├── __init__.py
│   ├── test_template_workflow.py
│   ├── test_activity_registry.py
│   ├── test_control_point.py
│   └── test_human_gate.py
│
├── pyproject.toml
├── requirements.txt
└── README.md
```

### 2.1 Dependency Rules

```text
runtime → domain + adapter + infrastructure
adapter → domain
infrastructure → domain
port    → domain
repo    → domain
```

No upward dependencies. No cross-dependencies between adapter/infrastructure/port/repo. The Control Plane (`repo/`) has no knowledge of L4 domain concepts (WeldMap, Neo4j, PostgreSQL, Ontology Store, etc.).

---

## 3. Domain Model

### 3.1 template.py

```python
from dataclasses import dataclass, field
from enum import Enum
from .policy import ExecutionPolicy
from .human_gate import HumanGateDefinition


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
    """Structured reference to an Activity implementation.

    Decouples Control Point definitions from specific Activity deployments.
    Supports versioning, task queue routing, and execution target selection
    (e.g. GPU, Edge, Cloud) without changing workflow topology.
    """
    activity_name: str                  # e.g. "mea_activity"
    activity_version: str = "latest"    # e.g. "v1", "v2"
    task_queue: str | None = None       # Override task queue (None = default)
    execution_target: str | None = None # e.g. "gpu", "edge", "cloud"


@dataclass(frozen=True)
class WorkflowTemplate:
    id: str
    version: str                              # WorkflowTemplateVersion
    control_points: list["ControlPointDefinition"]
    transitions: list["TransitionDefinition"]
    entry_point: str                          # CP ID to start from
    migration_strategy: MigrationStrategy | None = None


@dataclass(frozen=True)
class ControlPointDefinition:
    """A Control Point is a workflow state.

    It may optionally have an ActivityBinding attached for execution,
    and/or a HumanGateDefinition for manual intervention.

    Control Points are never Activities.
    Activities are never States.
    """
    id: str                                   # e.g. "CP0"
    name: str
    activity_binding: ActivityBinding | None  # Structured binding (None = decision-only point)
    execution_policy: ExecutionPolicy
    human_gate: HumanGateDefinition | None = None


@dataclass(frozen=True)
class TransitionDefinition:
    from_cp: str                              # Source CP ID
    transition_type: TransitionType = TransitionType.SEQUENTIAL
    branches: list["BranchDefinition"] = field(default_factory=list)
    parallel_targets: list[str] = field(default_factory=list)  # CP IDs for PARALLEL transitions
    join_condition: str | None = None                          # For JOIN transitions


@dataclass(frozen=True)
class BranchDefinition:
    condition: str                            # "OK" / "MARGINAL" / "NG" / "default"
    to_cp: str | None                         # None = workflow ends
```

**Principle alignment:**
- Constraint 2: Control Points are States → `ControlPointDefinition` defines state, not behavior. Activity is attached through `ActivityBinding`, not merged.
- Constraint 3: Activities are Pluggable Handlers → `ActivityBinding` is a structured reference, resolved by registry. Hot-swappable via version/target without touching workflow topology.
- Constraint 4: Templates are Runtime Artifacts → topology defined in data, not code
- Constraint 5: Human Gates are Generic → `HumanGateDefinition` uses `GateAction` (CONTINUE/REDIRECT/TERMINATE), not business-specific actions
- Constraint 7: Topology independent from Activity → changing `ActivityBinding` does not change CP definitions
- Constraint 8: Parallel execution → `TransitionType.PARALLEL` + `parallel_targets` in domain model. Implementation deferred.

### 3.2 execution.py

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


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
    activity_output: "ActivityOutput | None" = None


@dataclass
class ControlPointInstance:
    cp_id: str
    status: ControlPointStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    activity_output: "ActivityOutput | None" = None


@dataclass
class WorkflowState:
    workflow_context: WorkflowContext
    current_cp: str | None = None
    status: WorkflowStatus = WorkflowStatus.RUNNING
    cp_instances: dict[str, ControlPointInstance] = field(default_factory=dict)
    transitions: list[StateTransition] = field(default_factory=list)
```

### 3.3 activity.py

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
    workflow_context: dict            # Serialized WorkflowContext
    params: dict | None = None        # Activity-specific parameters


@dataclass
class ActivityOutput:
    status: ActivityStatus
    data: dict | None = None
    error: str | None = None
```

**L2 → L3 interface contract:** All downstream agents (IQA, PPA, MEA, etc.) implement `ActivityAdapter.execute(input) → ActivityOutput`. The Control Plane never sees agent internals.

### 3.4 policy.py

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

### 3.5 human_gate.py

```python
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum


class GateAction(Enum):
    """Generic gate actions. Reusable across all workflow domains.

    Business workflows map their domain actions to these primitives:
        APPROVE   → CONTINUE
        REJECT    → TERMINATE
        ESCALATE  → REDIRECT
    """
    CONTINUE = "CONTINUE"       # Proceed to next step
    REDIRECT = "REDIRECT"       # Jump to another control point
    TERMINATE = "TERMINATE"     # End the workflow


@dataclass(frozen=True)
class HumanGateDefinition:
    """Generic Human Gate definition. No business-specific semantics."""
    timeout: timedelta | None = None
    redirect_target: str | None = None    # CP ID for REDIRECT action
```

---

## 4. Runtime Design

### 4.1 TemplateWorkflow — Core Interpreter

```python
@workflow.defn(name="TemplateWorkflow")
class TemplateWorkflow:
    """Single interpreter workflow that executes any WorkflowTemplate.

    Workflow Templates are loaded by reference (template_id + version),
    not passed as runtime payloads. This keeps Temporal workflow history
    small and treats templates as versioned runtime artifacts.
    """

    def __init__(self):
        self._template: WorkflowTemplate | None = None
        self._state: WorkflowState | None = None
        self._gate_action: GateAction | None = None
        self._gate_redirect_target: str | None = None

    @workflow.run
    async def run(self, template_ref: dict) -> dict:
        """Accept a template reference, load through TemplateRepository activity.

        Args:
            template_ref: {"template_id": "...", "template_version": "..."}
        """
        # Load template through abstraction layer (not direct payload)
        self._template = await workflow.execute_activity(
            "load_template",
            arg=template_ref,
            result_type=WorkflowTemplate,
            start_to_close_timeout=timedelta(seconds=10),
        )

        self._state = self._init_state(template_ref)
        current_cp = self._template.entry_point

        # Upsert initial search attributes for observability
        self._upsert_cp_attributes(current_cp)

        while current_cp is not None:
            cp_def = self._resolve_cp(current_cp)
            self._enter_cp(current_cp)

            # 1. Human Gate check (if defined)
            if cp_def.human_gate:
                action, redirect_target = await self._wait_human_gate(cp_def)

                if action == GateAction.TERMINATE:
                    return self._complete(WorkflowStatus.TERMINATED)

                if action == GateAction.REDIRECT:
                    target = redirect_target or cp_def.human_gate.redirect_target
                    self._transition(current_cp, target, "REDIRECT")
                    current_cp = target
                    self._upsert_cp_attributes(current_cp)
                    continue

                # CONTINUE: fall through to activity execution

            # 2. Execute Activity (if bound)
            result = None
            if cp_def.activity_binding:
                result = await self._execute_activity(cp_def)

                # Handle activity error with retry exhaustion
                if result and result.status == ActivityStatus.ERROR:
                    self._fail_cp(current_cp, result)
                    return self._complete(WorkflowStatus.FAILED)

            # 3. Resolve transition
            condition = self._result_to_condition(result)
            next_cp = self._resolve_next_cp(current_cp, condition)
            self._transition(current_cp, next_cp, condition)

            # 4. Upsert search attributes for observability
            self._upsert_cp_attributes(next_cp or "COMPLETED")

            current_cp = next_cp

        return self._complete(WorkflowStatus.COMPLETED)
```

**Template loading strategy (Review #1):**
- Workflow receives `template_ref` (ID + version), not the full template payload
- Template is loaded through `load_template` activity which delegates to `TemplateRepository`
- This keeps workflow history small and treats templates as versioned runtime artifacts

**Temporal features used:**
- `workflow.execute_activity(activity_name_str, ...)` — dynamic dispatch by string name
- `workflow.wait_condition(fn, timeout)` — non-polling Human Gate
- `@workflow.update` — safe Human Gate action with validator
- `@workflow.query` — current state inspection
- `workflow.upsert_search_attributes` — observability at each CP
- `workflow.patched` / `workflow.deprecate_patch` — versioning
- `workflow.continue_as_new` — long-running workflow history compaction

### 4.2 Activity Dispatch

```python
async def _execute_activity(self, cp_def: ControlPointDefinition) -> ActivityOutput:
    """Dispatch activity through structured ActivityBinding.

    The binding contains activity_name, version, task_queue, and execution_target.
    This decouples workflow topology from Activity deployment details.
    """
    binding = cp_def.activity_binding
    policy = cp_def.execution_policy
    retry = temporalio.common.RetryPolicy(
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

    # Dynamic activity dispatch using binding's activity_name
    # task_queue from binding takes precedence if specified
    output = await workflow.execute_activity(
        binding.activity_name,                    # string name from ActivityBinding
        arg=activity_input,
        result_type=ActivityOutput,              # explicit type for deserialization
        task_queue=binding.task_queue or self._default_task_queue(),
        retry_policy=retry,
        schedule_to_close_timeout=policy.timeout_policy.schedule_to_close,
        start_to_close_timeout=policy.timeout_policy.start_to_close,
        heartbeat_timeout=policy.timeout_policy.heartbeat,
    )

    return ActivityOutput(status=output.status, data=output.data, error=output.error)
```

**ActivityBinding usage (Review #2):**
- `binding.activity_name` — determines which Activity Adapter to invoke
- `binding.task_queue` — routes to specific deployment (e.g. GPU worker, Edge worker)
- `binding.execution_target` — passed as param to Activity, allowing same Activity to adapt behavior
- `binding.activity_version` — resolved by ActivityRegistry at registration time, not at dispatch time
- Changing binding (e.g. MEA v1 → MEA v2 GPU) does not change the Control Point or workflow topology

### 4.3 Human Gate Runtime

Uses generic `GateAction` primitives instead of business-specific actions (Review #4):

```python
@workflow.update(name="submit_gate_action")
def submit_gate_action(self, action: GateAction, redirect_target: str | None = None) -> None:
    """Update handler for Human Gate actions. Validator runs first."""
    self._gate_action = action
    self._gate_redirect_target = redirect_target

@submit_gate_action.validator
def validate_gate_action(self, action: GateAction, redirect_target: str | None = None) -> None:
    """Validate gate action before applying. Future: check reviewer permissions."""
    if action not in (GateAction.CONTINUE, GateAction.REDIRECT, GateAction.TERMINATE):
        raise ValueError(f"Invalid gate action: {action}")
    if action == GateAction.REDIRECT and not redirect_target:
        # redirect_target can come from HumanGateDefinition if not provided
        pass

@workflow.query(name="current_state")
def query_current_state(self) -> dict:
    """Query handler for external state inspection."""
    return {
        "current_cp": self._state.current_cp,
        "status": self._state.status.value,
        "cp_instances": {k: {"status": v.status.value} for k, v in self._state.cp_instances.items()},
    }

@workflow.query(name="awaiting_gate")
def query_awaiting_gate(self) -> str | None:
    """Which CP is currently awaiting a human gate decision."""
    if self._gate_action is None and self._state.current_cp:
        cp = self._resolve_cp(self._state.current_cp)
        if cp.human_gate:
            return self._state.current_cp
    return None

async def _wait_human_gate(self, cp_def: ControlPointDefinition) -> tuple[GateAction, str | None]:
    """Wait for a gate action. Returns (action, redirect_target)."""
    self._gate_action = None
    self._gate_redirect_target = None
    timeout = cp_def.human_gate.timeout
    if timeout:
        await workflow.wait_condition(lambda: self._gate_action is not None, timeout=timeout)
    else:
        await workflow.wait_condition(lambda: self._gate_action is not None)
    return self._gate_action, self._gate_redirect_target
```

**Generic gate semantics (Review #4):**
- `CONTINUE` — proceed to next step (business maps: APPROVE)
- `REDIRECT` — jump to another CP (business maps: ESCALATE)
- `TERMINATE` — end the workflow (business maps: REJECT)
- Business-specific mapping happens at the caller side, not inside the Control Plane

### 4.4 Transition Resolution

```python
def _resolve_next_cp(self, from_cp: str, condition: str) -> str | None:
    for transition in self._template.transitions:
        if transition.from_cp != from_cp:
            continue

        # Handle transition types
        if transition.transition_type == TransitionType.PARALLEL:
            # Parallel execution: not yet implemented in runtime
            # Domain model supports it; runtime will be extended later
            raise NotImplementedError(
                "Parallel transition execution not yet implemented. "
                "Domain model is ready for future extension."
            )

        # Sequential: check branches
        for branch in transition.branches:
            if branch.condition == condition:
                return branch.to_cp
        # Check default branch
        for branch in transition.branches:
            if branch.condition == "default":
                return branch.to_cp

    return None  # No transition = workflow ends

def _result_to_condition(self, result: ActivityOutput | None) -> str:
    if result is None:
        return "default"
    return result.status.value  # "OK" / "MARGINAL" / "NG"
```

**Parallel execution readiness (Review #6):**
- `TransitionType.PARALLEL` + `parallel_targets` in the domain model
- Runtime raises `NotImplementedError` for PARALLEL transitions
- Future implementation will use `asyncio.gather` with multiple `workflow.execute_activity` calls
- No domain model redesign needed when parallel execution is added

### 4.5 Observability

Every CP transition upserts search attributes:

```python
def _upsert_cp_attributes(self, current_cp: str | None) -> None:
    from temporalio.common import SearchAttributeKey

    cp_key = SearchAttributeKey.for_text("ControlPlaneCurrentCP")
    status_key = SearchAttributeKey.for_text("ControlPlaneStatus")
    template_key = SearchAttributeKey.for_keyword("ControlPlaneTemplateID")
    version_key = SearchAttributeKey.for_keyword("ControlPlaneTemplateVersion")

    workflow.upsert_search_attributes([
        cp_key.value_set(current_cp or "COMPLETED"),
        status_key.value_set(self._state.status.value if self._state else "RUNNING"),
        template_key.value_set(self._state.workflow_context.template_id if self._state else ""),
        version_key.value_set(self._state.workflow_context.template_version if self._state else ""),
    ])
```

### 4.6 Versioning Strategy

Workflow Templates are versioned runtime artifacts (Review #1, #4):

- **Running instances:** Continue with the version they started with (determinism guaranteed by Temporal event sourcing). Template is loaded once at workflow start and cached in workflow state.
- **New instances:** Use the latest version available in `TemplateRepository`.
- **In-place migration:** Use `workflow.patched()` for incremental changes.

```python
# Inside TemplateWorkflow.run, for backward-compatible changes:
if workflow.patched("template-v2-add-CP5"):
    # New logic for CP5
    ...
```

### 4.7 Long-Running Workflow Support

For workflows that may exceed Temporal's history size limits:

```python
# In the main loop, after N control points:
if len(self._state.transitions) > 50:
    # Pass template reference (not full template) to continue_as_new
    workflow.continue_as_new(
        arg={"template_id": self._state.workflow_context.template_id,
             "template_version": self._state.workflow_context.template_version},
        # State is preserved via search attributes and memo
    )
```

---

## 5. Activity Abstraction Layer

### 5.1 ActivityAdapter (adapter/base.py)

```python
from abc import ABC, abstractmethod


class ActivityAdapter(ABC):
    """Unified execution contract for all downstream agents.

    Activities are execution handlers attached to Control Points through
    ActivityBindings. They are not workflow nodes. They are not states.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique activity name (e.g. 'iqa_activity', 'mea_activity')."""
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        """Activity version for registry versioning."""
        ...

    @abstractmethod
    async def execute(self, input: ActivityInput) -> ActivityOutput:
        """Execute the activity. All agent logic is behind this interface."""
        ...
```

### 5.2 Mock Activity Implementations

For each downstream agent:

```python
class IQAActivity(ActivityAdapter):
    @property
    def name(self) -> str: return "iqa_activity"
    @property
    def version(self) -> str: return "v1"
    async def execute(self, input: ActivityInput) -> ActivityOutput:
        return ActivityOutput(status=ActivityStatus.OK, data={"mock": "iqa_result"})

class PPAActivity(ActivityAdapter):
    @property
    def name(self) -> str: return "ppa_activity"
    @property
    def version(self) -> str: return "v1"
    async def execute(self, input: ActivityInput) -> ActivityOutput:
        return ActivityOutput(status=ActivityStatus.OK, data={"mock": "ppa_result"})

# MEA, RDA, VDA, RVA, MTA, HCA — same pattern
```

### 5.3 Activity Wrapper for Temporal Registration

```python
def create_temporal_activity(adapter: ActivityAdapter):
    """Wraps an ActivityAdapter as a Temporal-registered activity function."""

    @activity.defn(name=adapter.name)
    async def activity_fn(input: ActivityInput) -> ActivityOutput:
        return await adapter.execute(input)

    return activity_fn
```

---

## 6. Infrastructure — Activity Registry

Moved from `adapter/` to `infrastructure/registry/` (Review #7). Registry is a reusable platform capability, not an adapter concern.

### 6.1 ActivityRegistry (infrastructure/registry.py)

```python
class ActivityRegistry:
    """Registers, discovers, and binds ActivityAdapters.

    Responsibilities:
    - Registration: register adapters by name + version
    - Discovery: look up adapters by name and version
    - Binding: resolve ActivityBinding from ControlPointDefinition
    - Version resolution: support "latest" and explicit version selection
    """

    def __init__(self) -> None:
        self._adapters: dict[str, dict[str, ActivityAdapter]] = {}
        # {name: {version: adapter}}

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
        """Resolve an ActivityBinding to a concrete ActivityAdapter.

        The binding's version and execution_target are used for lookup
        and routing, but the adapter selection is by name + version.
        Task queue and execution_target are handled at dispatch time
        in the TemplateWorkflow.
        """
        return self.get(binding.activity_name, binding.activity_version)

    def all(self) -> list[ActivityAdapter]:
        """Return all registered adapters (for worker registration)."""
        result = []
        for versions in self._adapters.values():
            result.extend(versions.values())
        return result
```

---

## 7. External Integration Ports

### 7.1 Event Port

```python
class EventPort(ABC):
    """Integration point for event streaming (NATS, Kafka, etc.)."""
    @abstractmethod
    async def publish(self, event: "ControlPlaneEvent") -> None: ...
    @abstractmethod
    async def subscribe(self, event_type: str) -> "AsyncIterator[ControlPlaneEvent]": ...
```

### 7.2 Data Port

```python
class DataPort(ABC):
    """Integration point for data access (MES, SCADA, etc.)."""
    @abstractmethod
    async def read(self, key: str) -> dict | None: ...
    @abstractmethod
    async def write(self, key: str, value: dict) -> None: ...
```

### 7.3 Command Port

```python
class CommandPort(ABC):
    """Integration point for command dispatch (PLC, ERP, etc.)."""
    @abstractmethod
    async def send(self, command: "ControlPlaneCommand") -> "CommandResult": ...
```

---

## 8. Repository Abstractions

All repository abstractions use generic types. No L4 domain concepts (WeldMap, Neo4j, etc.) appear in L2 (Review #3).

### 8.1 StateRepository (generic)

```python
class StateRepository(ABC):
    """Generic state storage. L4 implementations (WeldMap, Neo4j, PostgreSQL, etc.)
    implement this interface. The Control Plane does not know about WeldMap."""
    @abstractmethod
    async def get(self, key: str) -> dict | None: ...
    @abstractmethod
    async def put(self, key: str, value: dict) -> None: ...
    @abstractmethod
    async def query(self, criteria: dict) -> list[dict]: ...
    @abstractmethod
    async def delete(self, key: str) -> None: ...
```

### 8.2 AuditRepository

```python
class AuditRepository(ABC):
    """Audit trail storage."""
    @abstractmethod
    async def record(self, entry: "AuditEntry") -> None: ...
    @abstractmethod
    async def query(self, criteria: "AuditQuery") -> "list[AuditEntry]": ...
```

### 8.3 TemplateRepository

```python
class TemplateRepository(ABC):
    """Workflow Template storage. Templates are versioned runtime artifacts.

    The TemplateWorkflow loads templates through this repository
    rather than receiving them as direct payloads.
    """
    @abstractmethod
    async def save(self, template: WorkflowTemplate) -> None: ...
    @abstractmethod
    async def load(self, template_id: str, version: str) -> "WorkflowTemplate | None": ...
    @abstractmethod
    async def list_versions(self, template_id: str) -> list[str]: ...
```

---

## 9. Worker Bootstrap

```python
async def run_worker():
    config = load_config()
    registry = ActivityRegistry()

    # Register all activity adapters
    for adapter_cls in [IQAActivity, PPAActivity, MEAActivity, RDAActivity,
                        VDAActivity, RVAActivity, MTAActivity, HCAActivity]:
        adapter = adapter_cls()
        registry.register(adapter)

    # Register template loading activity
    registry.register(TemplateLoaderActivity(config.template_repository))

    # Create Temporal client
    client = await Client.connect(config.temporal_host)

    # Create worker with workflow and activities
    worker = Worker(
        client,
        task_queue=config.task_queue,
        workflows=[TemplateWorkflow],
        activities=[create_temporal_activity(a) for a in registry.all()],
    )

    await worker.run()
```

---

## 10. Temporal Feature Mapping

| Spec Requirement | Temporal Python SDK Feature | API |
|---|---|---|
| State machine driven | Workflow loop with explicit transitions | `while` + `_resolve_next_cp` |
| Pluggable activities | Dynamic activity dispatch | `execute_activity(activity_name_str, result_type=...)` |
| Template-driven topology | TemplateWorkflow interpreter | `@workflow.defn` + template reference as input |
| Template as artifact | Load via activity, not direct payload | `execute_activity("load_template", ...)` |
| Versioned templates | Patching + version field | `workflow.patched()` / `deprecate_patch()` |
| Retry | RetryPolicy | `temporalio.common.RetryPolicy` |
| Timeout | Activity timeout options | `schedule_to_close_timeout`, `start_to_close_timeout`, `heartbeat_timeout` |
| Human Gate — wait | Non-polling await | `workflow.wait_condition(fn, timeout)` |
| Human Gate — action | Update with validator | `@workflow.update` + `.validator` |
| Human Gate — inspect | Query | `@workflow.query` |
| Agent isolation | Single orchestrator pattern | All dispatch through TemplateWorkflow |
| Long running | ContinueAsNew | `workflow.continue_as_new()` |
| Workflow recovery | Event sourcing | Automatic via Temporal |
| Observability | Search Attributes | `workflow.upsert_search_attributes()` |
| Horizontal scaling | Multiple workers | Native Temporal Worker scaling |
| Future multi-tenant | Namespace isolation | `Client.connect()` + namespace config |
| Child workflows | Sub-orchestration | `workflow.execute_child_workflow()` |
| Parallel execution (future) | `asyncio.gather` + multiple activities | Domain model ready, runtime deferred |

---

## 11. Non-Functional Requirements Mapping

| NFR | Implementation |
|---|---|
| High Availability | Multiple Worker processes + Temporal Server cluster |
| Horizontal Scaling | Add Worker instances, same task queue |
| Long Running Workflows | `continue_as_new` after N transitions |
| Workflow Recovery | Temporal event sourcing (automatic) |
| Workflow Versioning | Template version field + `workflow.patched()` |
| Deterministic Execution | Temporal replay guarantees (no non-deterministic calls in workflow) |
| Auditability | `@workflow.query` + Search Attributes + AuditRepository |
| Observability | `upsert_search_attributes` at every CP transition |
| Future Multi-Tenant | Namespace-per-tenant (config-driven, no code change) |
| No custom runtime | All orchestration via Temporal-native primitives only |

---

## 12. Success Criteria Verification

1. **Add a new Agent by registering a new Activity Adapter** → Implement `ActivityAdapter`, call `registry.register()`, add `create_temporal_activity()` to worker. Zero changes to workflow runtime.

2. **Add a new Workflow by providing a new Workflow Template** → Store `WorkflowTemplate` in `TemplateRepository`, start workflow with `template_id` + `template_version` reference. Zero new workflow code.

3. **Upgrade Workflow Templates through versioning** → Template `version` field + `workflow.patched()` for in-place changes. Running instances unaffected.

4. **Integrate L4 data without changing Control Plane internals** → Implement `StateRepository` generic interface. L4 chooses its own backend (WeldMap, Neo4j, etc.). Control Plane has zero L4 domain knowledge.

5. **Integrate NATS/MES/SCADA without changing Workflow Runtime** → Implement `EventPort`/`DataPort`/`CommandPort`. Use in activities. Workflow runtime unchanged.

6. **Hot-swap Activity implementations** → Change `ActivityBinding` in template (version, task_queue, execution_target). No workflow code change. New adapter registered to registry with same or different version.

---

## 13. Test Strategy

- **Unit tests:** Domain models (template validation, transition resolution, ActivityBinding), ActivityRegistry
- **Integration tests:** TemplateWorkflow with `temporalio.testing.WorkflowTestRunner`
- **E2E tests:** Full workflow execution with mock activities against Temporal test server
- **Human Gate tests:** Update/Query flow with generic GateAction, timeout scenarios, REDIRECT flow
- **Template loading tests:** Verify template reference → load activity → template resolution

---

## 14. Project Files

### pyproject.toml

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
```

### requirements.txt

```
temporalio>=1.27.0
pydantic>=2.0
```

---

## Review Change Log

| # | Review Item | Change Applied |
|---|---|---|
| 1 | Template loading strategy | `run()` accepts `template_ref` dict, loads via `load_template` activity + `TemplateRepository` |
| 2 | Structured ActivityBinding | `ActivityBinding` dataclass with name, version, task_queue, execution_target. CP references `ActivityBinding` not string |
| 3 | No WeldMap dependency | Removed `WeldMapRepository` and `IndustrialMemoryRepository`. Replaced with generic `StateRepository`. No L4 domain concepts in L2 |
| 4 | Generalized Human Gate | `GateAction` (CONTINUE/REDIRECT/TERMINATE) replaces `GateDecision` (APPROVE/REJECT/ESCALATE). Business mapping at caller side |
| 5 | CP/Activity separation | Explicit documentation: "Control Points are never Activities. Activities are never States." ActivityBinding is optional on CP |
| 6 | Parallel execution model | `TransitionType` enum (SEQUENTIAL/PARALLEL/JOIN) + `parallel_targets` in `TransitionDefinition`. Runtime deferred, domain ready |
| 7 | Registry to infrastructure | `ActivityRegistry` moved from `adapter/registry.py` to `infrastructure/registry.py` |
| 8 | Reinforce Temporal-native | Added top-level Architectural Constraints section. Removed custom state machine engine. All retry/timeout/scheduling via Temporal |