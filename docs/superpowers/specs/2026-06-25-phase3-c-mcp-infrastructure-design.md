# Phase 3 子项目 C — MCP 基础设施设计

**日期**: 2026-06-25
**阶段**: Phase 3（实时人机反馈 + 检测/标注 MCP 工具）
**子项目**: C — MCP 基础设施（认知平面内）
**前置**: Phase 2 验收通过（commit 802f455），§10 设计决策表已对齐代码现状

---

## 1. 背景与范围

### 1.1 L1/L3 MCP 分层决策

Phase 3 启动前对 §13 工具三层体系做了一个重要修正：**MCP 按 L1 认知内容 vs L3 工业执行分到不同平面，不共享实现**。

| MCP 类别 | 所属平面 | 例子 | 谁调用 |
|---|---|---|---|
| 认知内容 MCP | L1 cognitiveplane（本仓库）| web search、文档检索、知识图谱 | L1 ReAct LLM |
| 工业执行 MCP | L3 executionplane（另一仓库）| Label Studio、检测模型 API、CAD、MES、ERP | L2 activity pool / Temporal workflow |

这修正了 §13 当前"L1 LLM 与 L3 Activity 共享同一份 MCP 实现"的设计。理由：L1 和 L3 关心的 MCP 类型根本不同 — L1 关心认知内容，L3 关心工业执行。共享实现会把两类语义不同的工具塞进同一个 `adapters/mcp/` 目录，违反单一职责。

**对本子项目的影响**：
- C 只建**认知平面内**的 MCP 基础设施
- 原计划子项目 D（detect_defects + annotate_label MCP server）**移出认知平面 Phase 3**，迁到 executionplane 仓库
- Phase 3 认知平面范围变为 4 个子项目（A WebSocket / B AgentLoop / C MCP 基础设施 / E 标注 UI + 反馈闭环）
- §11.4 验收 3.3 / 3.4 需在 Phase 3 后续子项目设计时重写为"LLM 调认知平面 request_detection / request_annotation 代理 Tool，经 Gateway 出门"—— 这不在 C 子项目范围内

### 1.2 C 子项目交付边界

C 只交付 MCP 抽象基础设施 + 一个 trivial stub server 验证管道。不含任何真实认知 MCP（按需接入），也不含工业 MCP（移到 executionplane）。

| 在范围内 | 不在范围内 |
|---|---|
| MCPClient ABC + InProcessClient 实现 | StdioClient / SSEClient（Phase 5+）|
| MCPServer（一个 server 的连接包装）| 真实认知 MCP server（文档检索等）|
| MCPAdapter（适配单工具为 BrainTool）| 工业执行 MCP server（detect_defects / annotate_label）|
| MCPRegistry（发现 + 三层判定 + 注册）| §13 文档修订（C 实现完后单独 PR）|
| 三层 ToolPolicy 判定器 | web_search 重构为 MCP（保持 Tool+Provider 不动）|
| echo_server trivial stub | |
| 端到端管道验证 | |

### 1.3 与 §13 的对齐

§13 的核心设计意图保留：
- MCPAdapter ABC 适配**单个工具**为 BrainTool
- `control/mcp_registry.py` 是薄桥接，只做发现 + 注册
- MCP 唯一实现位置在 `adapters/mcp/`
- LLM 通过 ToolRegistry 看到的全是 BrainTool，不感知后端是内部 Tool 还是 MCP

修正点：§13 "L1 LLM 和 L3 Activity 共享调用同一份 MCP 实现" → "L1 认知 MCP 在 cognitiveplane/adapters/mcp/，L3 工业 MCP 在 executionplane/各自位置，不共享"。此修正待 C 实现完后以独立 PR 落到 §13。

---

## 2. 架构与组件

### 2.1 文件结构

