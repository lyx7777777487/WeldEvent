# Phase 3 子项目 C — MCP 基础设施 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 cognitiveplane 内交付 MCP 抽象基础设施（MCPClient / MCPServer / MCPAdapter / MCPRegistry）+ 三层 ToolPolicy 判定器 + echo_server trivial stub，端到端打通 stub → Registry → ToolRegistry → ReActEngine → mock LLM → tool_call → result 回流管道。

**Architecture:** 三层分离 — MCPClient（传输层 ABC，Phase 3 只有 InProcessClient）→ MCPServer（一个 server 的连接包装）→ MCPAdapter（适配单工具为 BrainTool）→ MCPRegistry（发现 + 三层 ToolPolicy 判定 + 注册到 ToolRegistry）。MCP 唯一实现位置在 `adapters/mcp/`，`control/mcp_registry.py` 是薄桥接。L1 认知 MCP 与 L3 工业 MCP 不共享实现（§13 修正）。

**Tech Stack:** Python 3.11+ async/await, dataclasses, asyncio.CallbackList 替代品（自维护 list）, jsonschema, pytest, mock LLM provider (`capability/mock.py`)。

**Spec:** `docs/superpowers/specs/2026-06-25-phase3-c-mcp-infrastructure-design.md`

**验收:** C.1-C.5（见 spec §6.1）

---

## File Structure

### 新增文件

| 路径 | 职责 |
|---|---|
| `cognitiveplane/adapters/mcp/__init__.py` | 包导出 |
| `cognitiveplane/adapters/mcp/base.py` | MCPClient ABC + MCPServer + MCPAdapter + ToolDescriptor + MCPAnnotations + MCPPolicyDecision |
| `cognitiveplane/adapters/mcp/in_process.py` | InProcessClient（Phase 3 唯一传输）|
| `cognitiveplane/adapters/mcp/tool_policy_classifier.py` | MCPToolPolicyClassifier 三层判定 |
| `cognitiveplane/adapters/mcp/stubs/__init__.py` | stubs 包 |
| `cognitiveplane/adapters/mcp/stubs/echo_server.py` | trivial stub MCP server（1 个工具 echo_tool）|
| `cognitiveplane/control/mcp_registry.py` | MCPRegistry |
| `cognitiveplane/governance/mcp_policy.yaml` | 第三层 override 配置（空 + 注释样例）|
| `cognitiveplane/tests/test_adapters/test_mcp/__init__.py` | 测试包 |
| `cognitiveplane/tests/test_adapters/test_mcp/test_in_process_client.py` | 单元 |
| `cognitiveplane/tests/test_adapters/test_mcp/test_mcp_adapter.py` | 单元 |
| `cognitiveplane/tests/test_adapters/test_mcp/test_tool_policy_classifier.py` | 单元 |
| `cognitiveplane/tests/test_adapters/test_mcp/test_mcp_registry.py` | 单元 |
| `cognitiveplane/tests/test_adapters/test_mcp/test_echo_server_e2e.py` | 端到端 |

### 改动文件

| 路径 | 改动 |
|---|---|
| `cognitiveplane/control/deps.py` | 解锁 `ControlDeps.mcp_registry` 字段 |
| `cognitiveplane/control/tool_registry.py` | 增加 `unregister(name)` 方法 |

### 不改文件

`control/react.py`（ReActEngine 不感知工具来源）、`control/hooks.py`（Hook 对 BrainTool 通用）、`governance/tool_policy.py`（内部工具用，与 MCP 判定器不冲突）。

---

## Task 1: ToolDescriptor + MCPAnnotations 数据结构

**Files:**
- Create: `cognitiveplane/adapters/mcp/__init__.py`
- Create: `cognitiveplane/adapters/mcp/base.py`
- Test: `cognitiveplane/tests/test_adapters/test_mcp/__init__.py`
- Test: `cognitiveplane/tests/test_adapters/test_mcp/test_base_dataclasses.py`

- [ ] **Step 1: 创建包与测试目录**

```bash
mkdir -p cognitiveplane/adapters/mcp/stubs
mkdir -p cognitiveplane/tests/test_adapters/test_mcp
touch cognitiveplane/adapters/mcp/__init__.py
touch cognitiveplane/adapters/mcp/stubs/__init__.py
touch cognitiveplane/tests/test_adapters/test_mcp/__init__.py
```

- [ ] **Step 2: 写失败测试 — ToolDescriptor 与 MCPAnnotations 不可变性 + frozen**

Create `cognitiveplane/tests/test_adapters/test_mcp/test_base_dataclasses.py`:

```python
"""Test ToolDescriptor + MCPAnnotations dataclass contracts.

Spec: §2.3 — frozen dataclasses, used as MCPClient.list_tools() return type.
"""
from __future__ import annotations

import dataclasses

from cognitiveplane.adapters.mcp.base import (
    MCPAnnotations,
    MCPPolicyDecision,
    ToolDescriptor,
)


def test_tool_descriptor_minimal():
    """ToolDescriptor with only required fields — annotations default None."""
    desc = ToolDescriptor(
        name="echo_tool",
        description="Echoes input back",
        parameters_schema={"type": "object", "properties": {}},
    )
    assert desc.name == "echo_tool"
    assert desc.description == "Echoes input back"
    assert desc.parameters_schema == {"type": "object", "properties": {}}
    assert desc.annotations is None


def test_tool_descriptor_with_annotations():
    desc = ToolDescriptor(
        name="search_docs",
        description="Search documents",
        parameters_schema={},
        annotations=MCPAnnotations(readOnlyHint=True),
    )
    assert desc.annotations is not None
    assert desc.annotations.readOnlyHint is True
    assert desc.annotations.destructiveHint is None
    assert desc.annotations.openWorldHint is None


def test_tool_descriptor_frozen():
    """Frozen — cannot mutate after construction."""
    desc = ToolDescriptor(name="x", description="x", parameters_schema={})
    try:
        desc.name = "y"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    assert False, "Expected FrozenInstanceError"


def test_mcp_policy_decision_defaults():
    """MCPPolicyDecision — require_reason defaults False, reason defaults empty."""
    decision = MCPPolicyDecision(auto_approve=True, tier="A")
    assert decision.auto_approve is True
    assert decision.tier == "A"
    assert decision.require_reason is False
    assert decision.reason == ""
```

- [ ] **Step 3: 运行测试验证失败**

Run: `cd cognitiveplane && python -m pytest tests/test_adapters/test_mcp/test_base_dataclasses.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cognitiveplane.adapters.mcp.base'`

- [ ] **Step 4: 实现 base.py 数据结构**

Create `cognitiveplane/adapters/mcp/base.py`:

```python
"""MCP 基础数据结构 + ABC 定义。

Spec: docs/superpowers/specs/2026-06-25-phase3-c-mcp-infrastructure-design.md §2.

L1 认知 MCP 基础设施 — 仅抽象层 + InProcess 传输。
真实认知 MCP（文档检索等）按需接入；工业执行 MCP（detect_defects 等）
在 executionplane 仓库，不在本包。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class MCPAnnotations:
    """MCP 协议 annotations 字段 — 工具自声明安全属性。

    Spec §4.2.1 第二层判定来源。None 表示工具未声明该 hint。
    """
    readOnlyHint: bool | None = None
    destructiveHint: bool | None = None
    openWorldHint: bool | None = None


@dataclass(frozen=True)
class ToolDescriptor:
    """MCP 工具描述 — MCPClient.list_tools() 返回。

    name: 工具名（如 "echo_tool" / 未来的 "search_docs"）
    description: 给 LLM 看的工具说明
    parameters_schema: JSON Schema，给 LLM function calling 用
    annotations: §4.2.1 第二层判定来源，None 表示工具未声明
    """
    name: str
    description: str
    parameters_schema: dict
    annotations: MCPAnnotations | None = None


@dataclass(frozen=True)
class MCPPolicyDecision:
    """三层 ToolPolicy 判定结果 — 缓存到 MCPAdapter.metadata。

    Spec §4.2 — 判定顺序: YAML override > annotations > 前缀启发式 > 保守 Tier-B。
    """
    auto_approve: bool
    tier: str  # "A" (retriable) / "B" (precise, require approval)
    require_reason: bool = False
    reason: str = ""


# MCPClient / MCPServer / MCPAdapter 定义在后续 task 加入此文件
```

- [ ] **Step 5: 运行测试验证通过**

Run: `cd cognitiveplane && python -m pytest tests/test_adapters/test_mcp/test_base_dataclasses.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add cognitiveplane/adapters/mcp/__init__.py \
        cognitiveplane/adapters/mcp/stubs/__init__.py \
        cognitiveplane/adapters/mcp/base.py \
        cognitiveplane/tests/test_adapters/test_mcp/__init__.py \
        cognitiveplane/tests/test_adapters/test_mcp/test_base_dataclasses.py
git commit -m "feat(mcp): add ToolDescriptor/MCPAnnotations/MCPPolicyDecision dataclasses"
```

---

## Task 2: MCPClient ABC

**Files:**
- Modify: `cognitiveplane/adapters/mcp/base.py`
- Test: `cognitiveplane/tests/test_adapters/test_mcp/test_mcp_client_abc.py`

- [ ] **Step 1: 写失败测试 — MCPClient 是 ABC，子类必须实现所有抽象方法**

Create `cognitiveplane/tests/test_adapters/test_mcp/test_mcp_client_abc.py`:

