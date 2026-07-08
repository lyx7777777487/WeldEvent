# Phase 3 边界钉死 Delta Spec

- **日期**: 2026-06-25
- **状态**: Draft (待用户 review)
- **性质**: Delta — 不推倒 `2026-06-11-cognitiveplane-7plane-redesign.md`，在其之上钉死 Brain / Temporal / Activity / Tool Pool / MCP 五层边界。原 spec 保留作 7-Plane 职责、Hook、ToolPolicy 等细节来源，本 spec 是**边界权威**。
- **触发**: Phase 3 推进中发现代码长出 4 个偏航点（ToolRegistry 过宽 / AgentLoop 上主路治理弱 / MCPAdapter 伪装 BrainTool / design_workflow 不输出 DAG）。止血已完成（A1-A4 + #5），本 spec 把止血背后的边界契约写死，避免后续阶段回退。

---

## 0. 一句话主线

```
Brain / ReAct — 判断 + 规划 + 生成 WorkflowSpec + 解释 Temporal 状态
Temporal       — 执行 WorkflowSpec，可靠性 / 等待 / 重试 / 审计
Activity Pool  — 执行 DAG 节点
Tool Pool      — 统一封装内部工具 / MCP / HTTP / 模型
MCP            — Tool Pool 的一种底层协议，不是 Brain 直连对象
```

**LLM 决定一切** 仍然成立——LLM 决定的是**走哪条路 + 流程 + 判断 + 分支 + 人工介入点**，不是机械地等 signal 和 retry。

---

## 1. 五层边界

### 1.1 Brain / ReAct（L1 认知层）

**职责**：理解、判断、规划、生成 `WorkflowSpec`、解释 Temporal 回传状态、决定是否调整 DAG、决定人工介入点。

**不做**：
- 不做长流程执行（不持久化重试状态、不跨会话保持执行上下文）
- 不直接调工业执行 MCP（`detect_defects` / `annotate_label` 等）
- 不替 Temporal 决定执行顺序、重试策略、人工 gate 时机

**运行时容器**：`AgentLoop`——会话容器 + 短路径 ReAct 宿主 + 长路径 Temporal 观察者（详见 §3）。

### 1.2 Temporal（L2 控制层）

**职责**：可靠执行 `WorkflowSpec`——重试、等待人工、超时、补偿、审计、顺序保证。

**不做**：
- 不做认知判断（不调 LLM 做意图分类、不解释结果语义）
- 不直接调 L4 工具（只调度 Activity）

**接入时机**：Phase 4+。Phase 2/3 主线无 Temporal，所有决策走 ReAct 短路径。`bridge/` 当前为占位骨架，未来按 §6 改向接入。

### 1.3 Activity Pool（L3 执行层）

**职责**：执行 DAG 节点——`brain_task` / `tool_task` / `human_task` / `wait_task`。每个节点声明 `capability`（不是具体工具名），由 Tool Pool 解析到具体实现。

**不做**：
- 不做认知判断（`brain_task` 节点除外——它回调 Brain 做一次 ReAct）
- 不直接持有 L4 工具实现（只声明 capability，由 Tool Pool 解析）

**接入时机**：Phase 4+。当前 L3 在 `executionplane/` 仓库侧未实现。

### 1.4 Tool Pool（L4 能力层）

**职责**：统一封装内部工具 / MCP / HTTP / 模型 / WeldMap 访问。提供 capability-based 解析接口给 Activity Pool，也提供 tool-based 接口给 BrainToolRegistry。

**不做**：
- 不做业务决策（只执行被调用的能力）
- 不替 Brain 决定调用顺序

**接入时机**：Phase 4+ 长路径需要。当前 BrainToolRegistry（§4）已承担短路径的 tool 解析。

### 1.5 MCP（L4 协议层）

**职责**：Tool Pool 的一种底层协议适配。Brain 不直接调 MCP server，MCP server 也不直接注册到 BrainToolRegistry。