```
cognitiveplane/adapters/mcp/
├── __init__.py
├── base.py                       # MCPClient ABC + MCPServer + MCPAdapter + ToolDescriptor + MCPAnnotations
├── in_process.py                 # InProcessClient (Phase 3 唯一传输)
├── tool_policy_classifier.py     # 三层 ToolPolicy 判定器
└── stubs/
    ├── __init__.py
    └── echo_server.py            # trivial stub — 验证管道用 (1 个工具 echo_tool)

cognitiveplane/control/
└── mcp_registry.py               # MCPRegistry — 发现 + 三层判定 + 注册到 ToolRegistry

cognitiveplane/governance/
└── mcp_policy.yaml               # 第三层人工 override 配置 (Phase 3 空文件 + 注释样例)

cognitiveplane/tests/test_adapters/test_mcp/
├── __init__.py
├── test_in_process_client.py
├── test_mcp_adapter.py
├── test_tool_policy_classifier.py
├── test_mcp_registry.py
└── test_echo_server_e2e.py
```

### 2.2 四个核心类职责

| 类 | 文件 | 职责 |
|---|---|---|
| `MCPClient` (ABC) | `adapters/mcp/base.py` | 传输层抽象：`list_tools()` / `call_tool(name, args)` / `subscribe_list_changed(cb)`。Phase 3 只有 InProcessClient；Phase 5+ 加 StdioClient/SSEClient |
| `MCPServer` | `adapters/mcp/base.py` | 一个 MCP server 的连接包装。持有 MCPClient，暴露 `list_tools()` / `call_tool()` / `on_list_changed` 事件 |
| `MCPAdapter` (BrainTool) | `adapters/mcp/base.py` | 适配**单个工具**为 BrainTool。持有 MCPServer + tool_name，`execute()` 委托 `server.call_tool()` |
| `MCPRegistry` | `control/mcp_registry.py` | 注册 MCPServer 实例，发现工具，跑三层 ToolPolicy 判定，把 MCPAdapter 注册到 ToolRegistry。订阅 list_changed 自动重判 |

### 2.3 核心数据结构

```python
# adapters/mcp/base.py

@dataclass(frozen=True)
class ToolDescriptor:
    """MCP 工具描述 — MCPClient.list_tools() 返回。"""
    name: str                       # 工具名，如 "echo_tool" / 未来的 "search_docs"
    description: str
    parameters_schema: dict         # JSON Schema，给 LLM function calling 用
    annotations: MCPAnnotations | None = None  # §4.2.1 第一层判定来源

@dataclass(frozen=True)
class MCPAnnotations:
    """MCP 协议 annotations 字段 — 工具自声明安全属性。"""
    readOnlyHint: bool | None = None
    destructiveHint: bool | None = None
    openWorldHint: bool | None = None

@dataclass(frozen=True)
class MCPPolicyDecision:
    """三层 ToolPolicy 判定结果 — 缓存到 MCPAdapter.metadata。"""
    auto_approve: bool
    tier: str                       # "A" (retriable) / "B" (precise, require approval)
    require_reason: bool = False
    reason: str = ""                # 判定来源，如 "annotations.readOnlyHint" / "prefix:read" / "yaml_override"
```

### 2.4 类接口签名