```python
"""Test MCPClient ABC contract.

Spec: §2.4 — MCPClient is the transport-layer ABC with three abstract methods.
"""
from __future__ import annotations

import inspect

import pytest

from cognitiveplane.adapters.mcp.base import MCPClient, ToolDescriptor


def test_mcp_client_is_abc():
    """MCPClient cannot be instantiated directly."""
    with pytest.raises(TypeError):
        MCPClient()  # type: ignore[abstract]


def test_mcp_client_abstract_methods():
    """All three methods are abstract — subclasses must implement them."""
    abstract_methods = {
        name
        for name, member in inspect.getmembers(MCPClient)
        if getattr(member, "__isabstractmethod__", False)
    }
    assert abstract_methods == {"list_tools", "call_tool", "subscribe_list_changed"}


def test_mcp_client_subclass_must_implement_all():
    """Subclass missing any abstract method still abstract."""
    class Partial(MCPClient):
        async def list_tools(self):
            return []

    with pytest.raises(TypeError):
        Partial()  # type: ignore[abstract]


def test_mcp_client_full_subclass_instantiable():
    """Full implementation can be instantiated."""
    class Full(MCPClient):
        async def list_tools(self):
            return []

        async def call_tool(self, name, arguments):
            return {}

        def subscribe_list_changed(self, callback):
            pass

    client = Full()
    assert isinstance(client, MCPClient)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd cognitiveplane && python -m pytest tests/test_adapters/test_mcp/test_mcp_client_abc.py -v`
Expected: FAIL — `ImportError: cannot import name 'MCPClient'`

- [ ] **Step 3: 实现 MCPClient ABC — 追加到 base.py 末尾**

Append to `cognitiveplane/adapters/mcp/base.py`:

```python


class MCPClient(ABC):
    """MCP 传输层抽象。

    Spec §2.4 — Phase 3 只有 InProcessClient；Phase 5+ 加 StdioClient/SSEClient。
    上层（MCPServer / MCPAdapter / MCPRegistry / ToolRegistry / ReActEngine）
    不感知传输实现。
    """

    @abstractmethod
    async def list_tools(self) -> list[ToolDescriptor]:
        """返回该 server 暴露的所有工具描述。"""

    @abstractmethod
    async def call_tool(self, name: str, arguments: dict) -> dict:
        """调用工具，返回结构化结果。"""

    @abstractmethod
    def subscribe_list_changed(
        self, callback: Callable[[], Awaitable[None]]
    ) -> None:
        """订阅 tools/list_changed 事件。

        callback 在工具列表变更时被调用（异步）。MCPRegistry 用此触发重判。
        """
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd cognitiveplane && python -m pytest tests/test_adapters/test_mcp/test_mcp_client_abc.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add cognitiveplane/adapters/mcp/base.py \
        cognitiveplane/tests/test_adapters/test_mcp/test_mcp_client_abc.py
git commit -m "feat(mcp): add MCPClient ABC with three abstract transport methods"
```

---

## Task 3: MCPServer

**Files:**
- Modify: `cognitiveplane/adapters/mcp/base.py`
- Test: `cognitiveplane/tests/test_adapters/test_mcp/test_mcp_server.py`

- [ ] **Step 1: 写失败测试 — MCPServer 委托 client + 转发 list_changed**

Create `cognitiveplane/tests/test_adapters/test_mcp/test_mcp_server.py`:

```python
"""Test MCPServer — connection wrapper delegating to MCPClient.

Spec: §2.4 + §3.1.
"""
from __future__ import annotations

import asyncio

import pytest

from cognitiveplane.adapters.mcp.base import (
    MCPClient,
    MCPServer,
    ToolDescriptor,
)


class FakeClient(MCPClient):
    """Test double — records calls and lets test drive list_changed."""

    def __init__(self, tools: list[ToolDescriptor], call_result: dict | None = None):
        self._tools = tools
        self._call_result = call_result or {}
        self.call_log: list[tuple[str, dict]] = []
        self._listeners: list = []

    async def list_tools(self) -> list[ToolDescriptor]:
        return self._tools

    async def call_tool(self, name: str, arguments: dict) -> dict:
        self.call_log.append((name, arguments))
        return self._call_result

    def subscribe_list_changed(self, callback) -> None:
        self._listeners.append(callback)

    async def fire_list_changed(self) -> None:
        """Test helper — fire list_changed to all subscribers."""
        for cb in self._listeners:
            await cb()


@pytest.mark.asyncio
async def test_server_list_tools_delegates_to_client():
    tools = [ToolDescriptor(name="t1", description="d1", parameters_schema={})]
    client = FakeClient(tools)
    server = MCPServer(client=client, name="fake")

    result = await server.list_tools()

    assert result == tools


@pytest.mark.asyncio
async def test_server_call_tool_delegates_to_client():
    client = FakeClient([], call_result={"echo": "hello"})
    server = MCPServer(client=client, name="fake")

    result = await server.call_tool("echo_tool", {"text": "hello"})

    assert result == {"echo": "hello"}
    assert client.call_log == [("echo_tool", {"text": "hello"})]


@pytest.mark.asyncio
async def test_server_on_list_changed_forwards_to_client_subscribe():
    client = FakeClient([])
    server = MCPServer(client=client, name="fake")

    fired = asyncio.Event()

    async def listener():
        fired.set()

    server.on_list_changed(listener)
    await client.fire_list_changed()

    assert fired.is_set()


def test_server_name_property():
    client = FakeClient([])
    server = MCPServer(client=client, name="my_server")
    assert server.name == "my_server"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd cognitiveplane && python -m pytest tests/test_adapters/test_mcp/test_mcp_server.py -v`
Expected: FAIL — `ImportError: cannot import name 'MCPServer'`

- [ ] **Step 3: 实现 MCPServer — 追加到 base.py**

Append to `cognitiveplane/adapters/mcp/base.py`:

```python


class MCPServer:
    """一个 MCP server 的连接包装。

    Spec §2.4 — 持有 MCPClient，暴露 list_tools / call_tool / on_list_changed。
    Server 本身无状态 — 每次调用都委托 client。
    """

    def __init__(self, client: MCPClient, name: str) -> None:
        self._client = client
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def list_tools(self) -> list[ToolDescriptor]:
        return await self._client.list_tools()

    async def call_tool(self, name: str, arguments: dict) -> dict:
        return await self._client.call_tool(name, arguments)

    def on_list_changed(
        self, callback: Callable[[], Awaitable[None]]
    ) -> None:
        """订阅 list_changed 事件 — 转发给 client.subscribe_list_changed。"""
        self._client.subscribe_list_changed(callback)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd cognitiveplane && python -m pytest tests/test_adapters/test_mcp/test_mcp_server.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add cognitiveplane/adapters/mcp/base.py \
        cognitiveplane/tests/test_adapters/test_mcp/test_mcp_server.py
git commit -m "feat(mcp): add MCPServer connection wrapper delegating to MCPClient"
```

---

## Task 4: MCPAdapter (BrainTool 适配器)

**Files:**
- Modify: `cognitiveplane/adapters/mcp/base.py`
- Test: `cognitiveplane/tests/test_adapters/test_mcp/test_mcp_adapter.py`

- [ ] **Step 1: 写失败测试 — MCPAdapter 是 BrainTool，execute 委托 server.call_tool**

Create `cognitiveplane/tests/test_adapters/test_mcp/test_mcp_adapter.py`:

```python
"""Test MCPAdapter — adapts a single MCP tool to BrainTool.

Spec: §2.4 + §3.2 steps 11-15.
"""
from __future__ import annotations

import pytest

from cognitiveplane.adapters.mcp.base import (
    MCPAdapter,
    MCPPolicyDecision,
    MCPServer,
    ToolDescriptor,
)
from cognitiveplane.control.tools import BrainTool


@pytest.mark.asyncio
async def test_adapter_is_braintool():
    """C.1 验收 — MCPAdapter 必须是 BrainTool 子类。"""
    desc = ToolDescriptor(name="echo_tool", description="d", parameters_schema={})
    policy = MCPPolicyDecision(auto_approve=False, tier="B")
    # MCPServer 构造需要 MCPClient；用 None 占位因为测试不调 execute
    adapter = MCPAdapter(server=None, descriptor=desc, policy=policy)  # type: ignore[arg-type]
    assert isinstance(adapter, BrainTool)


def test_adapter_name_description_schema_from_descriptor():
    desc = ToolDescriptor(
        name="search_docs",
        description="Search documents",
        parameters_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )
    policy = MCPPolicyDecision(auto_approve=True, tier="A")
    adapter = MCPAdapter(server=None, descriptor=desc, policy=policy)  # type: ignore[arg-type]

    assert adapter.name == "search_docs"
    assert adapter.description == "Search documents"
    assert adapter.parameters_schema == {
        "type": "object",
        "properties": {"q": {"type": "string"}},
    }


def test_adapter_to_function_definition_format():
    """function definition 与内部工具格式一致 — LLM 不感知来源。"""
    desc = ToolDescriptor(
        name="echo_tool",
        description="Echo input",
        parameters_schema={"type": "object", "properties": {}},
    )
    policy = MCPPolicyDecision(auto_approve=False, tier="B")
    adapter = MCPAdapter(server=None, descriptor=desc, policy=policy)  # type: ignore[arg-type]

    definition = adapter.to_function_definition()

    assert definition == {
        "type": "function",
        "function": {
            "name": "echo_tool",
            "description": "Echo input",
            "parameters": {"type": "object", "properties": {}},
        },
    }


@pytest.mark.asyncio
async def test_adapter_execute_delegates_to_server_call_tool():
    """Spec §3.2 step 12 — adapter.execute 委托 server.call_tool。"""
    desc = ToolDescriptor(name="echo_tool", description="d", parameters_schema={})
    policy = MCPPolicyDecision(auto_approve=False, tier="B")

    class FakeServer(MCPServer):  # type: ignore[misc]
        def __init__(self):
            self.call_log: list[tuple[str, dict]] = []

        async def call_tool(self, name, arguments):
            self.call_log.append((name, arguments))
            return {"echo": arguments.get("text")}

    server = FakeServer()  # type: ignore[call-arg]
    adapter = MCPAdapter(server=server, descriptor=desc, policy=policy)

    result = await adapter.execute(text="hello")

    assert result.error is None
    assert result.output == {"echo": "hello"}
    assert server.call_log == [("echo_tool", {"text": "hello"})]


@pytest.mark.asyncio
async def test_adapter_execute_wraps_call_failure_as_tool_result_error():
    """Spec §5 — call_tool 抛异常时返回 ToolResult(error=...), 不抛到上层。"""
    desc = ToolDescriptor(name="bad_tool", description="d", parameters_schema={})
    policy = MCPPolicyDecision(auto_approve=False, tier="B")

    class FailingServer(MCPServer):  # type: ignore[misc]
        async def call_tool(self, name, arguments):
            raise RuntimeError("connection refused")

    server = FailingServer()  # type: ignore[call-arg]
    adapter = MCPAdapter(server=server, descriptor=desc, policy=policy)

    result = await adapter.execute(text="x")

    assert result.error is not None
    assert "mcp_call_failed" in result.error
    assert "connection refused" in result.error


def test_adapter_policy_metadata_exposed():
    """Spec §4 — policy 缓存到 adapter，Hook 拦截时可直接读。"""
    desc = ToolDescriptor(name="echo_tool", description="d", parameters_schema={})
    policy = MCPPolicyDecision(
        auto_approve=False, tier="B", require_reason=True, reason="prefix:unknown:conservative"
    )
    adapter = MCPAdapter(server=None, descriptor=desc, policy=policy)  # type: ignore[arg-type]

    assert adapter.policy == policy
    assert adapter.policy.reason == "prefix:unknown:conservative"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd cognitiveplane && python -m pytest tests/test_adapters/test_mcp/test_mcp_adapter.py -v`