**三类 MCP（关键 — 2026-07-06 修订，拆分"标注管理"为第三类）**：

| 类别 | 例子 | 归属 | 调用方 | 审批策略 |
|------|------|------|--------|---------|
| 认知内容 MCP | web_search、文档检索、echo_server stub | 认知平面仓库（L1） | BrainToolRegistry → MCPAdapter（`phase=4`） | Tier-A auto（查询类） |
| 标注管理 MCP | list_datasets、get_dataset、create_job、upload_images、create_task、assign_task | L3 实现 + L1 adapter（`phase_override=3`） | BrainToolRegistry → MCPAdapter + approval gate | 查询类（list_/get_）Tier-A auto；写入类（create_/upload_/assign_）走 approval gate 用户确认 |
| 严格工业执行 MCP | detect_defects、annotate_label（AI 写标注结果）、trigger_ai（AI 推理） | `executionplane/` 仓库（L3） | Activity Pool → ToolPool → MCP client | 永远不暴露给 Brain |

认知内容 MCP 可以（在 phase≥4 时）作为 BrainTool 暴露给 LLM——这是"认知动作"的协议变体。

**标注管理 MCP** 是认知动作（LLM 在对话中帮用户管理标注作业 CRUD），可暴露给 Brain，但写操作（create_/upload_/assign_）**必须**经架构级 approval gate 拦截，emit `approval_request` 事件阻塞等前端用户确认。判定依据：`list_datasets` 类似 `search_standards`（认知检索），不是 `defect_detection`（工业推理）。

**严格工业执行 MCP**（AI 模型推理类）永远不暴露给 Brain，必须通过 WorkflowSpec → Activity → ToolPool 路径调用。

**为什么拆分**：原 spec 把"标注"一刀切归"工业执行 MCP"，但 Label Studio 的 `create_job` 是**管理动作**（创建作业），不是**写标注结果**（`annotate_label` 才是 AI 推理写标注）。spec §2.1 的判定测试"动词是认知还是工业"——`list_datasets`/`create_job` 是认知管理动词，`trigger_ai` 才接近工业推理动词。拆分后既符合业务现实，又保留 approval gate 缓解措施。

**当前实现**：
- `MCPAdapter` 在 `adapters/mcp/base.py`，`phase=4` 默认对 LLM 不可见
- 标注管理 MCP 通过 `MCPServer(phase_override=3)` 在 `cognitiveplane/adapters/mcp/label_studio_server.py` 装配
- 三层 ToolPolicy 判定：YAML override > annotations > 前缀启发式（`governance/mcp_policy.yaml` 配置 10 个标注工具分级）
- 写入类 5 个工具（create_job/upload_images/create_task/trigger_ai/assign_task）注册到 `react.py::APPROVAL_REQUIRED_TOOLS`，架构级拦截走 approval gate

---

## 2. 分流准则 — 短路径 vs 长路径

Brain 在 ReAct 内自主决定走哪条路。判断维度：要不要等人 / 要不要重试 / 要不要审计 / 跨不跨会话。是 → 长路径；否 → 短路径。

| 路径 | 触发 | Brain 行为 | 调用形态 |
|------|------|-----------|---------|
| 短路径 | 同步认知动作（检索/读/记忆/解释/看图） | 直接调 BrainTool | `tool_name + args`，一次 ReAct 迭代内完成 |
| 长路径 | 工业动作 / 跨会话 / 等人 / 审计 | 调 `design_workflow` 生成 DAG | `WorkflowSpec{nodes: [{capability, args}], edges}` |

**禁止**：把短路径工具包成 DAG 节点（不必要的 indirection，让 ReAct 变脆），或让长路径动作走 BrainTool 直调（绕过 Temporal 的可靠性保证）。

### 2.1 BrainTool vs ToolPool capability 判定准则