```python
# adapters/mcp/base.py

class MCPClient(ABC):
    """MCP 传输层抽象。Phase 3 只有 InProcessClient。"""

    @abstractmethod
    async def list_tools(self) -> list[ToolDescriptor]:
        """返回该 server 暴露的所有工具描述。"""

    @abstractmethod
    async def call_tool(self, name: str, arguments: dict) -> dict:
        """调用工具，返回结构化结果。"""

    @abstractmethod
    def subscribe_list_changed(self, callback: Callable[[], Awaitable[None]]) -> None:
        """订阅 tools/list_changed 事件。callback 在工具列表变更时被调用。"""


class MCPServer:
    """一个 MCP server 的连接包装。"""

    def __init__(self, client: MCPClient, name: str) -> None: ...

    async def list_tools(self) -> list[ToolDescriptor]:
        return await self._client.list_tools()

    async def call_tool(self, name: str, arguments: dict) -> dict:
        return await self._client.call_tool(name, arguments)

    def on_list_changed(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._client.subscribe_list_changed(callback)


class MCPAdapter(BrainTool):
    """适配单个 MCP 工具为 BrainTool。无状态 — 每次 execute 都委托 server.call_tool。"""

    def __init__(self, server: MCPServer, descriptor: ToolDescriptor,
                 policy: MCPPolicyDecision) -> None:
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

    async def execute(self, **kwargs) -> ToolResult:
        try:
            result = await self._server.call_tool(self._descriptor.name, kwargs)
            return ToolResult(output=result)
        except Exception as e:
            return ToolResult(error=f"mcp_call_failed: {e}", metadata={"policy": self._policy})

    def to_function_definition(self) -> dict:
        # 标准 BrainTool function definition，LLM 看到的接口与内部工具完全一致
        return {
            "name": self._descriptor.name,
            "description": self._descriptor.description,
            "parameters": self._descriptor.parameters_schema,
        }


# control/mcp_registry.py

class MCPRegistry:
    """MCP 工具发现 + 三层 ToolPolicy 判定 + 注册到 ToolRegistry。

    薄桥接 — 不持有工具实现，只持有 MCPServer 引用和判定缓存。
    """

    def __init__(self, classifier: MCPToolPolicyClassifier,
                 event_log: EventLog | None = None) -> None: ...

    def register_server(self, server: MCPServer) -> None:
        """注册一个 MCPServer — 拉取工具列表、跑三层判定、缓存。"""
        server.on_list_changed(self._handle_list_changed)

    async def register_all(self, tool_registry: ToolRegistry) -> None:
        """把所有已注册 server 的工具注册到 ToolRegistry。"""
        ...

    async def _handle_list_changed(self, server: MCPServer) -> None:
        """list_changed 事件 — 重新拉工具列表、diff、重判、更新 ToolRegistry。"""
        ...
```

---

## 3. 数据流

### 3.1 启动时（composition root）

```
1. 创建 InProcessClient(echo_server_impl)
2. 创建 MCPServer(client=..., name="echo_server")
3. 创建 MCPToolPolicyClassifier(yaml_path="governance/mcp_policy.yaml")
4. 创建 MCPRegistry(classifier=..., event_log=...)
5. registry.register_server(server)
     └─ server.list_tools() → [ToolDescriptor(name="echo_tool", ...)]
     └─ 对每个 descriptor 跑三层判定 (见 §4)
     └─ 缓存 descriptor + policy 到 registry 内部表
6. await registry.register_all(tool_registry)
     └─ 对每个缓存的 (descriptor, policy) 创建 MCPAdapter(server, descriptor, policy)
     └─ tool_registry.register(adapter)  # MCPAdapter 是 BrainTool
7. 注入 registry 到 ControlDeps.mcp_registry
```

### 3.2 运行时（ReActEngine 单步）

```
8.  LLM 看到 tool_registry.get_llm_tool_definitions() 包含 echo_tool
9.  LLM 决定调 echo_tool(text="hello")
10. ReActEngine 调 tool_registry.execute("echo_tool", {"text": "hello"})
11. ToolRegistry 找到 MCPAdapter 实例，调 adapter.execute(text="hello")
12. MCPAdapter.execute 委托 server.call_tool("echo_tool", {"text": "hello"})
13. MCPServer.call_tool 委托 client.call_tool(...)
14. InProcessClient 路由到 echo_server 的 Python 函数，返回 {"echo": "hello"}
15. MCPAdapter 包装成 ToolResult(output={"echo": "hello"})
16. ToolRegistry 返回 ToolResult 给 ReActEngine
17. ReActEngine 把 tool_result 注入下一轮 LLM 消息
18. LLM 看到 echo 结果，继续推理或给最终答复
```

### 3.3 list_changed 事件流（Phase 3 stub 不触发，管道就位）