Expected: FAIL — `ImportError: cannot import name 'MCPAdapter'`

- [ ] **Step 3: 实现 MCPAdapter — 追加到 base.py**

Append to `cognitiveplane/adapters/mcp/base.py`. 需要在文件顶部加 `BrainTool` + `ToolResult` 的 import:

```python
from cognitiveplane.control.tools import BrainTool, ToolResult
```

然后在文件末尾追加：

```python


class MCPAdapter(BrainTool):
    """适配单个 MCP 工具为 BrainTool。

    Spec §2.4 + §3.2 — 无状态，每次 execute 都委托 server.call_tool。
    LLM 通过 ToolRegistry 看到的接口与内部工具完全一致。
    """

    def __init__(
        self,
        server: MCPServer,
        descriptor: ToolDescriptor,
        policy: MCPPolicyDecision,
    ) -> None:
        self._server = server
        self._descriptor = descriptor
        self._policy = policy

    @property
    def name(self) -> str:
        return self._descriptor.name

    @property
    def description(self) -> str:
        return self._descriptor.description

    @property
    def parameters_schema(self) -> dict:
        return self._descriptor.parameters_schema

    @property
    def policy(self) -> MCPPolicyDecision:
        """缓存的三层判定结果 — Hook 拦截时读此字段，不重判。"""
        return self._policy

    async def execute(self, **kwargs) -> ToolResult:
        try:
            result = await self._server.call_tool(self._descriptor.name, kwargs)
            return ToolResult(output=result)
        except Exception as e:
            return ToolResult(
                error=f"mcp_call_failed: {e}",
                error_type="mcp_call_failed",
            )
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd cognitiveplane && python -m pytest tests/test_adapters/test_mcp/test_mcp_adapter.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add cognitiveplane/adapters/mcp/base.py \
        cognitiveplane/tests/test_adapters/test_mcp/test_mcp_adapter.py
git commit -m "feat(mcp): add MCPAdapter — adapts single MCP tool to BrainTool"
```

---

## Task 5: InProcessClient

**Files:**
- Create: `cognitiveplane/adapters/mcp/in_process.py`
- Test: `cognitiveplane/tests/test_adapters/test_mcp/test_in_process_client.py`

- [ ] **Step 1: 写失败测试 — InProcessClient 路由到 Python 函数 + 订阅机制**

Create `cognitiveplane/tests/test_adapters/test_mcp/test_in_process_client.py`:

```python
"""Test InProcessClient — Phase 3 唯一传输实现.

Spec §2.4 + §3.2 steps 13-14 — call_tool 直接调 Python 函数。
"""
from __future__ import annotations

import asyncio

import pytest

from cognitiveplane.adapters.mcp.base import MCPAnnotations, ToolDescriptor
from cognitiveplane.adapters.mcp.in_process import InProcessClient


@pytest.mark.asyncio
async def test_list_tools_returns_registered_descriptors():
    tools = [
        ToolDescriptor(
            name="echo_tool",
            description="Echo input",
            parameters_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            annotations=MCPAnnotations(readOnlyHint=True),
        ),
    ]

    async def echo_handler(text: str) -> dict:
        return {"echo": text}

    client = InProcessClient(tools=tools, handlers={"echo_tool": echo_handler})

    result = await client.list_tools()

    assert len(result) == 1
    assert result[0].name == "echo_tool"
    assert result[0].annotations is not None
    assert result[0].annotations.readOnlyHint is True


@pytest.mark.asyncio
async def test_call_tool_routes_to_handler():
    desc = ToolDescriptor(name="echo_tool", description="d", parameters_schema={})

    async def echo_handler(text: str) -> dict:
        return {"echo": text}

    client = InProcessClient(tools=[desc], handlers={"echo_tool": echo_handler})

    result = await client.call_tool("echo_tool", {"text": "hello"})

    assert result == {"echo": "hello"}


@pytest.mark.asyncio
async def test_call_tool_unknown_tool_raises():
    """Unknown tool — InProcessClient cannot route, raises KeyError.

    上层 MCPAdapter 会捕获并转为 ToolResult(error=...) — 见 Task 4 测试。
    """
    client = InProcessClient(tools=[], handlers={})

    with pytest.raises(KeyError):
        await client.call_tool("nonexistent", {})


@pytest.mark.asyncio
async def test_call_tool_handler_missing_raises():
    """Tool descriptor registered but no handler — configuration error."""
    desc = ToolDescriptor(name="orphan", description="d", parameters_schema={})
    client = InProcessClient(tools=[desc], handlers={})  # no handler for "orphan"

    with pytest.raises(KeyError):
        await client.call_tool("orphan", {})


@pytest.mark.asyncio
async def test_subscribe_list_changed_callback_invoked_on_fire():
    """subscribe_list_changed — callback fires when fire_list_changed called."""
    client = InProcessClient(tools=[], handlers={})

    fired = asyncio.Event()

    async def listener():
        fired.set()

    client.subscribe_list_changed(listener)
    await client.fire_list_changed()

    assert fired.is_set()


@pytest.mark.asyncio
async def test_subscribe_multiple_listeners_all_invoked():
    client = InProcessClient(tools=[], handlers={})

    count = {"n": 0}

    async def listener1():
        count["n"] += 1

    async def listener2():
        count["n"] += 10

    client.subscribe_list_changed(listener1)
    client.subscribe_list_changed(listener2)
    await client.fire_list_changed()

    assert count["n"] == 11
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd cognitiveplane && python -m pytest tests/test_adapters/test_mcp/test_in_process_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cognitiveplane.adapters.mcp.in_process'`

- [ ] **Step 3: 实现 InProcessClient**

Create `cognitiveplane/adapters/mcp/in_process.py`:

```python
"""InProcessClient — Phase 3 唯一 MCP 传输实现。

Spec §2.4 — call_tool 直接调 Python 函数；Phase 5+ 加 StdioClient 时
call_tool 改成 JSON-RPC over stdin/stdout，上层（MCPServer /
MCPAdapter / MCPRegistry / ToolRegistry / ReActEngine）全部不变。

list_changed 事件由 server 实现主动调用 fire_list_changed 触发 —
真实 MCP server 是协议通知，stub 是显式调用。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from cognitiveplane.adapters.mcp.base import MCPClient, ToolDescriptor

ToolHandler = Callable[..., Awaitable[dict[str, Any]]]
ListChangedCallback = Callable[[], Awaitable[None]]


class InProcessClient(MCPClient):
    """In-process transport — tools are Python functions.

    tools: 该 server 暴露的工具描述列表
    handlers: tool_name → async callable，返回 dict 结果
    """

    def __init__(
        self,
        tools: list[ToolDescriptor],
        handlers: dict[str, ToolHandler],
    ) -> None:
        self._tools = tools
        self._handlers = handlers
        self._list_changed_listeners: list[ListChangedCallback] = []

    async def list_tools(self) -> list[ToolDescriptor]:
        return list(self._tools)

    async def call_tool(self, name: str, arguments: dict) -> dict:
        if name not in self._handlers:
            raise KeyError(f"no handler registered for tool '{name}'")
        return await self._handlers[name](**arguments)

    def subscribe_list_changed(
        self, callback: ListChangedCallback
    ) -> None:
        self._list_changed_listeners.append(callback)

    async def fire_list_changed(self) -> None:
        """Stub 专用 — 显式触发 list_changed 事件。

        真实 MCP server（StdioClient/SSEClient）由协议通知触发，
        不需要此方法。stub 用它模拟 server 端工具列表变更。
        """
        for cb in self._list_changed_listeners:
            await cb()
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd cognitiveplane && python -m pytest tests/test_adapters/test_mcp/test_in_process_client.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add cognitiveplane/adapters/mcp/in_process.py \
        cognitiveplane/tests/test_adapters/test_mcp/test_in_process_client.py
git commit -m "feat(mcp): add InProcessClient — Phase 3 in-process transport"
```

---

## Task 6: MCPToolPolicyClassifier 三层判定