| 类别 | 判定 | 例子 | 调用方 |
|------|------|------|--------|
| BrainTool | 认知动词：检索/读/记忆/解释/看图 | `search_standards` `read_weldmap` `archive_memory` `analyze_image` `explain_decision` `web_search` | ReAct 直接调 |
| ToolPool capability | 工业动词：检测/标注/调整/测量 | `defect_detection` `annotation_write` `parameter_adjust` `human_review` | Activity 解析 capability 名后调 |

**判定测试**：动词是"认知"还是"工业"。`analyze_image`（Brain 看图分析）是 BrainTool，`defect_detection`（工业检测模型推理）是 ToolPool capability——不会有人把 `analyze_image` 包成 DAG 节点，也不会有人让 Brain 直调 `defect_detection`。

---

## 3. AgentLoop 角色三件套

`AgentLoop` 在边界钉死后的角色**收窄**为三件，不是长流程执行器：

| 角色 | Phase | 状态 |
|------|-------|------|
| 交互会话容器（管理 WebSocket 生命周期、receive_task） | 2/3 | ✅ 已实现 |
| 短路径 ReAct 宿主（react_task 跑 ReActEngine.run()） | 2/3 | ✅ 已实现 |
| 长路径 Temporal 观察者（把 Temporal workflow 进度推回 WebSocket） | 4+ | 待实现 |

**不做**：长流程执行器。Phase 2/3 中 AgentLoop 临时承担 feedback 写 Memory 的责任，**Temporal 到位后 feedback 走 Temporal HumanGateSignal**，AgentLoop 不再持有 feedback queue。

### 3.1 治理一致性（已修复，A2）

AgentLoop 必须与 HTTP `/api/v1/chat` 路径用**同一套 hooks**——`[SafetyHook(), PolicyHook(tool_policy)]`。不能让同一工具调用因入口不同（HTTP vs WebSocket）而绕过 `ToolPolicy`。

实现位置：`control/agent_loop.py:__init__` 接受 `hooks: list[BeforeToolHook] | None = None`；`interaction/api/chat.py` `/ws` 入口显式传 `hooks=hooks`。

---

## 4. BrainToolRegistry vs ToolPool — 物理拆分

### 4.1 当前状态（Phase 2/3）

`ToolRegistry` 同时承担两个角色：BrainTool 注册中心 + 未来 ToolPool 雏形。Phase 2/3 短路径下这是可接受的——只有 BrainTool，没有 Activity 调用方。

### 4.2 目标状态（Phase 4+）

物理拆分为两个 Registry：

| Registry | 位置 | 调用方 | 内容 |
|----------|------|--------|------|
| BrainToolRegistry | `cognitiveplane/control/` | ReAct LLM | 认知动词工具（BrainTool 子类） |
| ToolPool | `executionplane/`（实现） + L1 接口（认知平面侧只持抽象 port） | Activity Pool | 工业动词 capability（含工业执行 MCP adapter） |

**认知内容 MCP** 留在 BrainToolRegistry（通过 MCPAdapter 注册）；**工业执行 MCP** 归 ToolPool（在 `executionplane/` 仓库实现）。

### 4.3 Phase Gate 机制（已实现，A4）

过渡期用 `BrainTool.phase` 类属性 + `ToolRegistry.current_phase` 控制 LLM 可见性：

- 工具仍注册（`execute()` 可显式调用），但 `phase > current_phase` 时不出现在 `get_llm_tool_definitions()` 里
- ReAct Tier-1 路径硬猜隐藏工具名 → 发 `tool_call` 事件 `rejected=True` + 回写拒绝原因给 LLM
- Tier-2 / Tier-3 fallback 路径调 `is_llm_visible()` 检查，不会路由到隐藏工具

当前 phase 标签（2026-06-25）：