```
19. MCPServer.on_list_changed emit
20. MCPRegistry._handle_list_changed(server):
      └─ 重新拉 list_tools()
      └─ diff 旧工具列表 vs 新工具列表
      └─ 新增工具: 跑三层判定 → 创建 MCPAdapter → tool_registry.register
      └─ 下线工具: tool_registry.unregister(name)
      └─ 变更工具: 重新判定 + 替换 adapter
      └─ 写 EventLog 记录变更 + 判定原因
```

### 3.4 关键设计点

- **三层判定在注册时计算一次**，缓存到 `MCPAdapter.metadata["mcp_policy"]`，不在每次调用时重判（§4.2.1 "默认偏保守"约束）
- **MCPAdapter 是无状态 BrainTool**，每次 execute 都委托 server.call_tool — 不缓存结果
- **list_changed 是异步事件**，MCPRegistry 用 asyncio.CallbackList 维护订阅者，避免阻塞主循环
- **InProcessClient 是 Phase 3 唯一传输**，call_tool 直接调 Python 函数；Phase 5+ 加 StdioClient 时，call_tool 改成 JSON-RPC over stdin/stdout，上层（MCPServer / MCPAdapter / MCPRegistry / ToolRegistry / ReActEngine）全部不变

---

## 4. 三层 ToolPolicy 判定

`adapters/mcp/tool_policy_classifier.py` 实现 §4.2.1 三层规则。

### 4.1 判定顺序

§4.2.1 字面顺序是"第一层 annotations → 第二层前缀 → 第三层 YAML override"，但第三层"人工 override 始终优先"语义上应该最高。本设计把判定顺序调整为：

```
1. YAML override (最高优先级，人工配置)
2. MCP annotations
3. 名字前缀启发式
4. 未匹配 → 保守 Tier-B
```

**偏差理由**：§4.2.1 明文约束"人工配置始终优先于自动判定"，但字面顺序把第三层放在最后。这是 §4.2.1 内部的不一致。本设计遵循明文约束，把 YAML override 提到最前。spec 此处标记为对 §4.2.1 字面顺序的微调，需在 C 实现完后以独立 PR 落到 §4.2.1。

### 4.2 判定器实现

```python
# adapters/mcp/tool_policy_classifier.py

READ_PREFIXES = ("read_", "get_", "list_", "search_", "find_", "query_")
WRITE_PREFIXES = ("create_", "update_", "delete_", "import_", "send_", "publish_", "post_")

class MCPToolPolicyClassifier:
    def __init__(self, yaml_path: Path | None = None) -> None:
        self._yaml_overrides = self._load_yaml(yaml_path) if yaml_path else {}

    def classify(self, descriptor: ToolDescriptor) -> MCPPolicyDecision:
        # 第一层: YAML override (最高)
        if descriptor.name in self._yaml_overrides:
            return MCPPolicyDecision(**self._yaml_overrides[descriptor.name],
                                     reason="yaml_override")

        # 第二层: MCP annotations
        if descriptor.annotations:
            if descriptor.annotations.readOnlyHint is True:
                return MCPPolicyDecision(auto_approve=True, tier="A",
                                         reason="annotations.readOnlyHint")
            if descriptor.annotations.destructiveHint is True:
                return MCPPolicyDecision(auto_approve=False, require_reason=True, tier="B",
                                         reason="annotations.destructiveHint")
            if descriptor.annotations.openWorldHint is True:
                return MCPPolicyDecision(auto_approve=False, tier="B",
                                         reason="annotations.openWorldHint")

        # 第三层: 名字前缀启发式
        if descriptor.name.startswith(READ_PREFIXES):
            return MCPPolicyDecision(auto_approve=True, tier="A", reason="prefix:read")
        if descriptor.name.startswith(WRITE_PREFIXES):
            return MCPPolicyDecision(auto_approve=False, require_reason=True, tier="B",
                                     reason="prefix:write")

        # 未匹配 → 保守 Tier-B
        return MCPPolicyDecision(auto_approve=False, tier="B",
                                 reason="prefix:unknown:conservative")
```

### 4.3 echo_stub 判定结果