**Files:**
- Create: `cognitiveplane/adapters/mcp/tool_policy_classifier.py`
- Create: `cognitiveplane/governance/mcp_policy.yaml`
- Test: `cognitiveplane/tests/test_adapters/test_mcp/test_tool_policy_classifier.py`

- [ ] **Step 1: 写 governance/mcp_policy.yaml 空配置 + 注释样例**

Create `cognitiveplane/governance/mcp_policy.yaml`:

```yaml
# governance/mcp_policy.yaml
# Phase 3 第三层人工 override 配置。
# 优先级最高 — 覆盖 annotations 判定和前缀启发式。
# Phase 3 起步为空，按需添加 override。
#
# 格式:
#   <tool_name>:
#     auto_approve: <bool>
#     tier: "A" | "B"
#     require_reason: <bool>
#
# 示例 (Phase 3 不启用，注释样例):
# echo_tool:
#   auto_approve: true
#   tier: "A"
#   require_reason: false
```

- [ ] **Step 2: 写失败测试 — 三层判定全覆盖**

Create `cognitiveplane/tests/test_adapters/test_mcp/test_tool_policy_classifier.py`:

```python
"""Test MCPToolPolicyClassifier — 三层 ToolPolicy 判定.

Spec §4 — 判定顺序: YAML override > annotations > 前缀启发式 > 保守 Tier-B。
注意: 本设计把 §4.2.1 字面顺序"annotations → 前缀 → YAML"调整为
"YAML > annotations > 前缀"，遵循 §4.2.1 "人工配置始终优先"明文约束。
偏差已记录在 spec §8。
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from cognitiveplane.adapters.mcp.base import MCPAnnotations, ToolDescriptor
from cognitiveplane.adapters.mcp.tool_policy_classifier import (
    MCPToolPolicyClassifier,
)


def _desc(name: str, annotations: MCPAnnotations | None = None) -> ToolDescriptor:
    return ToolDescriptor(
        name=name, description="d", parameters_schema={}, annotations=annotations
    )


# ── 第一层: YAML override (最高优先级) ──

def test_yaml_override_takes_precedence_over_annotations(tmp_path: Path):
    """YAML override > annotations — 即使 readOnlyHint=True, YAML 说不要 auto_approve 就不要。"""
    yaml_path = tmp_path / "mcp_policy.yaml"
    yaml_path.write_text(dedent("""
        search_docs:
          auto_approve: false
          tier: "B"
          require_reason: true
    """))

    classifier = MCPToolPolicyClassifier(yaml_path=yaml_path)
    desc = _desc("search_docs", MCPAnnotations(readOnlyHint=True))

    decision = classifier.classify(desc)

    assert decision.auto_approve is False
    assert decision.tier == "B"
    assert decision.require_reason is True
    assert decision.reason == "yaml_override"


def test_yaml_override_takes_precedence_over_prefix(tmp_path: Path):
    """YAML override > 前缀 — 即使名字以 read_ 开头, YAML 说 Tier-B 就 Tier-B。"""
    yaml_path = tmp_path / "mcp_policy.yaml"
    yaml_path.write_text("read_special:\n  auto_approve: false\n  tier: \"B\"\n")

    classifier = MCPToolPolicyClassifier(yaml_path=yaml_path)
    desc = _desc("read_special")

    decision = classifier.classify(desc)

    assert decision.auto_approve is False
    assert decision.tier == "B"
    assert decision.reason == "yaml_override"


def test_yaml_missing_file_falls_through_to_other_layers():
    """No YAML path — skip override layer, continue to annotations/prefix."""
    classifier = MCPToolPolicyClassifier(yaml_path=None)
    desc = _desc("read_docs", MCPAnnotations(readOnlyHint=True))

    decision = classifier.classify(desc)

    # annotations layer fires
    assert decision.auto_approve is True
    assert decision.tier == "A"
    assert decision.reason == "annotations.readOnlyHint"


# ── 第二层: MCP annotations ──

def test_annotations_readonly_hint_auto_approve_tier_a():
    classifier = MCPToolPolicyClassifier(yaml_path=None)
    desc = _desc("anything", MCPAnnotations(readOnlyHint=True))

    decision = classifier.classify(desc)

    assert decision.auto_approve is True
    assert decision.tier == "A"
    assert decision.reason == "annotations.readOnlyHint"


def test_annotations_destructive_hint_tier_b_require_reason():
    classifier = MCPToolPolicyClassifier(yaml_path=None)
    desc = _desc("anything", MCPAnnotations(destructiveHint=True))

    decision = classifier.classify(desc)

    assert decision.auto_approve is False
    assert decision.tier == "B"
    assert decision.require_reason is True
    assert decision.reason == "annotations.destructiveHint"


def test_annotations_openworld_hint_tier_b():
    classifier = MCPToolPolicyClassifier(yaml_path=None)
    desc = _desc("anything", MCPAnnotations(openWorldHint=True))

    decision = classifier.classify(desc)

    assert decision.auto_approve is False
    assert decision.tier == "B"
    assert decision.reason == "annotations.openWorldHint"


def test_annotations_all_none_falls_through_to_prefix():
    """annotations present but all hints None — skip annotations layer."""
    classifier = MCPToolPolicyClassifier(yaml_path=None)
    desc = _desc("read_docs", MCPAnnotations())  # all None

    decision = classifier.classify(desc)

    # prefix layer fires
    assert decision.auto_approve is True
    assert decision.tier == "A"
    assert decision.reason == "prefix:read"


# ── 第三层: 名字前缀启发式 ──

@pytest.mark.parametrize("prefix", ["read_", "get_", "list_", "search_", "find_", "query_"])
def test_prefix_read_tier_a_auto_approve(prefix: str):
    classifier = MCPToolPolicyClassifier(yaml_path=None)
    desc = _desc(f"{prefix}docs")

    decision = classifier.classify(desc)

    assert decision.auto_approve is True
    assert decision.tier == "A"
    assert decision.reason == "prefix:read"


@pytest.mark.parametrize(
    "prefix", ["create_", "update_", "delete_", "import_", "send_", "publish_", "post_"]
)
def test_prefix_write_tier_b_require_reason(prefix: str):
    classifier = MCPToolPolicyClassifier(yaml_path=None)
    desc = _desc(f"{prefix}thing")

    decision = classifier.classify(desc)

    assert decision.auto_approve is False
    assert decision.tier == "B"
    assert decision.require_reason is True
    assert decision.reason == "prefix:write"


# ── 未匹配 → 保守 Tier-B ──

def test_unknown_prefix_conservative_tier_b():
    """echo_tool — 不匹配 read/write 前缀, 落入保守 Tier-B."""
    classifier = MCPToolPolicyClassifier(yaml_path=None)
    desc = _desc("echo_tool")

    decision = classifier.classify(desc)

    assert decision.auto_approve is False
    assert decision.tier == "B"
    assert decision.require_reason is False  # 保守但不强制 reason
    assert decision.reason == "prefix:unknown:conservative"


# ── YAML 格式错误 fallback ──

def test_yaml_malformed_falls_back_to_conservative(tmp_path: Path, capsys):
    """Spec §5 — YAML 格式错时记 EventLog + fallback 保守 Tier-B.

    本测试只验证 fallback 行为；EventLog 记录在 Task 7 MCPRegistry 集成时验证。
    """
    yaml_path = tmp_path / "mcp_policy.yaml"
    yaml_path.write_text("this: is: not: valid: yaml: [")

    classifier = MCPToolPolicyClassifier(yaml_path=yaml_path)
    desc = _desc("echo_tool")

    decision = classifier.classify(desc)

    # YAML 解析失败 → 跳过 override 层 → echo_tool 落入保守 Tier-B
    assert decision.auto_approve is False
    assert decision.tier == "B"
    assert decision.reason == "prefix:unknown:conservative"
```

- [ ] **Step 3: 运行测试验证失败**

Run: `cd cognitiveplane && python -m pytest tests/test_adapters/test_mcp/test_tool_policy_classifier.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cognitiveplane.adapters.mcp.tool_policy_classifier'`

- [ ] **Step 4: 实现 MCPToolPolicyClassifier**

Create `cognitiveplane/adapters/mcp/tool_policy_classifier.py`:

```python
"""MCPToolPolicyClassifier — 三层 ToolPolicy 判定.

Spec §4 — 判定顺序: YAML override > annotations > 前缀启发式 > 保守 Tier-B。

偏差: §4.2.1 字面顺序是 "annotations → 前缀 → YAML override"，但
§4.2.1 明文写 "人工配置始终优先于自动判定"。本设计遵循明文约束，
把 YAML override 提到最前。偏差已记录在 spec §8。
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from cognitiveplane.adapters.mcp.base import MCPPolicyDecision, ToolDescriptor

logger = logging.getLogger(__name__)

READ_PREFIXES = ("read_", "get_", "list_", "search_", "find_", "query_")
WRITE_PREFIXES = (
    "create_",
    "update_",
    "delete_",
    "import_",
    "send_",
    "publish_",
    "post_",
)


class MCPToolPolicyClassifier:
    """三层 ToolPolicy 判定器 — 无状态，可复用。"""

    def __init__(self, yaml_path: Path | None = None) -> None:
        self._yaml_overrides: dict[str, dict] = {}
        if yaml_path is not None:
            self._yaml_overrides = self._load_yaml(yaml_path)

    @staticmethod
    def _load_yaml(yaml_path: Path) -> dict[str, dict]:
        """加载 YAML override 配置。格式错时返回空 dict 并记日志。"""
        try:
            with open(yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                logger.warning("mcp_policy.yaml root is not a dict — ignoring overrides")
                return {}
            return data
        except yaml.YAMLError as e:
            logger.warning("mcp_policy.yaml parse failed: %s — ignoring overrides", e)
            return {}
        except FileNotFoundError:
            return {}

    def classify(self, descriptor: ToolDescriptor) -> MCPPolicyDecision:
        # 第一层: YAML override (最高优先级)
        override = self._yaml_overrides.get(descriptor.name)
        if override is not None:
            return MCPPolicyDecision(
                auto_approve=override.get("auto_approve", False),
                tier=override.get("tier", "B"),
                require_reason=override.get("require_reason", False),
                reason="yaml_override",
            )

        # 第二层: MCP annotations
        if descriptor.annotations is not None:
            if descriptor.annotations.readOnlyHint is True:
                return MCPPolicyDecision(
                    auto_approve=True, tier="A", reason="annotations.readOnlyHint"
                )
            if descriptor.annotations.destructiveHint is True:
                return MCPPolicyDecision(
                    auto_approve=False,
                    tier="B",
                    require_reason=True,
                    reason="annotations.destructiveHint",
                )
            if descriptor.annotations.openWorldHint is True:
                return MCPPolicyDecision(
                    auto_approve=False, tier="B", reason="annotations.openWorldHint"
                )

        # 第三层: 名字前缀启发式
        if descriptor.name.startswith(READ_PREFIXES):
            return MCPPolicyDecision(
                auto_approve=True, tier="A", reason="prefix:read"
            )
        if descriptor.name.startswith(WRITE_PREFIXES):
            return MCPPolicyDecision(
                auto_approve=False,
                tier="B",
                require_reason=True,
                reason="prefix:write",
            )

        # 未匹配 → 保守 Tier-B
        return MCPPolicyDecision(
            auto_approve=False,
            tier="B",
            reason="prefix:unknown:conservative",
        )
```

- [ ] **Step 5: 运行测试验证通过**

Run: `cd cognitiveplane && python -m pytest tests/test_adapters/test_mcp/test_tool_policy_classifier.py -v`
Expected: PASS (all tests — 3 YAML + 4 annotations + 13 prefix + 1 unknown + 1 malformed)

- [ ] **Step 6: Commit**

```bash
git add cognitiveplane/adapters/mcp/tool_policy_classifier.py \
        cognitiveplane/governance/mcp_policy.yaml \
        cognitiveplane/tests/test_adapters/test_mcp/test_tool_policy_classifier.py
git commit -m "feat(mcp): add MCPToolPolicyClassifier — three-layer policy decision"
```

---

## Task 7: ToolRegistry.unregister 方法

**Files:**
- Modify: `cognitiveplane/control/tool_registry.py`
- Test: `cognitiveplane/tests/test_control/test_tool_registry.py`

- [ ] **Step 1: 写失败测试 — unregister 移除工具 + 静默处理不存在工具**

Append to `cognitiveplane/tests/test_control/test_tool_registry.py`:

```python
def test_unregister_removes_tool():
    """Spec §3.3 — list_changed 下线工具时调用 unregister 移除。"""
    from cognitiveplane.control.tools import BrainTool, ToolResult

    class FakeTool(BrainTool):
        @property
        def name(self):
            return "fake_tool"

        @property
        def description(self):
            return "d"

        @property
        def parameters_schema(self):
            return {}

        async def execute(self, **kwargs):
            return ToolResult()

    registry = ToolRegistry()
    registry.register(FakeTool())

    assert "fake_tool" in [t.name for t in registry._tools.values()]
    registry.unregister("fake_tool")
    assert "fake_tool" not in registry._tools


def test_unregister_nonexistent_silent():
    """Spec §5 — unregister 不存在的工具静默返回，不抛异常。"""
    registry = ToolRegistry()
    # Should not raise
    registry.unregister("nonexistent_tool")
    assert "nonexistent_tool" not in registry._tools
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd cognitiveplane && python -m pytest tests/test_control/test_tool_registry.py::test_unregister_removes_tool tests/test_control/test_tool_registry.py::test_unregister_nonexistent_silent -v`
Expected: FAIL — `AttributeError: 'ToolRegistry' object has no attribute 'unregister'`

- [ ] **Step 3: 实现 unregister — 追加到 ToolRegistry 类**

在 `cognitiveplane/control/tool_registry.py` 的 `ToolRegistry` 类中，紧接 `register` 方法后追加：

```python
    def unregister(self, name: str) -> None:
        """移除工具 — list_changed 下线时调用。

        Spec §5 — 不存在的工具静默返回，不抛异常（list_changed diff
        可能与当前注册状态有偏差）。
        """
        self._tools.pop(name, None)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd cognitiveplane && python -m pytest tests/test_control/test_tool_registry.py::test_unregister_removes_tool tests/test_control/test_tool_registry.py::test_unregister_nonexistent_silent -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add cognitiveplane/control/tool_registry.py \
        cognitiveplane/tests/test_control/test_tool_registry.py
git commit -m "feat(control): add ToolRegistry.unregister for MCP list_changed removals"
```

---

## Task 8: MCPRegistry — register_server + register_all

**Files:**
- Create: `cognitiveplane/control/mcp_registry.py`
- Test: `cognitiveplane/tests/test_adapters/test_mcp/test_mcp_registry.py`

- [ ] **Step 1: 写失败测试 — 注册 server 后跑判定 + 注册 adapter 到 ToolRegistry**

Create `cognitiveplane/tests/test_adapters/test_mcp/test_mcp_registry.py`:

```python
"""Test MCPRegistry — 发现 + 三层判定 + 注册到 ToolRegistry.

Spec §3.1 (启动时数据流) + §3.3 (list_changed 事件流).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cognitiveplane.adapters.mcp.base import (
    MCPAnnotations,
    MCPServer,
    ToolDescriptor,
)
from cognitiveplane.adapters.mcp.in_process import InProcessClient
from cognitiveplane.adapters.mcp.tool_policy_classifier import (
    MCPToolPolicyClassifier,
)
from cognitiveplane.control.event_log import EventLog
from cognitiveplane.control.mcp_registry import MCPRegistry
from cognitiveplane.control.tool_registry import ToolRegistry
from cognitiveplane.shared.types import CaseId


def _make_echo_server() -> MCPServer:
    desc = ToolDescriptor(
        name="echo_tool",
        description="Echo input back",
        parameters_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )

    async def echo_handler(text: str) -> dict:
        return {"echo": text}

    client = InProcessClient(tools=[desc], handlers={"echo_tool": echo_handler})
    return MCPServer(client=client, name="echo_server")


def _make_registry() -> MCPRegistry:
    classifier = MCPToolPolicyClassifier(yaml_path=None)
    event_log = EventLog(case_id=CaseId(value="test"))
    return MCPRegistry(classifier=classifier, event_log=event_log)


@pytest.mark.asyncio
async def test_register_server_pulls_tools_and_caches():
    """Spec §3.1 step 5 — register_server 拉工具列表 + 缓存判定结果。"""
    registry = _make_registry()
    server = _make_echo_server()

    await registry.register_server(server)

    cached = registry.list_cached_tools()
    assert len(cached) == 1
    assert cached[0][0].name == "echo_tool"
    # echo_tool 无 annotations, 不匹配 read/write 前缀 → 保守 Tier-B
    assert cached[0][1].reason == "prefix:unknown:conservative"


@pytest.mark.asyncio
async def test_register_all_creates_adapters_in_tool_registry():
    """Spec §3.1 step 6 — register_all 把 MCPAdapter 注册到 ToolRegistry。"""
    registry = _make_registry()
    server = _make_echo_server()
    await registry.register_server(server)

    tool_registry = ToolRegistry()
    await registry.register_all(tool_registry)

    definitions = tool_registry.get_llm_tool_definitions()
    names = [d["function"]["name"] for d in definitions]
    assert "echo_tool" in names


@pytest.mark.asyncio
async def test_register_all_adapter_is_callable_through_tool_registry():
    """端到端片段 — ToolRegistry 能调到 MCPAdapter。"""
    registry = _make_registry()
    server = _make_echo_server()
    await registry.register_server(server)

    tool_registry = ToolRegistry()
    await registry.register_all(tool_registry)

    result = await tool_registry._tools["echo_tool"].execute(text="hello")

    assert result.error is None
    assert result.output == {"echo": "hello"}


@pytest.mark.asyncio
async def test_register_multiple_servers():
    """多个 server 都能注册到同一个 ToolRegistry。"""
    registry = _make_registry()

    # server 1: echo
    echo_server = _make_echo_server()
    await registry.register_server(echo_server)

    # server 2: ping
    ping_desc = ToolDescriptor(
        name="ping_tool",
        description="Returns pong",
        parameters_schema={},
    )

    async def ping_handler() -> dict:
        return {"pong": True}

    ping_client = InProcessClient(tools=[ping_desc], handlers={"ping_tool": ping_handler})
    ping_server = MCPServer(client=ping_client, name="ping_server")
    await registry.register_server(ping_server)

    tool_registry = ToolRegistry()
    await registry.register_all(tool_registry)

    names = [d["function"]["name"] for d in tool_registry.get_llm_tool_definitions()]
    assert "echo_tool" in names
    assert "ping_tool" in names


@pytest.mark.asyncio
async def test_list_changed_adds_new_tool():
    """Spec §3.3 — list_changed 后新增工具出现在 ToolRegistry。"""
    registry = _make_registry()
    server = _make_echo_server()
    await registry.register_server(server)

    tool_registry = ToolRegistry()
    await registry.register_all(tool_registry)

    assert "ping_tool" not in [d["function"]["name"] for d in tool_registry.get_llm_tool_definitions()]

    # 模拟 server 端工具列表变更：替换 client 的 tools + handlers，触发 list_changed
    in_process_client = server._client  # type: ignore[attr-defined]
    ping_desc = ToolDescriptor(name="ping_tool", description="p", parameters_schema={})

    async def ping_handler() -> dict:
        return {"pong": True}

    in_process_client._tools = [ping_desc]  # type: ignore[attr-defined]
    in_process_client._handlers = {"ping_tool": ping_handler}  # type: ignore[attr-defined]
    await in_process_client.fire_list_changed()

    names = [d["function"]["name"] for d in tool_registry.get_llm_tool_definitions()]
    assert "ping_tool" in names
    # echo_tool 在新工具列表里没了 → 下线
    assert "echo_tool" not in names


@pytest.mark.asyncio
async def test_list_changed_removes_offline_tool():
    """Spec §3.3 — list_changed 后下线工具从 ToolRegistry 移除。"""
    registry = _make_registry()
    server = _make_echo_server()
    await registry.register_server(server)

    tool_registry = ToolRegistry()
    await registry.register_all(tool_registry)
    assert "echo_tool" in [d["function"]["name"] for d in tool_registry.get_llm_tool_definitions()]

    # 清空 server 的工具列表
    in_process_client = server._client  # type: ignore[attr-defined]
    in_process_client._tools = []  # type: ignore[attr-defined]
    in_process_client._handlers = {}  # type: ignore[attr-defined]
    await in_process_client.fire_list_changed()

    assert "echo_tool" not in [d["function"]["name"] for d in tool_registry.get_llm_tool_definitions()]
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd cognitiveplane && python -m pytest tests/test_adapters/test_mcp/test_mcp_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cognitiveplane.control.mcp_registry'`