| 工具 | phase | 阶段语义 |
|------|-------|---------|
| `web_search` | 1 | 阶段 1 起可见 |
| `analyze_image` | 2 | 阶段 2 多模态 |
| `search_standards` / `search_cases` / `read_weldmap` / `request_confirmation` / `escalate` / `design_workflow` / `manage_plan` | 3 | 阶段 3 反馈+MCP |
| `search_process` / `MCPAdapter` | 4 | 阶段 4 |
| `adjust_parameter` | 5 | 阶段 5 规则系统 |
| `archive_memory` | 6 | 阶段 6 知识库 |
| `explain_decision` | 7 | 阶段 7 |

`manage_plan` 已从默认 `_register_tools` 移除（A3）——工具类保留，按需 opt-in。

---

## 5. WorkflowSpec 数据结构

### 5.1 DTO 定义

`cognitiveplane/shared/dto_workflow.py`：

```python
NodeType   = Literal["brain_task", "tool_task", "human_task", "wait_task"]
OnFailure  = Literal["abort", "continue", "escalate", "retry"]
CallerType = Literal["brain_direct", "activity", "system"]

class ToolIntent(BaseModel):
    capability: str           # capability 名, 不是具体工具/MCP binding
    input: dict[str, Any]
    reason: str = ""

class CallerContext(BaseModel):
    caller_type: CallerType
    case_id: str | None
    node_id: str | None
    session_id: str | None

class WorkflowNode(BaseModel):
    node_id: str
    type: NodeType
    capability: str | None    # brain_task 节点可为 None
    depends_on: list[str]
    input: dict[str, Any]
    condition: str | None     # 条件分支
    on_failure: OnFailure = "escalate"
    caller_context: CallerContext

class WorkflowSpec(BaseModel):
    workflow_id: str          # 自动生成 wf-<hex8>
    objective: str
    requirements: list[str]
    nodes: list[WorkflowNode]
    metadata: dict[str, Any]
```

### 5.2 design_workflow 重定义（已实现）

`design_workflow` 不再调旧 `BrainOrchestrator.execute()`，而是产出 `WorkflowSpec` 草案：

- `execute()` 接收 `{objective, reason, requirements?, case_id?}`，调 `_build_workflow_spec()` 产出 `WorkflowSpec`
- 返回 `{"status": "draft", "workflow_spec": spec.model_dump()}`
- 工具本体**不**启动 Temporal、**不**写标注、**不**调工业 MCP——只产出草案
- 是否提交 Temporal 由上层确认 / 网关策略处理

### 5.3 三档 LLMTier 入口形状契约（已实现，#5）

三档调用路径必须用同一入口形状 `{objective, reason, ...}`，禁止 fallback 路径用旧 `{"query": ...}`：

| LLMTier | 调用形态 | 入口形状 |
|---------|---------|---------|
| Tier-1 | LLM 直接生成 tool_call | `{objective, reason, requirements?, case_id?}` |
| Tier-2 | `_run_structured` 按意图路由 | `{objective: user_input, reason: "Tier-2 ... fallback"}` |
| Tier-3 | `_run_embedding_rules` 按关键词匹配 | `{objective: user_input, reason: "Tier-3 ... fallback"}` |

详见 plan §3.5.1。

---

## 6. Temporal Bridge 改向

### 6.1 当前状态（占位骨架）

`cognitiveplane/bridge/` 现有 `WorkflowLauncher` / `DecisionTranslator` 是历史遗留，**没有接到运行中的 app**。沿用旧 `BrainDecision → WorkflowTemplate` 翻译模型。

### 6.2 目标状态（Phase 4+）

废弃 `BrainDecision → WorkflowTemplate`，改为 `WorkflowSpec → Temporal Generic DAG Runner`：