echo_stub 的 `echo_tool`：
- 无 annotations → 跳过第一层（调整后的第二层）
- 名字 `echo_` 不匹配 read_/write_ 前缀 → 落入保守 Tier-B

结果：`MCPPolicyDecision(auto_approve=False, tier="B", require_reason=False, reason="prefix:unknown:conservative")`

这对验证管道刚好够用 — 能演示 Hook 拦截路径（PolicyHook 会检查 auto_approve，echo_tool 不 auto_approve，会触发拦截或审批逻辑）。

### 4.4 mcp_policy.yaml 初始内容

```yaml
# governance/mcp_policy.yaml
# Phase 3: 第三层人工 override 配置。
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

---

## 5. 错误处理

| 失败点 | 处理 | 理由 |
|---|---|---|
| MCPClient.list_tools() 失败 | MCPRegistry 记录 EventLog，跳过该 server，不阻塞其他 server 注册 | 一个 server 挂不能拖垮整个启动 |
| MCPClient.call_tool() 失败 | MCPAdapter 返回 `ToolResult(error=...)`，ReActEngine 走 Tier-A/B retry 策略（§3.6）| MCP 工具失败与内部工具失败走同一套 ToolReliability 路径 |
| 三层判定异常（YAML 格式错） | 记录 EventLog + fallback 到保守 Tier-B | 不能因为配置错误让 server 注册失败 |
| list_changed 事件处理异常 | 记录 EventLog，保留旧工具列表不动 | 重载失败不能破坏现有状态 |
| ToolRegistry.unregister 不存在工具 | 静默返回 | list_changed diff 可能与当前注册状态有偏差 |

**原则**：MCP 基础设施层的故障不应让 ReActEngine 崩溃。所有异常记 EventLog，决策走保守路径。

---

## 6. 测试策略

5 个测试文件，对应 5 个验证点：

| 文件 | 验证 | 类型 |
|---|---|---|
| `test_in_process_client.py` | InProcessClient.list_tools / call_tool 路由正确 | 单元 |
| `test_mcp_adapter.py` | MCPAdapter.execute 委托 server.call_tool，返回 ToolResult；call_tool 异常时返回 ToolResult(error) | 单元 |
| `test_tool_policy_classifier.py` | 三层判定覆盖：YAML override × annotations 三 hint × 6 个 read 前缀 × 7 个 write 前缀 × 未匹配保守 | 单元 |
| `test_mcp_registry.py` | register_server → 注册 adapter；list_changed → 新增/下线/变更工具正确处理；YAML override 优先于 annotations | 单元 |
| `test_echo_server_e2e.py` | echo_server → Registry → ToolRegistry → ReActEngine（mock LLM 触发 echo_tool 调用）→ tool_result 回流 LLM 下一轮 | 端到端 |

E2E 测试用 `capability/mock.py` 的 mock LLM provider，让它强制返回 `echo_tool` 的 tool_call，验证整条管道。

### 6.1 C 子项目验收标准

C 子项目自身验收（不是 Phase 3 整体验收）：

| # | 验收项 | 通过条件 |
|---|---|---|
| C.1 | MCPAdapter 是 BrainTool | `isinstance(MCPAdapter(...), BrainTool)` 为 True，能通过 `tool_registry.register()` 注册 |
| C.2 | 三层判定全覆盖 | 单元测试覆盖 YAML override / 3 个 annotations hint / read+write 前缀 / 未匹配保守，共 ≥10 个 case |
| C.3 | list_changed 管道就位 | 单元测试模拟 list_changed 事件，新增/下线/变更工具正确反映到 ToolRegistry |
| C.4 | echo_server 端到端 | E2E 测试：mock LLM 触发 echo_tool → tool_result 正确回流 LLM 下一轮消息 |
| C.5 | 不引入 Phase 3+ 禁用项 | C 代码 diff 中不出现 `agent_loop.py` / `request_detection` / `request_annotation` / `switch_role` / `request_image_detail` / 真实工业 MCP server 实现 |

---

## 7. 改动文件清单

### 7.1 新增文件

- `cognitiveplane/adapters/mcp/__init__.py`
- `cognitiveplane/adapters/mcp/base.py`
- `cognitiveplane/adapters/mcp/in_process.py`
- `cognitiveplane/adapters/mcp/tool_policy_classifier.py`
- `cognitiveplane/adapters/mcp/stubs/__init__.py`
- `cognitiveplane/adapters/mcp/stubs/echo_server.py`
- `cognitiveplane/control/mcp_registry.py`
- `cognitiveplane/governance/mcp_policy.yaml`
- `cognitiveplane/tests/test_adapters/test_mcp/__init__.py`
- `cognitiveplane/tests/test_adapters/test_mcp/test_in_process_client.py`
- `cognitiveplane/tests/test_adapters/test_mcp/test_mcp_adapter.py`
- `cognitiveplane/tests/test_adapters/test_mcp/test_tool_policy_classifier.py`
- `cognitiveplane/tests/test_adapters/test_mcp/test_mcp_registry.py`
- `cognitiveplane/tests/test_adapters/test_mcp/test_echo_server_e2e.py`

### 7.2 改动文件

- `cognitiveplane/control/deps.py` — 解锁 `ControlDeps.mcp_registry` 字段（从注释变为真实字段）
- `cognitiveplane/control/tool_registry.py` — 增加 `unregister(name)` 方法（list_changed 下线工具需要）
- `cognitiveplane/app.py` — composition root 注册 echo_server + MCPRegistry（可选，也可只在测试里注册）

### 7.3 不改文件

- `control/react.py` — ReActEngine 不感知工具来源是 MCP 还是内部，统一通过 ToolRegistry 调
- `control/hooks.py` — Hook 已对 BrainTool 通用，MCPAdapter 是 BrainTool，自动覆盖
- `governance/tool_policy.py` — ToolPolicy 是手工配表（内部工具用），MCP 工具走 MCPToolPolicyClassifier，两条路径不冲突

---

## 8. 与计划文档的偏差记录

C 实现完后需要以独立 PR 修订计划文档：

| 偏差 | 位置 | 修订内容 |
|---|---|---|
| L1/L3 MCP 分层 | §13 | "L1 LLM 和 L3 Activity 共享同一份 MCP 实现" → "L1 认知 MCP 在 cognitiveplane/adapters/mcp/，L3 工业 MCP 在 executionplane/各自位置，不共享" |
| §11.4 验收 3.3 / 3.4 | §11.4 | "LLM 直接调 detect_defects / annotate_label MCP" → "LLM 调认知平面 request_detection / request_annotation 代理 Tool，经 Gateway → L2 → L3 链路" |
| 三层判定顺序 | §4.2.1 | 字面顺序"annotations → 前缀 → YAML" → "YAML override → annotations → 前缀"（遵循"人工配置始终优先"明文约束）|
| §10 表 "L1→L2 Bridge" 行 | §10 | 标注 Bridge 不仅是 L1→L2，也包括 L1→L3 代理 Tool 路径 |
| §10 "外部工具" 行例子 | §10 | "Label Studio、检测模型等通过 MCP 动态接入" → "认知内容 MCP（文档检索、知识图谱等）通过 MCP 动态接入认知平面；工业执行 MCP（Label Studio、检测模型）在 executionplane 通过 MCP 接入，L1 LLM 经认知平面代理 Tool 间接调用" |

这些偏差不在 C 子项目实现范围内，C 完成后单独处理。

---

## 9. 不在 Phase 3 范围

- 真实认知 MCP（文档检索、知识图谱等）— 按需接入
- 真实工业 MCP（detect_defects / annotate_label）— 移到 executionplane 仓库
- StdioClient / SSEClient — Phase 5+
- §13 / §4.2.1 / §11.4 / §10 文档修订 — C 实现完后单独 PR
- AgentLoop（子项目 B）— C 后续子项目
- WebSocket 流式协议升级（子项目 A）— C 后续子项目
- 标注 UI + 反馈闭环（子项目 E）— C 后续子项目