- [ ] **Step 3: 实现 MCPRegistry**

Create `cognitiveplane/control/mcp_registry.py`:

```python
"""MCPRegistry — 发现 + 三层 ToolPolicy 判定 + 注册到 ToolRegistry.

Spec §2.4 + §3.1 + §3.3 — 薄桥接，不持有工具实现，只持有 MCPServer
引用和判定缓存。list_changed 事件触发重判 + 更新 ToolRegistry。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from cognitiveplane.adapters.mcp.base import (
    MCPAdapter,
    MCPPolicyDecision,
    MCPServer,
    ToolDescriptor,
)
from cognitiveplane.adapters.mcp.tool_policy_classifier import (
    MCPToolPolicyClassifier,
)
from cognitiveplane.control.event_log import BrainEventType, EventLog

logger = logging.getLogger(__name__)


@dataclass
class _CachedEntry:
    """一个 server 的工具列表 + 判定结果缓存。"""
    server_name: str
    descriptors: list[ToolDescriptor]
    policies: dict[str, MCPPolicyDecision]  # tool_name → decision


class MCPRegistry:
    """MCP 工具发现 + 三层判定 + 注册到 ToolRegistry。

    使用:
        registry = MCPRegistry(classifier=..., event_log=...)
        await registry.register_server(server1)
        await registry.register_server(server2)
        await registry.register_all(tool_registry)
    """

    def __init__(
        self,
        classifier: MCPToolPolicyClassifier,
        event_log: EventLog | None = None,
    ) -> None:
        self._classifier = classifier
        self._event_log = event_log
        # server_name → (MCPServer, _CachedEntry)
        self._servers: dict[str, tuple[MCPServer, _CachedEntry]] = {}
        # tool_registry 引用 — register_all 时设置，list_changed 时用
        self._tool_registry: "ToolRegistry | None" = None  # type: ignore[name-defined]

    async def register_server(self, server: MCPServer) -> None:
        """注册一个 MCPServer — 拉取工具列表、跑三层判定、缓存。

        Spec §3.1 step 5.
        """
        try:
            descriptors = await server.list_tools()
        except Exception as e:
            logger.warning("MCP server %s list_tools failed: %s — skipping", server.name, e)
            if self._event_log is not None:
                self._event_log.emit(
                    BrainEventType.TOOL_RESULT,
                    source="mcp_registry",
                    data={
                        "event": "list_tools_failed",
                        "server": server.name,
                        "error": str(e),
                    },
                )
            return

        policies: dict[str, MCPPolicyDecision] = {}
        for desc in descriptors:
            try:
                policies[desc.name] = self._classifier.classify(desc)
            except Exception as e:
                logger.warning(
                    "MCP tool %s policy classify failed: %s — fallback conservative",
                    desc.name,
                    e,
                )
                policies[desc.name] = MCPPolicyDecision(
                    auto_approve=False, tier="B", reason="classify_error:conservative"
                )

        self._servers[server.name] = (
            server,
            _CachedEntry(
                server_name=server.name,
                descriptors=descriptors,
                policies=policies,
            ),
        )

        # 订阅 list_changed
        server.on_list_changed(lambda s=server: self._handle_list_changed(s))

        if self._event_log is not None:
            for desc, policy in zip(descriptors, [policies[d.name] for d in descriptors]):
                self._event_log.emit(
                    BrainEventType.TOOL_CALL,
                    source="mcp_registry",
                    data={
                        "event": "tool_registered",
                        "server": server.name,
                        "tool": desc.name,
                        "policy": {
                            "auto_approve": policy.auto_approve,
                            "tier": policy.tier,
                            "require_reason": policy.require_reason,
                            "reason": policy.reason,
                        },
                    },
                )

    def list_cached_tools(self) -> list[tuple[ToolDescriptor, MCPPolicyDecision]]:
        """返回所有已注册 server 的 (descriptor, policy) 列表 — 测试用。"""
        result: list[tuple[ToolDescriptor, MCPPolicyDecision]] = []
        for _, entry in self._servers.values():
            for desc in entry.descriptors:
                result.append((desc, entry.policies[desc.name]))
        return result

    async def register_all(self, tool_registry: "ToolRegistry") -> None:  # type: ignore[name-defined]
        """把所有已注册 server 的工具注册到 ToolRegistry。

        Spec §3.1 step 6 — 为每个 (descriptor, policy) 创建 MCPAdapter
        并注册到 ToolRegistry。
        """
        self._tool_registry = tool_registry
        for server, entry in self._servers.values():
            for desc in entry.descriptors:
                policy = entry.policies[desc.name]
                adapter = MCPAdapter(server=server, descriptor=desc, policy=policy)
                tool_registry.register(adapter)

    async def _handle_list_changed(self, server: MCPServer) -> None:
        """list_changed 事件 — 重新拉工具列表、diff、重判、更新 ToolRegistry。

        Spec §3.3 + §5 — 重载失败时保留旧工具列表不动。
        """
        try:
            new_descriptors = await server.list_tools()
        except Exception as e:
            logger.warning(
                "MCP server %s list_changed re-fetch failed: %s — keeping old tools",
                server.name,
                e,
            )
            if self._event_log is not None:
                self._event_log.emit(
                    BrainEventType.TOOL_RESULT,
                    source="mcp_registry",
                    data={
                        "event": "list_changed_refetch_failed",
                        "server": server.name,
                        "error": str(e),
                    },
                )
            return

        old_entry = self._servers.get(server.name)
        old_names = {d.name for d in old_entry.descriptors} if old_entry else set()
        new_names = {d.name for d in new_descriptors}

        added = new_names - old_names
        removed = old_names - new_names
        # changed = same name but different descriptor (compare by tuple equality)
        changed = {
            d.name
            for d in new_descriptors
            if d.name in old_names
            and d != next(od for od in (old_entry.descriptors if old_entry else []) if od.name == d.name)
        }

        # 重判 + 更新缓存
        new_policies: dict[str, MCPPolicyDecision] = {}
        for desc in new_descriptors:
            new_policies[desc.name] = self._classifier.classify(desc)

        self._servers[server.name] = (
            server,
            _CachedEntry(
                server_name=server.name,
                descriptors=new_descriptors,
                policies=new_policies,
            ),
        )

        # 更新 ToolRegistry
        if self._tool_registry is not None:
            for name in removed:
                self._tool_registry.unregister(name)
            for desc in new_descriptors:
                if desc.name in added or desc.name in changed:
                    policy = new_policies[desc.name]
                    adapter = MCPAdapter(server=server, descriptor=desc, policy=policy)
                    self._tool_registry.register(adapter)  # register 覆盖同名

        if self._event_log is not None:
            self._event_log.emit(
                BrainEventType.TOOL_CALL,
                source="mcp_registry",
                data={
                    "event": "list_changed_processed",
                    "server": server.name,
                    "added": sorted(added),
                    "removed": sorted(removed),
                    "changed": sorted(changed),
                },
            )
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd cognitiveplane && python -m pytest tests/test_adapters/test_mcp/test_mcp_registry.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add cognitiveplane/control/mcp_registry.py \
        cognitiveplane/tests/test_adapters/test_mcp/test_mcp_registry.py
git commit -m "feat(mcp): add MCPRegistry — discovery + policy + list_changed handling"
```

---

## Task 9: ControlDeps.mcp_registry 字段解锁

**Files:**
- Modify: `cognitiveplane/control/deps.py`
- Test: `cognitiveplane/tests/test_control/test_deps.py`

- [ ] **Step 1: 写失败测试 — ControlDeps.mcp_registry 是真实字段**

Append to `cognitiveplane/tests/test_control/test_deps.py`:

```python
def test_control_deps_has_mcp_registry_field():
    """Spec §7.2 — ControlDeps.mcp_registry 从注释占位变为真实字段。"""
    from cognitiveplane.control.deps import ControlDeps

    deps = ControlDeps()
    assert deps.mcp_registry is None  # default None


def test_control_deps_mcp_registry_assignable():
    """可注入 MCPRegistry 实例。"""
    from cognitiveplane.control.deps import ControlDeps
    from cognitiveplane.adapters.mcp.tool_policy_classifier import (
        MCPToolPolicyClassifier,
    )
    from cognitiveplane.control.mcp_registry import MCPRegistry

    classifier = MCPToolPolicyClassifier(yaml_path=None)
    registry = MCPRegistry(classifier=classifier, event_log=None)
    deps = ControlDeps(mcp_registry=registry)

    assert deps.mcp_registry is registry
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd cognitiveplane && python -m pytest tests/test_control/test_deps.py::test_control_deps_has_mcp_registry_field tests/test_control/test_deps.py::test_control_deps_mcp_registry_assignable -v`
Expected: FAIL — `AttributeError: 'ControlDeps' object has no attribute 'mcp_registry'`