- Brain 调 `design_workflow` 产出 `WorkflowSpec`
- 上层确认后，通过 `CognitiveGatewayWritePort.notify_workflow_trigger` 提交
- Bridge 层把 `WorkflowSpec` 翻译成 `temporal_client.start_workflow(RunWorkflowSpec, workflow_spec)`
- Temporal workflow 是 generic DAG runner，按 `nodes` 顺序调度 Activity，每个 Activity 按 `capability` 调 ToolPool
- feedback 走 Temporal `HumanGateSignal`，不再走 AgentLoop feedback queue

**Phase 2/3 不实现**——bridge 保留占位，等 L2 数据平面（WeldMap 全量事件溯源）就绪后接入。

---

## 7. MCP 双类拆分（已确认）

详见 `[[mcp-l1-l3-split]]` 和 §1.5。要点：

- 认知平面（L1）只持**认知内容 MCP**——`echo_server` stub、未来 web_search MCP、文档检索 MCP
- **工业执行 MCP**（`detect_defects` / `annotate_label` / `update_annotation`）归 `executionplane/` 仓库（L3），受 L2 activity pool 调用
- 两套 MCP 不共享——认知平面通过 MCP 管道调用 L3 提供的执行工具，但工具实现本体在 L3 仓库
- `MCPAdapter` 在认知平面侧 `phase=4`，Phase 2/3 对 LLM 不可见

---

## 8. 当前实现状态对照

| 章节 | 契约 | 代码状态 |
|------|------|---------|
| §1.1 Brain 不直调工业 MCP | `MCPAdapter.phase=4`，`detect_defects` 等不在 BrainToolRegistry | ✅ A4 |
| §1.5 MCP 双类 | 认知内容 MCP 留 L1，工业执行 MCP 归 L3 | ✅ [[mcp-l1-l3-split]] |
| §3.1 AgentLoop 治理一致 | `/ws` 入口传 `[SafetyHook(), PolicyHook(...)]` | ✅ A2 |
| §3 AgentLoop 角色三件套 | 会话容器 + 短路径宿主已实现，长路径观察者待 Phase 4 | ✅ 短路径 |
| §4.3 Phase Gate | `BrainTool.phase` + `ToolRegistry.current_phase` + Tier-1/2/3 三档检查 | ✅ A4 |
| §4.3 manage_plan 移除默认注册 | `_register_tools` 不再注册 ManagePlanTool | ✅ A3 |
| §5.2 design_workflow 产出 WorkflowSpec | `execute()` 调 `_build_workflow_spec()` | ✅ |
| §5.3 三档入口形状一致 | Tier-1/2/3 都用 `{objective, reason, ...}` | ✅ #5 |
| §6 Temporal Bridge 改向 | 废弃 `BrainDecision → WorkflowTemplate` | ⏳ Phase 4+ |
| §4.2 BrainToolRegistry vs ToolPool 物理拆分 | 两个 Registry | ⏳ Phase 4+ |
| §1.2 Temporal 接入 | bridge 接运行 app | ⏳ Phase 4+ |
| §1.3 Activity Pool 实现 | `executionplane/` 仓库 | ⏳ Phase 4+ |

---

## 9. 冻结令

Phase 3 推进冻结（2026-06-25）：3.3 / 3.4 / 3.5 真实闭环全停，不接 `detect_defects` / `annotate_label` MCP。在 §6 / §4.2 / §1.2 / §1.3 落地前，不堆 Phase 3 功能。详见 `[[phase3-boundary-pinning-freeze]]`。

---

## 10. 后续 spec 拆分

本 spec 钉死边界。后续实施按章节拆 sub-spec / sub-plan：

- WorkflowSpec + design_workflow 重定义 → 已完成（§5）
- Phase Gate 机制 → 已完成（§4.3）
- AgentLoop 角色三件套 → 短路径已完成（§3），长路径观察者待 Phase 4
- Temporal Bridge 改向 → Phase 4 sub-spec
- BrainToolRegistry vs ToolPool 物理拆分 → Phase 4 sub-spec
- Activity Pool 实现 → `executionplane/` 仓库 sub-spec