- [ ] **Step 3: 解锁 ControlDeps.mcp_registry 字段**

在 `cognitiveplane/control/deps.py` 的 `ControlDeps` 类中，把 `# Phase 3: mcp_registry` 注释替换为真实字段。同时在 `TYPE_CHECKING` 块中加 MCPRegistry import。

Edit `cognitiveplane/control/deps.py` — 找到 `ControlDeps` 类定义：

```python
@dataclass
class ControlDeps:
    """L1 Control plane dependencies — orchestrator and decision repository."""
    orchestrator: BrainOrchestrator | None = None
    decision_repo: BrainDecisionRepository | None = None
    # Phase 1c: tool_policy: ToolPolicy | None = None
    # Phase 1c: hooks: list[BeforeToolHook] = field(default_factory=list)
    # Phase 2a: react_graph: CompiledStateGraph | None = None
    # Phase 3: mcp_registry: MCPRegistry | None = None  (plan §7 line 2338)
```

替换为：

```python
@dataclass
class ControlDeps:
    """L1 Control plane dependencies — orchestrator and decision repository."""
    orchestrator: BrainOrchestrator | None = None
    decision_repo: BrainDecisionRepository | None = None
    mcp_registry: "MCPRegistry | None" = None  # Phase 3 解锁 (plan §7 line 2338)
    # Phase 1c: tool_policy: ToolPolicy | None = None
    # Phase 1c: hooks: list[BeforeToolHook] = field(default_factory=list)
    # Phase 2a: react_graph: CompiledStateGraph | None = None
```

在 `TYPE_CHECKING` 块中加 import（紧接现有 control 模块 import 之后）：

```python
if TYPE_CHECKING:
    from cognitiveplane.capability.provider import LLMProvider
    from cognitiveplane.capability.web_search import WebSearchProvider
    from cognitiveplane.shared.ports.knowledge import (
        RAGQueryPort,
        StandardsQueryPort,
        CaseLibraryQueryPort,
        ProcessKnowledgePort,
    )
    from cognitiveplane.shared.ports.memory import (
        MemorySearchPort,
        MemoryReadPort,
        MemoryWritePort,
        MemoryPromotionPort,
        MemoryArchivePort,
        MemoryConfidencePort,
    )
    from cognitiveplane.shared.ports.gateway import (
        GatewayReadPort,
        GatewayWritePort,
    )
    from cognitiveplane.shared.ports.validation import ValidationPipelinePort
    from cognitiveplane.shared.ports.collaboration import HumanReviewRequestRepository
    from cognitiveplane.shared.ports.learning import LearningEventRepository
    from cognitiveplane.control.ports import BrainDecisionRepository
    from cognitiveplane.control.orchestrator import BrainOrchestrator
    from cognitiveplane.control.mcp_registry import MCPRegistry  # 新增
    from cognitiveplane.governance.escalation import EscalationTracker
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd cognitiveplane && python -m pytest tests/test_control/test_deps.py::test_control_deps_has_mcp_registry_field tests/test_control/test_deps.py::test_control_deps_mcp_registry_assignable -v`
Expected: PASS (2 tests)

- [ ] **Step 5: 跑全量 deps 测试确保未破坏现有行为**

Run: `cd cognitiveplane && python -m pytest tests/test_control/test_deps.py -v`
Expected: PASS (all existing + 2 new)

- [ ] **Step 6: Commit**

```bash
git add cognitiveplane/control/deps.py \
        cognitiveplane/tests/test_control/test_deps.py
git commit -m "feat(control): unlock ControlDeps.mcp_registry field (Phase 3)"
```

---

## Task 10: echo_server stub

**Files:**
- Create: `cognitiveplane/adapters/mcp/stubs/echo_server.py`
- Test: `cognitiveplane/tests/test_adapters/test_mcp/test_echo_server.py`

- [ ] **Step 1: 写失败测试 — echo_server 工厂返回 MCPServer，echo_tool 可调**

Create `cognitiveplane/tests/test_adapters/test_mcp/test_echo_server.py`:

```python
"""Test echo_server stub — trivial MCP server for pipeline validation.

Spec §1.2 — C 子项目交付一个 trivial stub 验证管道，不含业务语义。
"""
from __future__ import annotations

import pytest

from cognitiveplane.adapters.mcp.base import MCPServer
from cognitiveplane.adapters.mcp.stubs.echo_server import create_echo_server


@pytest.mark.asyncio
async def test_create_echo_server_returns_mcpserver():
    server = create_echo_server()
    assert isinstance(server, MCPServer)
    assert server.name == "echo_server"


@pytest.mark.asyncio
async def test_echo_server_lists_echo_tool():
    server = create_echo_server()
    tools = await server.list_tools()

    assert len(tools) == 1
    assert tools[0].name == "echo_tool"
    assert "echo" in tools[0].description.lower()
    assert tools[0].parameters_schema["type"] == "object"


@pytest.mark.asyncio
async def test_echo_server_call_tool_returns_input_text():
    server = create_echo_server()
    result = await server.call_tool("echo_tool", {"text": "hello world"})

    assert result == {"echo": "hello world"}


@pytest.mark.asyncio
async def test_echo_server_call_tool_with_empty_text():
    server = create_echo_server()
    result = await server.call_tool("echo_tool", {"text": ""})

    assert result == {"echo": ""}
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd cognitiveplane && python -m pytest tests/test_adapters/test_mcp/test_echo_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cognitiveplane.adapters.mcp.stubs.echo_server'`

- [ ] **Step 3: 实现 echo_server stub**

Create `cognitiveplane/adapters/mcp/stubs/echo_server.py`:

```python
"""echo_server — trivial MCP server stub for pipeline validation.

Spec §1.2 — C 子项目交付物。仅用于验证 MCPClient → MCPServer →
MCPAdapter → MCPRegistry → ToolRegistry → ReActEngine 整条管道。
不含业务语义，未来真实认知 MCP（文档检索等）按需接入。
"""

from __future__ import annotations

from cognitiveplane.adapters.mcp.base import ToolDescriptor
from cognitiveplane.adapters.mcp.in_process import InProcessClient, MCPServer

_ECHO_TOOL_DESCRIPTOR = ToolDescriptor(
    name="echo_tool",
    description="Echoes the input text back. Trivial stub for pipeline validation.",
    parameters_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to echo back"},
        },
        "required": ["text"],
    },
    # 无 annotations — 三层判定落入保守 Tier-B (spec §4.3)
)


async def _echo_handler(text: str) -> dict:
    return {"echo": text}


def create_echo_server() -> MCPServer:
    """构造 echo_server MCPServer 实例。"""
    client = InProcessClient(
        tools=[_ECHO_TOOL_DESCRIPTOR],
        handlers={"echo_tool": _echo_handler},
    )
    return MCPServer(client=client, name="echo_server")
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd cognitiveplane && python -m pytest tests/test_adapters/test_mcp/test_echo_server.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add cognitiveplane/adapters/mcp/stubs/echo_server.py \
        cognitiveplane/tests/test_adapters/test_mcp/test_echo_server.py
git commit -m "feat(mcp): add echo_server trivial stub for pipeline validation"
```

---

## Task 11: E2E 测试 — echo_server → ReActEngine

**Files:**
- Test: `cognitiveplane/tests/test_adapters/test_mcp/test_echo_server_e2e.py`

- [ ] **Step 1: 检查 mock provider 接口**

Run: `cd cognitiveplane && grep -n "class\|async def\|def " capability/mock.py | head -30`

Expected: 看到 `MockLLMProvider` 类 + `complete` 方法签名。如签名与下方测试假设不符，调整测试中的调用方式。

- [ ] **Step 2: 写 E2E 测试 — mock LLM 触发 echo_tool 调用 + 验证 tool_result 回流**

Create `cognitiveplane/tests/test_adapters/test_mcp/test_echo_server_e2e.py`:

```python
"""E2E test — echo_server → Registry → ToolRegistry → ReActEngine → mock LLM → tool_call → result.

Spec §6.1 C.4 — 验证整条 MCP 管道端到端跑通。

测试策略:
1. 用 MockLLMProvider 让它强制返回 echo_tool 的 tool_call
2. ReActEngine 调 ToolRegistry 执行 echo_tool
3. MCPAdapter 委托 echo_server, 返回 {"echo": "hello"}
4. tool_result 注入下一轮 LLM 消息
5. 第二轮 mock LLM 返回最终答复，验证它看到了 tool_result
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from cognitiveplane.adapters.mcp.stubs.echo_server import create_echo_server
from cognitiveplane.adapters.mcp.tool_policy_classifier import (
    MCPToolPolicyClassifier,
)
from cognitiveplane.capability.mock import MockLLMProvider
from cognitiveplane.control.deps import (
    CapabilityDeps,
    CognitiveDependencies,
    ControlDeps,
    GatewayDeps,
    GovernanceDeps,
    KnowledgeDeps,
    MemoryDeps,
)
from cognitiveplane.control.event_log import EventLog
from cognitiveplane.control.hooks import SafetyHook
from cognitiveplane.control.mcp_registry import MCPRegistry
from cognitiveplane.control.react import ReActEngine
from cognitiveplane.control.tool_registry import ToolRegistry
from cognitiveplane.shared.types import CaseId


class EchoToolCallMockProvider(MockLLMProvider):
    """Mock LLM — 第一轮强制 echo_tool 调用，第二轮基于 tool_result 给最终答复。"""

    def __init__(self) -> None:
        self._call_count = 0
        self._last_tool_result: dict[str, Any] | None = None

    async def complete(self, request, **kwargs):
        from cognitiveplane.capability.provider import LLMResponse

        self._call_count += 1
        if self._call_count == 1:
            # 第一轮 — 返回 echo_tool 的 tool_call
            return LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "echo_tool",
                            "arguments": json.dumps({"text": "hello from e2e"}),
                        },
                    }
                ],
                model_used="mock-echo",
            )
        # 第二轮 — 看到 tool_result，给最终答复
        # request.messages 应包含 tool 角色的消息
        tool_messages = [m for m in request.messages if m.get("role") == "tool"]
        if tool_messages:
            self._last_tool_result = json.loads(tool_messages[0].get("content", "{}"))
        return LLMResponse(
            content=f"Echo result: {self._last_tool_result}",
            tool_calls=None,
            model_used="mock-echo",
        )


@pytest.mark.asyncio
async def test_echo_server_e2e_pipeline():
    """C.4 验收 — 整条管道端到端跑通。"""
    # 1. 构造 mock LLM
    llm = EchoToolCallMockProvider()

    # 2. 构造 MCPRegistry + 注册 echo_server
    classifier = MCPToolPolicyClassifier(yaml_path=None)
    event_log = EventLog(case_id=CaseId(value="e2e-test"))
    registry = MCPRegistry(classifier=classifier, event_log=event_log)
    echo_server = create_echo_server()
    await registry.register_server(echo_server)

    # 3. 构造 ToolRegistry + 注册 MCP 工具
    tool_registry = ToolRegistry()
    await registry.register_all(tool_registry)

    # 4. 验证 echo_tool 出现在 LLM 可见的工具列表
    definitions = tool_registry.get_llm_tool_definitions()
    tool_names = [d["function"]["name"] for d in definitions]
    assert "echo_tool" in tool_names, f"echo_tool not in {tool_names}"

    # 5. 构造 deps + ReActEngine
    deps = CognitiveDependencies(
        capability=CapabilityDeps(llm_provider=llm),
        control=ControlDeps(mcp_registry=registry),
        knowledge=KnowledgeDeps(),
        memory=MemoryDeps(),
        gateway=GatewayDeps(),
        governance=GovernanceDeps(),
    )
    engine = ReActEngine(
        deps,
        hooks=[SafetyHook()],
        event_log=event_log,
        tool_registry_override=tool_registry,
    )

    # 6. 跑 ReAct
    response = await engine.run(
        user_input="Please echo 'hello from e2e'",
        context=None,  # mock LLM 不读 context
        session={"session_id": "e2e-session"},
    )

    # 7. 验证最终答复包含 tool_result
    assert "hello from e2e" in response.text_reply, (
        f"Expected 'hello from e2e' in reply, got: {response.text_reply}"
    )
    assert "echo_tool" in response.tools_used, (
        f"Expected echo_tool in tools_used, got: {response.tools_used}"
    )
```

- [ ] **Step 3: 检查 ReActEngine 构造签名 — 是否接受 tool_registry_override**

Run: `cd cognitiveplane && grep -n "def __init__\|tool_registry" cognitiveplane/control/react.py | head -15`

如果 ReActEngine 没有 `tool_registry_override` 参数，需要：
- 选项 A: 修改 ReActEngine 增加 `tool_registry_override` 参数（推荐 — 测试需要）
- 选项 B: 调整测试，通过 deps 注入 ToolRegistry

**选项 A 步骤** — 修改 `cognitiveplane/control/react.py` 的 `ReActEngine.__init__`，在参数列表末尾加 `tool_registry_override: ToolRegistry | None = None`，并在构造函数体内若非 None 则用它替代内部构造的 ToolRegistry。

- [ ] **Step 4: 运行 E2E 测试**

Run: `cd cognitiveplane && python -m pytest tests/test_adapters/test_mcp/test_echo_server_e2e.py -v -s`
Expected: PASS — 测试通过，最终答复包含 "hello from e2e"。

如失败，常见原因：
- `MockLLMProvider` 基类签名不匹配 → 调整 `EchoToolCallMockProvider` 继承
- `ReActEngine.run` 参数不匹配 → 调整测试调用
- `request.messages` 字段名不同 → 调整 mock provider 读取逻辑

- [ ] **Step 5: Commit**

```bash
git add cognitiveplane/tests/test_adapters/test_mcp/test_echo_server_e2e.py \
        cognitiveplane/control/react.py  # 如修改了 ReActEngine
git commit -m "test(mcp): C.4 E2E — echo_server pipeline through ReActEngine"
```

---

## Task 12: 全量回归 + Phase discipline 检查

**Files:**
- Run: full test suite
- Run: `scripts/check_phase_discipline.py`

- [ ] **Step 1: 跑全量 MCP 测试**

Run: `cd cognitiveplane && python -m pytest tests/test_adapters/test_mcp/ -v`
Expected: PASS — 所有 MCP 测试通过（约 35 个）

- [ ] **Step 2: 跑全量 control 测试确保未破坏现有行为**

Run: `cd cognitiveplane && python -m pytest tests/test_control/ -v`
Expected: PASS — 所有现有 control 测试仍通过

- [ ] **Step 3: 跑 phase discipline 检查**

Run: `cd /Users/liuyixuan/PycharmProjects/WeldEvent && python scripts/check_phase_discipline.py`
Expected: PASS — C 子项目不引入 Phase 3+ 禁用项（验收 C.5）

如失败，检查 diff 中是否混入：
- `agent_loop.py` — 应在子项目 B
- `request_detection` / `request_annotation` — 应在子项目 D（已移出认知平面）
- `switch_role` / `request_image_detail` — Phase 5+
- 真实工业 MCP server 实现 — 应在 executionplane 仓库

- [ ] **Step 4: 跑全量测试**

Run: `cd cognitiveplane && python -m pytest -x`
Expected: PASS — 全量测试通过，无回归

- [ ] **Step 5: Commit（如有任何修复）**

```bash
git add -A
git commit -m "test(mcp): C 子项目全量回归通过 — C.1-C.5 验收完成"
```

---

## Self-Review

### 1. Spec 覆盖

| Spec 节 | 覆盖 Task |
|---|---|
| §2.2 四个核心类（MCPClient/MCPServer/MCPAdapter/MCPRegistry）| T2/T3/T4/T8 |
| §2.3 数据结构（ToolDescriptor/MCPAnnotations/MCPPolicyDecision）| T1 |
| §2.4 类接口签名 | T2/T3/T4/T8 |
| §3.1 启动时数据流 | T8（register_server/register_all）|
| §3.2 运行时数据流 | T11（E2E）|
| §3.3 list_changed 事件流 | T8（test_list_changed_adds/removes）|
| §4 三层 ToolPolicy 判定 | T6 |
| §4.1 判定顺序（YAML > annotations > 前缀）| T6（3 个 YAML 测试 + 4 annotations + 13 prefix）|
| §4.3 echo_stub 判定结果（保守 Tier-B）| T6（test_unknown_prefix_conservative_tier_b）|
| §4.4 mcp_policy.yaml 初始内容 | T6 Step 1 |
| §5 错误处理 | T4（call_tool 失败）+ T6（YAML 格式错）+ T8（list_tools 失败 + list_changed 重载失败）|
| §6.1 C.1（MCPAdapter 是 BrainTool）| T4 test_adapter_is_braintool |
| §6.1 C.2（三层判定全覆盖）| T6 |
| §6.1 C.3（list_changed 管道就位）| T8 |
| §6.1 C.4（echo_server 端到端）| T11 |
| §6.1 C.5（不引入 Phase 3+ 禁用项）| T12 Step 3 |
| §7.1 新增文件 | T1/T5/T6/T8/T10 覆盖所有新增文件 |
| §7.2 改动文件（deps.py + tool_registry.py）| T9 + T7 |
| §7.3 不改文件 | 验证通过 — plan 未修改 react.py 主体逻辑（仅 Task 11 可能加 override 参数）、hooks.py、tool_policy.py |

### 2. Placeholder 扫描

无 TBD/TODO。所有代码块完整。Task 11 Step 3 有条件分支（选项 A/B）但已给出选项 A 的具体步骤。

### 3. 类型一致性

- `ToolDescriptor` / `MCPAnnotations` / `MCPPolicyDecision` — T1 定义，T4/T5/T6/T8/T10/T11 全部使用相同字段名
- `MCPClient.list_tools() -> list[ToolDescriptor]` — T2 定义，T3/T5/T8 一致
- `MCPClient.call_tool(name, arguments) -> dict` — T2 定义，T3/T5/T8 一致
- `MCPClient.subscribe_list_changed(callback)` — T2 定义，T3/T5 一致
- `MCPServer(client, name)` 构造签名 — T3 定义，T5/T8/T10 一致
- `MCPAdapter(server, descriptor, policy)` 构造签名 — T4 定义，T8 一致
- `MCPAdapter.policy` 属性 — T4 定义，T8 测试中未直接读 policy（通过 list_cached_tools 间接验证）
- `MCPRegistry(classifier, event_log)` 构造签名 — T8 定义，T9/T11 一致
- `MCPRegistry.register_server(server)` / `register_all(tool_registry)` — T8 定义，T11 一致
- `MCPToolPolicyClassifier(yaml_path)` 构造签名 — T6 定义，T8/T9/T11 一致
- `InProcessClient(tools, handlers)` 构造签名 — T5 定义，T8/T10 一致
- `ToolRegistry.unregister(name)` — T7 定义，T8 一致

无类型/签名漂移。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-25-phase3-c-mcp-infrastructure.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?