# WeldEvent — 工业焊缝质检智能决策引擎

> 基于三层 Agent 架构的焊接质检 AI 系统。
> 通过 LLM 推理 + Temporal 工作流 + OpenCV 视觉算法，实现焊缝图像的自动质检、标注和工作流编排。

**版本**: v0.3.0 | **分支**: `26-513-NOW` | **最新提交**: `06eb59f`

---

## 目录

- [架构概览](#架构概览)
- [快速开始](#快速开始)
- [L1 认知平面](#l1-认知平面-cognitiveplane)
- [L2 控制平面](#l2-控制平面-controlplane)
- [工作流治理层](#工作流治理层-governance-layer)
- [治理事件溯源](#治理事件溯源-governance-event-sourcing)
- [L3 执行平面](#l3-执行平面-executionplane)
- [Bridge 桥接层](#bridge-桥接层)
- [Shared 共享基础设施](#shared-共享基础设施)
- [治理与安全](#治理与安全)
- [评估框架](#评估框架)
- [前端](#前端)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [测试](#测试)
- [当前状态与限制](#当前状态与限制)
- [技术与算法来源总表](#技术与算法来源总表)
  - [L1 · 认知平面](#l1--认知平面-cognitiveplane)
  - [L2 · 控制平面](#l2--控制平面-controlplane)
  - [L3 · 执行平面](#l3--执行平面-executionplane)
  - [跨层 · Shared + Bridge](#跨层--shared--bridge)
  - [前沿 Agent 框架对标总表](#前沿-agent-框架对标总表)

---

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│ L1 · 认知平面 (Cognitive Plane)                              │
│ FastAPI + ReActEngine + 18 工具 + WebSocket/SSE              │
│ LLM 推理 · 工具编排 · 用户交互 · 自然语言治理 · 审批门禁      │
│ 用户说"扣住这个批次" → agent 理解意图 → control_workflow     │
│ 33,842 行 │ DeepSeek-Chat + Volc Doubao Vision               │
└───────────────────────┬─────────────────────────────────────┘
                        │ Bridge L1↔L2: WorkflowSpec + EventBus
                        │ NL → agent → signal (8 governance action)
┌───────────────────────┴─────────────────────────────────────┐
│ L2 · 控制平面 (Control Plane)                                │
│ Temporal DAG Runner + Worker + 治理层                        │
│ 拓扑排序 · 节点执行 · 9 状态机 · 8 治理模块 · 事件溯源 · 审计│
│ 4,292 行 │ temporalio ≥1.27                                  │
└───────────────────────┬─────────────────────────────────────┘
                        │ Bridge L2↔L3: execute_node + WeldMap
┌───────────────────────┴─────────────────────────────────────┐
│ L3 · 执行平面 (Execution Plane)                              │
│ ActivityPool + IQA + PPA + Annotation + WeldMap              │
│ 图像质量评估 · 自适应预处理 · Label Studio MCP 标注           │
│ 6,371 行 │ OpenCV/NumPy/Pillow + MCP 协议                     │
└─────────────────────────────────────────────────────────────┘
```

**核心设计原则**: "LLM 决定做什么，架构决定不能做什么。" 系统不是 Pipeline，而是 LLM 的能力基座——提供工具、记忆、安全边界，让 LLM 自主决策。固定的是可用工具集、安全边界和记忆存取方式；动态的是 LLM 每步选择调用哪个工具、如何推理、何时停止。

---

## 快速开始

### 前置条件

- Python ≥3.12
- Temporal Server（开发用 Docker Compose）
- DeepSeek API Key

### 启动步骤

```bash
# 1. 启动 Temporal（开发环境）
docker compose -f controlplane/docker-compose-temporal.yml up -d

# 2. 配置环境变量
cat > .env << 'EOF'
DEEPSEEK_API_KEY=sk-your-key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
LANGFUSE_PUBLIC_KEY=pk-optional
LANGFUSE_SECRET_KEY=sk-optional
EOF

# 3. 启动 L2 Temporal Worker
python -m controlplane.worker

# 4. 启动 L1 API Server
uvicorn cognitiveplane.app:app --host 0.0.0.0 --port 8000

# 5. 打开前端
open http://localhost:8000/static/chat.html
```

---

## L1 认知平面 (cognitiveplane)

L1 是整个系统的入口层，负责接收用户输入、驱动 LLM 推理、编排工具调用、返回结果。

### ReActEngine — 核心推理循环

**文件**: `cognitiveplane/control/engine/react.py` (1,622 行)

ReActEngine 是 L1 的心脏，实现了完整的 Reasoning + Acting 循环。

**三级 LLM 回退（启动时确定，运行时不切换）**:

| 级别 | 名称 | 触发条件 | 行为 |
|------|------|---------|------|
| Tier-1 | Function Calling | LLM 支持原生 tool_calls | LLM 自主推理并调用工具，循环直到不再需要 |
| Tier-2 | Structured Output | LLM 支持 JSON mode 但不支持 FC | 意图分类（5 类）+ 规则化工具路由 |
| Tier-3 | Embedding Rules | 无 LLM | 中英文关键词匹配 → 最佳可用工具（10 个关键词-工具映射） |

**每轮迭代三阶段**:

1. **顺序校验**: JSON 解析 → JSON Schema 验证(`jsonschema.Draft7Validator`) → Hook 检查(SafetyHook + PolicyHook)。被拒工具注入 LLM 上下文以便重试。
2. **并发执行**: 所有通过校验的工具 `asyncio.gather` 并发执行。
3. **顺序结果处理**: 按 LLM 原始 `tool_calls` 顺序返回结果（LLM 对上下文顺序敏感）。

**关键机制**:

| 机制 | 实现 | 关键常量 |
|------|------|---------|
| **ApprovalGate** | 6 个写入类工具执行前 `await event.wait(timeout=300s)`，架构层阻塞 | `APPROVAL_TIMEOUT=300s` |
| **Context Compaction** | 4 策略(SLIDING_WINDOW/FULL_SUMMARY/SELF_COMPACT_SLIDING/SELF_COMPACT_ALL) + expensive→cheap fallback + MUST_PRESERVE/CAN_DROP 边界 | `max_tokens=8000` |
| **Session Notes** | `_derive_note` 覆盖 23 个工具(13 L1 + 10 MCP 标注)，每轮记录结果摘要/产出 ID，FIFO 上限 8 条 | — |
| **同轮工具上限** | `SAME_TOOL_LIMIT=3`，超限静默 reject，回填提示走 web_search | `SAME_TOOL_LIMIT=3` |
| **DSML 回退解析** | LLM 未返回原生 tool_calls 时正则解析 `<invoke>` 标签 | — |
| **失败反思** | `fail_counts[tool:args_fingerprint]` 连续 ≥2 次触发 verification gate | — |
| **ToolFailureReflector** | JSON Schema 校验失败时自动修复，design_workflow 专用策略含 depends_on 模糊匹配 | — |
| **Plan-and-Execute** | design_workflow 成功后写 `session["current_plan"]`，下轮注入 system prompt | — |
| **Skill 选择+锁定** | 子串计数 match_score + session 锁定 + 3 级漂移检测 | — |
| **三层 Guardrails** | AfterToolHook(工具结果) + OutputGuardrail(LLM 输出) + 焊接领域规则，默认 WARN | 5 条焊接规则 |

**两种执行模式**:
- `run()`: 同步模式，完整 ReAct 循环 → `InteractionResponse`
- `run_stream()`: 异步生成器，SSE 逐事件推送(thinking → tool_call → tool_result → token → final)

**关键常量**: `max_iterations=25`、`TOOL_TIMEOUT_SECONDS=180`、`APPROVAL_TIMEOUT_SECONDS=300`。

### AgentLoop — WebSocket 生命周期管理

**文件**: `cognitiveplane/control/agent_loop.py` (570 行)

双任务模型:
- `receive_task`: 消费 `_incoming` queue，分发 chat/feedback/interrupt
- `react_task`: 按需启动跑 ReActEngine `run_stream()`，同一时刻仅一个

三种消息类型: chat 启动 react_task、feedback 写入 Memory + 队列化 FeedbackSummary、interrupt 直接取消当前 react_task。

与 HTTP `/stream` 路径共享 hooks、skill_registry、approval_store、context_compactor、guardrails。

### ToolRegistry — 工具注册表

**文件**: `cognitiveplane/control/registry/tool_registry.py` (293 行)

注册了 **18 个工具**，按 `phase` 控制 LLM 可见性(`current_phase=3`)。

| 工具 | Phase | 类型 | 功能 |
|------|-------|------|------|
| `search_standards` | 1 | Tier-A | 查询焊接标准(NB/T47014, GB/T3323 等) |
| `search_cases` | 1 | Tier-A | 检索历史缺陷案例 |
| `search_process` | 1 | Tier-A | 查询焊接工艺参数(GMAW/GTAW/SMAW) |
| `search_vision_knowledge` | 1 | Tier-A | 查询视觉知识库 |
| `search_reasoning_knowledge` | 1 | Tier-A | 查询推理知识库 |
| `read_weldmap` | 1 | Tier-A | 读取当前案例 WeldMap 状态 |
| `explain_decision` | 1 | Tier-A | 解释过往决策 |
| `archive_memory` | 1 | Tier-A | 保存到操作员记忆 |
| `web_search` | 1 | Tier-A | DuckDuckGo 网络搜索 |
| `request_confirmation` | 1 | — | 请求用户确认 |
| `escalate` | 1 | Tier-B | 升级到人工操作员 |
| `analyze_image` | 2 | Tier-A | 多模态焊缝图像分析(Volc Doubao Vision Pro) |
| `analyze_dataset` | 2 | subagent | 数据集级分析(统计+语义) |
| `design_workflow` | 3 | Tier-B | 生成 WorkflowSpec DAG |
| `launch_workflow` | 3 | Tier-B | 提交 WorkflowSpec 到 L2 Temporal |
| `control_workflow` | 3 | Tier-B | 查询/暂停/恢复/取消工作流 |
| `upload_image_to_dataset` | 3 | Tier-B | L1 包装工具：image_ref → base64 → MCP upload_images |
| `delegate` | 3 | Tier-B | 委派子任务到 subagent |

MCP 标注工具(11 个)通过 ToolPolicy A/B/C 三级分级控制可见性，phase_override=3。

### Chat API — 双路径接入

**12 个端点**:

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/v1/chat/` | POST | JSON 同步聊天 |
| `/api/v1/chat/upload` | POST | multipart 文件上传聊天 |
| `/api/v1/chat/stream` | POST | SSE 流式聊天 |
| `/api/v1/chat/ws` | WS | AgentLoop WebSocket 双向通信 |
| `/api/v1/chat/cancel` | POST | 取消 Temporal 工作流 |
| `/api/v1/chat/workflow/events` | POST | L2→L1 事件入口 |
| `/api/v1/chat/workflow/status` | POST | 查询工作流状态 |
| `/api/v1/chat/workflow/stream/{session_id}` | GET | 工作流事件 SSE |
| `/api/v1/chat/approve` | POST | 审批决议 |
| `/api/v1/chat/sessions` | GET | 列出所有 session |
| `/api/v1/chat/sessions/{id}/export` | GET | 导出对话历史 |
| `/api/v1/chat/sessions/{id}/trajectory` | GET | 导出执行轨迹 |

HTTP `/stream` 与 WS `/ws` 共享全部治理组件(hooks/skills/approval/compactor/guardrails)，两路径行为一致。

### 长路径观察者 (WorkflowObserver)

- **WorkflowEventBus**: publish/subscribe 模式，subscribe 多 queue 支持多标签页，历史缓存(limit 50)供新订阅者回看
- **WorkflowObserver**: 订阅 publish hooks，仅 3 类 KEY_EVENTS(workflow_completed/failed/paused)推 NotificationStore
- **NotificationStore**: 5 类通知(CONFIRMATION_REQUEST/ESCALATION/ALERT/INSTRUCTION/WORKFLOW_UPDATE)，前端 WebSocket 实时推送

---

## L2 控制平面 (controlplane)

L2 是 Temporal 驱动的 DAG 工作流执行引擎。接收 L1 产出的 WorkflowSpec，按拓扑排序执行节点 activity，管理重试、HumanGate、暂停/恢复/取消等生命周期。治理状态通过**事件溯源**（Event Sourcing）管理：治理动作追加事件，状态从事件投影 reduce，支持回溯 `project(up_to=N)` 任意历史时刻。

### RunWorkflowSpec — 核心工作流

**文件**: `controlplane/runtime/dag_runner_workflow.py` (~2634 行)

这是 L2 的核心——唯一的 Temporal workflow 实现。由 `worker.py` 注册到 `control-plane` task queue。

**主入口 `run()`**:
1. 解析 WorkflowSpec → 拓扑排序(Kahn 算法) → 分层并行执行
2. 每层内节点通过 `_process_single_node` → `_execute_node_v2`(四阶段执行)
3. 四阶段: PREPARE(构建 input + 幂等键) → EXECUTE(调 activity) → VALIDATE(schema 校验 + LLM 语义评估) → COMMIT(存储结果 + 审计记录)

**4 种 on_failure 策略**: abort(终止工作流)、escalate(暂停等人)、retry(重试≤3 次，超限进 DLQ)、continue(标记失败继续下游)。另: rework 是独立 signal(rework_node), 非 on_failure 策略

**Temporal 查询**: `query_status()` 暴露完整工作流状态(已完成/失败/mock 节点、token 预算、DLQ、暂停状态、审查分配)

**Temporal Signal(共 20 个 action)**:

| 类别 | Signal | 功能 |
|------|--------|------|
| **审查** | `human_review` / `human_gate` | 5 向决策(approve/rework/modify_downstream/reject/escalate/override) |
| **审批撤回** | `revoke_approval` | 撤回已提交节点的审批 |
| **批量** | `batch_hold` / `release_hold` / `rework_batch` / `quarantine_batch` | 批级停牌/解除/重做/隔离 |
| **暂停** | `pause_scope` / `resume_scope` | 三级暂停(工作流/工位/批次) |
| **标签修正** | `relabel_request` | 仅修正标签，不重跑昂贵分析 |
| **审查委派** | `delegate_review` | 转派审查人 |
| **覆盖** | `ground_truth_override` | 人工强制覆盖机器判定 |
| **上下文注入** | `inject_context` | 执行中注入补充信息 |
| **节点重做** | `rework_node` | BFS 找下游 + 清除结果 + 重跑 |
| **规格修改** | `modify_spec` | 执行中修改工作流规格 |
| **生命周期** | `pause` / `resume` / `cancel_by_user` | 全局暂停/恢复/取消 |
| **批量信号** | `batch_signals` | 批量应用多个信号 |

**Activity RetryPolicy**: `maximum_attempts=1` (事件回传不重试, 事件推送失败不阻塞工作流)。瞬时故障(ConnectionError/OSError/TimeoutError)重新抛出由 Temporal 重试，业务错误返回 ERROR 不重试。

**关键特性**:
- **P0-2 Mock 安全**: 所有 mock activity 返回 `MARGINAL` + `mock=True`，绝不返回 `OK`，防止假通过
- **Node State Machine**: 每个节点有独立 `NodeStateMachine`(9 状态, 18 事件)，shadow-first 迁移: 镜像列表状态 + 软断言，硬门 pause_scope + PENDING 守卫
- **Saga 补偿** (Op 21): 节点失败时按逆序执行已记录补偿动作
- **Dead Letter Queue** (Op 24): 重试 3 次后隔离，下游取默认值不阻塞
- **Token 预算** (Op 25): 累计追踪，80%预警，100%标记下游降级
- **审计追踪** (Op 27): 每个信号/审查/覆盖/暂停/撤回产生不可变 `DecisionRecord`
- **治理事件溯源** (阶段 1-3): 6 个治理状态字段（paused_nodes / paused_batches / held_batches / injected_context / review_results / rework_nodes）全部事件驱动。治理模块只 append 事件 + refresh 缓存，不再直接 mutate 字段。`supersedes` 链自动处理 pause->resume / hold->release / rework->completed。投影 `project(up_to=N)` 支持回溯任意历史时刻的完整治理状态
- **Self-Refine Cycle** (Op 33): 验证阶段跟踪质量分数趋势，检测收敛或恶化

### Worker — Composition Root

**文件**: `controlplane/worker.py` (171 行)

单文件启动入口，连接 Temporal Server(最多 5 次指数退避重试)，注入 L3 ActivityPool(推导 import)、注入 LLM 评估 Provider、注册 `RunWorkflowSpec` + 11 个 activity(3 DAG + 8 mock)、注册 SIGINT/SIGTERM 优雅关闭。

**关键**: L2 不静态依赖 L1 或 L3，跨层依赖在 `worker.py` 运行时通过 `try/except` 解析，实现端口/适配器隔离。

---

## 工作流治理层 (Governance Layer)

治理层是 L2 的核心差异化能力--让用户通过**自然语言**介入正在执行的工作流，实现中断、回溯、冻结、覆盖等 8 类干预。设计借鉴 Magentic-One 的 check-in 机制 + Temporal 的 Reset/Signal 机制，但覆盖面更广。治理状态通过**事件溯源**（Event Sourcing）管理，支持回溯任意历史时刻的完整治理状态。

### 设计理念: 自然语言 -> Agent 理解 -> Signal 执行

```
用户在主界面说："这个批次可疑，扣住别动"
  ↓ agent 读 system prompt 的意图映射表
  ↓ agent 调 control_workflow(action="batch_hold", batch_id=...)
  ↓ WorkflowControlTool 经 batch_signals signal 转发
  ↓ workflow 的 _apply_pending_signals router 路由到 _batch_hold 模块
  ↓ 执行隔离 + 审计留痕
  ↓ agent 回话："已扣留批次 B-12，其他批次继续"
  ↓ execPanel 实时反映（纯展示，不操作）
```

**两档确认门**: 第一档（可逆/低责任）agent 直接发 signal；第二档（不可逆/责任敏感）架构自动弹确认卡片，用户显式确认后才发 signal，保证责任留痕可审计。

### 8 个治理模块

| 模块 | Signal | 档位 | 功能 | 技术来源 |
|------|--------|------|------|----------|
| `pause_scope` | `pause_scope` | T1 | 分级暂停（工位/批次/工作流），其余继续 | Magentic-One check-in |
| `batch_hold` | `batch_hold` | T1 | 批次冻结，隔离结果不阻塞其他批次 | Temporal fence |
| `relabel_request` | `relabel_request` | T1 | 标签错但结果对，只回标注环节 | rework_node 标签专项版 |
| `delegate_review` | `delegate_review` | T1 | 审查权转移，改 human_review 的审查人 | Temporal signal |
| `standard_update` | `standard_update` | T1 | 标准库版本升级，标记历史判定待复查 | AWS D1.1/ISO 5817 |
| `revoke_approval` | `revoke_approval` | **T2** | 撤回已批准决策，BFS 下游重跑 | Temporal Reset |
| `ground_truth_override` | review-gate override | **T2** | 质检员强制覆盖机器裁决，保留 machine_verdict | 人工校准 |
| `case_library_correction` | `case_library_correction` | **T2** | CBR 案例库纠错，波及未来引用 | CBR self-correction |

**T2 确认门**: revoke_approval / ground_truth_override / case_library_correction 三个不可逆操作，agent 调用时架构自动经 `ApprovalStore` 阻塞，弹确认卡片等用户拍板。审计记录"用户显式确认"而非"LLM 理解"，责任链清晰。

**文件**: `controlplane/runtime/dag_runner_workflow.py` (8 个 `_*` 私有方法 + `@workflow.signal` 入口) + `cognitiveplane/control/tools/workflow_control.py` (NL -> signal 转发) + `cognitiveplane/control/engine/system_prompt.py` (意图映射表)

### 节点状态机

**文件**: `controlplane/runtime/node_state_machine.py`

9 状态 / 18 事件的显式状态机，借鉴 Temporal 的工作流状态模型:

```
PENDING -> PREPARING -> EXECUTING -> COMMITTED
                                \-> FAILED
                                \-> AWAITING_REVIEW -> COMMITTED
PAUSED (可从 EXECUTING 进入)
HELD (batch_hold 隔离)
REVOKED (revoke_approval 终态)
```

**运行模式**: shadow-first 迁移策略--状态机镜像列表状态 + 软断言（供测试硬断言），逐步切硬强制。当前硬门: `pause_scope` 的 `_check_node_pause`（`while + wait_condition` 真阻断暂停节点）+ PENDING 守卫（幂等防非法 START）。

### 治理事件溯源 (Governance Event Sourcing)

**文件**: `cognitiveplane/audit/governance_event.py` + `controlplane/runtime/dag_runner_workflow.py`

治理状态从"可变字段直接 mutate"改为"追加事件 -> 投影 reduce"。这是解决回溯叠加问题（C8-C13: 回溯与暂停/并行/扇出/成本/输入漂移叠加）的根因方案。

**核心机制**:

```
治理动作 (pause/hold/rework/review/...)
  ↓ _gov_append() 追加 GovernanceEvent 到事件流
  ↓ _refresh_governance_cache() 从 project() 刷新 6 个字段
  ↓ 字段降级为投影的缓存 (投影是执行依据)
```

**`supersedes` 链**: resume 不删旧 pause，而是追加事件声明作废旧 pause。投影时按 supersedes 链过滤已作废事件。事件流只增不改（审计完整）。

**6 个事件化字段**:

| 字段 | 事件类型 | supersedes 机制 |
|------|---------|----------------|
| `_paused_nodes` | PAUSE_SCOPE / RESUME_SCOPE | resume supersedes pause |
| `_paused_batches` | PAUSE_SCOPE / RESUME_SCOPE | resume supersedes pause |
| `_held_batches` | BATCH_HOLD / RELEASE_HOLD / REWORK_BATCH / QUARANTINE_BATCH | release/rework/quarantine supersedes hold |
| `_injected_context` | INJECT_CONTEXT | 累积 (key 覆盖) |
| `_review_results` | HUMAN_REVIEW | 覆盖 (同 node_id) |
| `_rework_nodes` | REWORK_NODE / REWORK_NODE_COMPLETED | completed supersedes rework |

**`node_interventions`**: 投影显示每个节点当前受哪些 active 事件影响，解决 C12 叠加可见性（pause + rework 同一节点，两个 set 互不知道的问题）。

**回溯能力**: `project(up_to=N)` 从事件流 reduce 出历史某时刻的完整治理状态。支持 A/B 对比（两个投影快照 diff）和历史重放。

**Shadow 校验**: `_assert_governance_consistency()` 在 `_build_result` 中比对投影 vs 字段，mismatch 只记 warning 不阻断。结果暴露 `gov_mismatches` 供测试硬断言。

**技术来源**: 不引入 Restate/LangGraph/OpenAI RunState（它们解决单状态模型，不解决多治理状态叠加）。事件溯源在已有 AuditTrail 基建上实现，语义层解决叠加问题。

#### 中断与回溯机制

治理事件溯源使中断和回溯从"直接改字段"变为"追加事件 + 投影"：

| 机制 | 事件流行为 | 回溯支持 |
|------|-----------|---------|
| **分级暂停** (pause_scope) | PAUSE_SCOPE -> RESUME_SCOPE (supersedes) | project(up_to=N) 还原任意时刻暂停状态 |
| **批次冻结** (batch_hold) | BATCH_HOLD -> RELEASE_HOLD/REWORK_BATCH/QUARANTINE_BATCH (supersedes) | 回溯看到批次何时被冻结/释放 |
| **节点重做** (rework_node) | REWORK_NODE -> REWORK_NODE_COMPLETED (supersedes) | 回溯看到节点何时被标记重做/完成重做 |
| **撤回批准** (revoke_approval) | REVOKE_APPROVAL 事件 | 回溯看到决策何时被撤回 |
| **人工覆盖** (ground_truth_override) | GROUND_TRUTH_OVERRIDE + HUMAN_REVIEW 事件 | 回溯看到机器裁决 vs 人工覆盖 |
| **标签修正** (relabel_request) | RELABEL_REQUEST + REWORK_NODE 事件 | 回溯看到标签何时被修正 |
| **审查委派** (delegate_review) | DELEGATE_REVIEW 事件 | 回溯看到审查权何时转移 |
| **上下文注入** (inject_context) | INJECT_CONTEXT 事件 (累积) | 回溯看到上下文何时被注入 |
| **标准更新** (standard_update) | STANDARD_UPDATE 事件 | 回溯看到标准版本何时升级 |
| **案例纠错** (case_library_correction) | CASE_LIBRARY_CORRECTION 事件 | 回溯看到案例何时被修正 |

**叠加处理**（解决 C8-C13）：`node_interventions` 投影显示每个节点当前受哪些 active 事件影响。pause + rework 同一节点时，两个事件都 active，不再"两个 set 互不知道"。

**回溯使用**：
- `project(up_to=N)` -- 还原第 N 个事件时刻的完整治理状态
- `project()` -- 当前完整治理状态
- 两个投影 diff -- A/B 对比（如"如果没撤回会怎样"）
- 事件流只增不改 -- 审计完整，不可篡改

#### 工作流版本更迭 (Spec Versioning)

**文件**: `cognitiveplane/control/planner/versioning.py` + `controlplane/runtime/dag_runner_workflow.py`

工作流执行中修改 spec 时，自动区分变更类型并管理版本链：

| 变更类型 | 判定逻辑 | 处理方式 |
|---------|---------|---------|
| **参数级变更** (modify_params) | 节点 ID 不变、依赖不变、只改 input/on_failure/review_policy | 热更新未执行节点，不 cancel |
| **拓扑变更** (add_node) | 新增节点 | cancel + relaunch，保留已完成节点 |
| **拓扑变更** (remove_node) | 删除节点 | cancel + relaunch |
| **拓扑变更** (reorder) | 依赖关系改变 | cancel + relaunch |

**版本注册表** (PlanVersionRegistry)：
- workflow 启动时创建初始 FROZEN 版本 (`pv_1`)
- 拓扑变更时走 `propose_revision` + `approve_proposal`，创建新版本 (`pv_2`)，旧版本 `superseded`
- 参数级变更不创建新版本（spec 结构未变）
- `plan_version` + `plan_version_history` 暴露到 `_build_result`

**SPEC_REVISION 事件**：每次 spec 版本更迭追加 `SPEC_REVISION` 事件到治理事件流，记录 spec hash。`project(up_to=N)` 可还原任意时刻用的是哪个版本的 spec。

**借鉴来源**：Temporal Update API (validator + handler 飞行中修改) + Temporal patched() (版本感知分支) + 数据库迁移模式 (migrate old->new) + 调研报告 §7.2 (PlanRevisionProposal 影响分析)

### Heartbeat 流式进度

**文件**: `controlplane/runtime/dag_runner_workflow.py` `_emit_progress`

节点执行各阶段边界报进度到 `streaming_updates` (prepare 0.2 / execute 0.6 / validate 0.85, commit 1.0 走 dispatch 直连)，前端 execPanel 实时展示:
- PREPARE 完成 -> 0.2
- EXECUTE 完成 -> 0.6
- VALIDATE 完成 -> 0.85
- COMMIT 完成 -> 1.0

`query_status()` 暴露 `current_node` / `current_stage` / `current_progress`，供 L1 和前端实时查询。

### Embedding / CBR 案例检索

**文件**: `cognitiveplane/knowledge/adapters/chroma_rag.py`

`ChromaRAGAdapter` 实现 `CaseLibraryQueryPort`（第 3 个 Chroma collection `weld_cases`），5 条焊缝 CBR 种子案例。经 `SearchCasesTool` 注册为 agent 工具，agent 可经自然语言检索历史案例。`case_library_correction` 治理模块扫描 audit_trail 的 `case_id`，标记受影响的历史决策。

### Prompt Caching / Per-Purpose 模型路由

**文件**: `cognitiveplane/capability/openai_provider.py` + `config.py`

- **per-purpose 模型路由**: `LLMConfig.resolve_model(purpose)` 按 purpose 选模型（reasoning/classification/explanation/embedding/vision）
- **prompt-prefix caching**: react.py 从 `CACHE_BOUNDARY` 标记计算 `cache_prefix_tokens`，`_build_messages` 按后端门控注入（Claude 注入 `cache_control`，DeepSeek 走服务端自动缓存）
- **成本追踪**: `LLMCallTracker.query_by_purpose` 按 purpose 聚合 token/成本/延迟

---

## L3 执行平面 (executionplane)

L3 是工业视觉算法的执行层。通过 ActivityPool 将字符串 capability 映射到具体 activity 实现。

### ActivityPool

**文件**: `executionplane/pool.py` (236 行)

9 个 capability 注册(3 真实 + 6 mock)，别名映射(`defect_detection → iqa`、`preprocess → ppa`、`label_studio → annotation`)。

```python
pool = create_default_pool()  # 注册 IQA + PPA + Annotation
result = await pool.dispatch({"capability": "iqa", "workflow_id": "...", "input": {...}})
```

### IQA — 图像质量评估

**文件**: `executionplane/activities/iqa/activity.py` (684 行)

**4 项确定性 CV 检查**(纯 numpy，~10ms):

| 检查 | 算法 | 阈值 |
|------|------|------|
| 分辨率 | `min(w, h)` | ≥ 2048px (默认 macro_weld) |
| 曝光 | 灰度均值 | [45, 90] |
| 对焦 | Laplacian 方差 | ≥ 50 |
| 完整性 | 暗区占比(焊缝区域) | ≥ 30% |

**路由决策**: 置信度加权 4 项指标 → AUTO_PASS / SUGGEST_REVIEW / MANDATORY_REVIEW / REJECT。

**MLLM 深度视觉**(条件触发，1-3s): 置信度低于 0.85 时调 Doubao Vision Pro 检查遮挡/镜头污染/异物/反光/异常纹理。发现异常至少降级到 SUGGEST_REVIEW。

**WeldMap 写入**: `image/quality` → ImageQualityReport(含 resolution/exposure/focus/completeness 四项 + route_decision + confidence)

### PPA — 图像预处理

**文件**: `executionplane/activities/ppa/activity.py` (477 行)

从 WeldMap(优先)或 dependency_results(回退)读取 IQA 结果 → 按问题映射预处理策略 → 执行预处理。

**10 种预处理操作**: CLAHE 对比度增强、Gamma 校正、去噪(fastNlMeansDenoising → scipy uniform_filter)、锐化(3x3 核)、边框填充、自动色阶、亮度抑制、反光移除(中值滤波)、遮挡修复(cv2.inpaint)、伪影清理(morphological opening)。

每种操作都有 cv2(首选) + 纯 numpy/scipy(回退)双路径实现。

**WeldMap 写入**: `image/preprocess` → 策略列表 + 问题检测 + IQA 置信度

### Annotation — Label Studio MCP 标注

**文件**: `executionplane/activities/annotation/activity.py` (637 行)

**11 个 action**: list_datasets、get_dataset、create_job、list_jobs、get_job、create_task、list_tasks、trigger_ai、upload_images、assign_task、auto_annotate(复合 6 步链路)。

**version_id 4 级回退**: params.version_id → dataset_id+get_dataset → dependency_results 上游 → list_datasets 取第一个。

### Mock Activities

MEA/RDA/VDA/RVA/MTA/HCA 未实现，L2 `execute_node` 检查 `ActivityPool.supports()` 后走 `_CAPABILITY_DISPATCH` 表回到 mock 路径。Mock activity 返回 `MARGINAL + mock=True`，绝不返回 `OK`。

### WeldMap — 跨 Activity 状态黑板

**文件**: `executionplane/weldmap/` (220 行 in_memory + models + client)

**6 个域**: `image/quality`(IQA)、`image/preprocess`(PPA)、`mask`、`annotations`、`validation`、`decision`

**分布式设计**: CAS 乐观锁(`write_path` 带 `expected_version`，冲突返回 `False`)、Event Sourcing(WeldMapEvent 不可变写事件)、层级路径(`weldmap:///{workflow_id}/{domain}/`)

**实现**: InMemoryWeldMapClient(开发/测试完整，含 `asyncio.Lock` 保护 CAS 原子性)

### QualityStandard Registry

**文件**: `executionplane/config/quality_standard.py` (574 行)

定义所有质量阈值(分辨率/曝光/对焦/完整性/路由/深度视觉/权重)的数据类。4 个预设标准: `macro_weld`(默认)、`micro_metallography`(高分辨率)、`high_speed_line`(低分辨率宽松)、`night_inspection`(低光照)。支持 YAML/JSON 加载、tag 索引、父子标准继承。

---

## Bridge 桥接层

### L1→L2 Bridge

**文件**: `cognitiveplane/bridge/` (4 文件, ~760 行)

```
design_workflow → WorkflowSpec → EventConnector → WorkflowLauncher → Temporal Client
```

- `EventConnector.on_workflow_spec(spec)`: 接收 L1 产出的 WorkflowSpec → 通知 WeldMap gateway → WorkflowLauncher → TemporalWorkflowLaunchPort.submit → `client.start_workflow("RunWorkflowSpec")`
- 控制能力: `query_status` / `send_human_gate_signal` / `send_signal`(pause/resume/cancel) / `cancel_workflow`
- 默认 NullWorkflowLaunchPort(仅记录，开发/测试)，生产 TemporalWorkflowLaunchPort

### L2→L3 Bridge

```
execute_node activity → ActivityPool.resolve(capability) → Activity.execute(input)
```

L2 的 `execute_node` activity 通过 `configure_activity_pool()` 注入 L3 ActivityPool。`tool_task` 节点: `_activity_pool.dispatch()`。

### L2→L1 Event Relay

```
L2 节点事件 → HTTP POST /api/v1/chat/workflow/events → WorkflowEventBus.publish → 前端 SSE
```

`emit_workflow_event` activity 通过 `httpx.AsyncClient` POST 到 L1 callback URL。WorkflowEventBus 支持多 SSE 订阅者并发推送。

---

## Shared 共享基础设施

**文件**: `shared/` + `cognitiveplane/shared/` (915 + 3,027 行)

### shared/ — 跨层传输层

| 模块 | 内容 | 行数 |
|------|------|------|
| `shared/mcp/protocol.py` | JSON-RPC 2.0 消息构造/解析、ToolDescriptor、ToolCallResult | 207 |
| `shared/mcp/client.py` | MCPClient ABC + StdioMCPClient + HTTPMCPClient(含 SSE 支持) | 495 |
| `shared/labelstudio/auth.py` | JWT 登录 | 56 |
| `shared/labelstudio/config.py` | Label Studio 连接配置 | 58 |

### cognitiveplane/shared/ — L1 领域模型

| 模块 | 内容 | 行数 |
|------|------|------|
| `shared/enums.py` | 44 个枚举类(239 成员值) | 405 |
| `shared/types.py` | 12 个 ID 类型(Pydantic) | — |
| `shared/dto/` | 10 个子模块: context/collaboration/decision/escalation/gateway/knowledge/learning/memory/validation/weldmap_events | 954 |
| `shared/dto_decision/` | BrainDecision + 15 类 DecisionOutputContent + value objects | 472 |
| `shared/dto_workflow.py` | WorkflowSpec DTO(与 L2 独立镜像，SSOT 校验和保持同步) | — |
| `shared/ports/` | 7 个子模块: 26 ABC 端口接口 | 989 |

`shared/` 与 `cognitiveplane/shared/` 的设计边界: 前者只放纯传输/协议代码(L1/L3 共享)，后者是 L1 的领域模型。L1 adapters/mcp 适配 shared HTTPMCPClient 但不共享业务封装。

---

## 治理与安全

> **工作流治理层**（8 治理模块 + 状态机 + heartbeat + NL->signal 链）见[上方专节](#工作流治理层-governance-layer)。本节聚焦 L1 侧的护栏 / 验证 / 棘轮 / 权限。

### 三层护栏系统

**文件**: `cognitiveplane/governance/guardrails.py` (276 行)

| 层 | 抽象 | 焊接实现 | 规则数 |
|---|------|---------|--------|
| **BeforeTool** | SafetyHook + PolicyHook | 参数/权限校验 | 由 hooks.py 处理 |
| **AfterTool** | AfterToolHook | 工具返回危险参数(电流>500A、预热<100°C) | 2 条 |
| **Output** | OutputGuardrail | LLM 输出违规(绕过国标、跳过检测、伪造参数) | 3 条 |

**动作**: PASS/WARN/REJECT/RETRY。**默认 WARN 不阻断**，生产环境可按需调 REJECT。

### 四级验证管线

**文件**: `cognitiveplane/gateway/pipeline.py` (312 行)

Safety → Rule → Shadow → Consistency。Safety BLOCK → ESCALATED，Rule REJECT → REJECTED。Shadow CRITICAL → ESCALATED，INCONSISTENT → REQUIRES_REVIEW。

**注意**: ValidationPipeline 已构造(bootstrap/dependencies.py:93)但未接入 ReActEngine 主循环的决策流程。

### PolicyRatchet — 棘轮机制

**文件**: `cognitiveplane/governance/policy_ratchet.py` (206 行)

Append-only YAML 草稿存储，记录 schema_fail/reask_reject/timeout/cas_conflict 触发事件。不自动 GC，错误记录不阻断主循环。

### RBAC 权限

**文件**: `cognitiveplane/governance/permissions.py`

`StaticRBAC` 实现 `PermissionPort.check()`。三角色: inspector(只读)、engineer(读+设计+确认)、safety(提级+覆盖)。

---

## 评估框架

### 在线评估 (LLM-as-a-Judge)

**文件**: `cognitiveplane/governance/evaluation.py` (382 行)

每次对话结束后后台异步打分，4 维度:
- **helpfulness**: 是否完整回答了用户问题
- **safety**: 是否有违反焊接规程的建议(1=完全安全)
- **tool_efficiency**: 工具调用是否必要、是否有重复
- **grounding**: 回复是否基于国标/WeldEvent 数据

**接入**: `chat.py` → `Evaluator.evaluate_background()` → `WeldingLLMJudge.evaluate()` → `LangfuseScoreRecorder.record()` 挂分到 Langfuse trace。

**P1-8 修复**: 支持截断 JSON 修复(`_repair_truncated_json`)，max_tokens 从 512 升级到 1024。

### 离线回归测试 (GOLDEN_SET)

**文件**: `cognitiveplane/governance/eval_dataset.py` (620 行)

46 个用例，5 类场景: 正常工艺咨询(10 条)、危险建议拦截(9 条)、违规参数拦截(8 条)、国标查询(9 条)、缺陷诊断(10 条)。

```bash
python -m cognitiveplane.governance.eval_dataset
```

**注意**: GOLDEN_SET 的 `reply` 是预写静态字符串，实际不跑 Agent。Judge 只是对两个静态字符串做比较打分。要测试 Agent 的真实行为(工具选择、skill 命中)，需要扩展 EvalCase 设计。

### 零成本执行后评估

**文件**: `cognitiveplane/capability/evaluation.py` (约 100 行)

工作流执行完成后，`launch_workflow.py` 调 `ZeroCostEvaluator.evaluate()` 对本次执行质量打分(基于 node 完成状态和数据丰富度)。

---

## 前端

**文件**: `cognitiveplane/static/chat.html` (3,694 行)

单文件 HTML 应用，无外部 JS 依赖。深色主题，三栏布局(侧边栏 + 主聊天区 + 执行监控面板)。

**核心能力**:
- **流式渲染**: SSE 事件驱动，打字机效果，局部 DOM 更新(非全量重渲染)
- **工具调用可视化**: 中文可读标签(如"正在分析图片...")、状态圆点
- **审批卡片**: 按 tool_name 动态文案(8 个工具)，approve/reject + 多选项对话框 + 自定义反馈
- **执行监控面板**: SVG DAG 渲染(节点状态色: 待执行/运行中/完成/失败)、step 详情卡片、进度统计
- **WebSocket 连接**: 优先 WS /ws，失败 fallback HTTP /stream
- **打断/取消**: 前端 AbortController 中断 SSE + POST /cancel 终止 Temporal
- **Session 管理**: localStorage 持久化 session 列表、日期分组
- **文件上传**: 图片/PDF/ZIP/DOCX/Excel/CSV，预览条带

---

## 技术栈

| 层 | 技术 | 版本 |
|---|------|------|
| L1 推理 | FastAPI + asyncio | — |
| L1 LLM | DeepSeek-Chat (32K ctx) + Volc Doubao Vision Pro 32K | — |
| L1 前端 | 原生 HTML/CSS/JS | 单文件, 3,694 行 |
| L1 追踪 | Langfuse SDK v3 (可选) | 无 key 时 NoOp 降级 |
| L2 编排 | Temporal (gRPC) | 1.27+ |
| L3 视觉 | OpenCV 4.9+ / NumPy 1.26+ / Pillow 10+ / SciPy 1.11+ | 均为可选依赖 |
| L3 标注 | Label Studio MCP (远程 HTTP) | v2.0 |
| 跨层 | Pydantic 2.0+ / httpx 0.27+ | - |
| L2 治理 | 9 状态机 + 8 governance signal + heartbeat 流式 + 事件溯源 | shadow-first 迁移 |
| L1 检索 | ChromaDB (3 collection: vision/reasoning/cases) | CBR 案例库 |
| L1 缓存 | per-purpose 模型路由 + prompt-prefix caching | DeepSeek 服务端自动 / Claude cache_control |

**关键设计决策**:
- L1 和 L2 各自维护独立 `pyproject.toml`，通过 dict 序列化过边界
- OpenCV/SciPy 为可选依赖，核心功能仅需 NumPy
- MLLM 调用独立于 ReAct 主循环，避免视觉分析占用推理上下文

---

## 项目结构

```
WeldEvent/
├── cognitiveplane/          # L1 认知平面 (33,842 行)
│   ├── app.py               # FastAPI 入口 (240 行)
│   ├── bootstrap/            # 依赖装配 (4 文件)
│   ├── control/
│   │   ├── engine/           # ReActEngine 核心 (6 文件)
│   │   │   ├── react.py      # 核心循环 + 三级回退 (1,622 行)
│   │   │   ├── approval.py   # ApprovalGate 架构阻塞
│   │   │   ├── system_prompt.py # SystemPrompt 构建
│   │   │   ├── tool_execution.py # 工具执行+校验+钩子
│   │   │   └── session_notes.py  # 23 工具 session notes (L1+MCP)
│   │   ├── registry/         # 工具/MCP 注册表 (5 文件)
│   │   ├── tools/            # 18 个 L1 工具实现 (含 control_workflow governance)
│   │   ├── agent_loop.py     # WebSocket 生命周期 (570 行)
│   │   ├── skills.py         # Skill 匹配和锁定
│   │   ├── hooks.py          # SafetyHook + PolicyHook
│   │   ├── event_log.py      # 13 类事件溯源
│   │   ├── trajectory.py     # 对话轨迹记录
│   │   ├── subagent.py       # SubAgent 委派
│   │   ├── declaration.py    # Agent 声明加载 (TOML/MD)
│   │   ├── planner/          # 规划器 (Magentic-One, 4 文件)
│   │   └── data_understanding/ # 数据集分析 (4 文件)
│   ├── interaction/api/      # Chat API (7 文件)
│   ├── audit/                # DecisionRecord (8 治理决策类型)
│   ├── bridge/               # L1-L2 桥接 (3 文件)
│   ├── governance/           # 治理层
│   │   ├── evaluation.py     # LLM-as-a-Judge (381 行)
│   │   ├── eval_dataset.py   # GOLDEN_SET 46 用例 (537 行)
│   │   ├── guardrails.py     # 三层护栏 (276 行)
│   │   ├── policy_ratchet.py # 棘轮机制
│   │   ├── approval.py       # 审批服务
│   │   ├── escalation.py     # 提级追踪器
│   │   ├── review.py         # 人工审查流程
│   │   ├── permissions.py    # RBAC
│   │   └── validators/       # 四级验证器
│   ├── capability/           # LLM Provider + 缓存
│   ├── memory/               # 分层记忆系统
│   ├── knowledge/            # 知识库 + Chroma RAG
│   ├── gateway/              # WeldMap Gateway
│   ├── adapters/             # 可观测性 + DB Adapter
│   ├── bootstrap/            # App 启动装配
│   ├── shared/               # L1 领域模型 (3,027 行)
│   │   ├── enums.py          # 44 枚举类
│   │   ├── types.py          # 12 ID 类型
│   │   ├── dto/              # 领域 DTO (10 子模块)
│   │   ├── dto_decision/     # BrainDecision
│   │   └── ports/            # 26 端口 ABC
│   └── static/
│       └── chat.html         # 前端 (3,694 行)
├── controlplane/             # L2 控制平面 (4,292 行)
│   ├── worker.py             # Composition Root (171 行)
│   ├── config.py             # 配置
│   ├── domain/
│   │   ├── activity.py       # ActivityStatus/Input/Output
│   │   └── workflow_spec.py  # WorkflowSpec + 拓扑排序
│   ├── adapter/
│   │   ├── dag_activities.py # execute_node 等 3 个 activity (433 行)
│   │   └── mocks.py          # 8 个 mock activity
│   └── runtime/
│       ├── dag_runner_workflow.py # RunWorkflowSpec (3,092 行)
│       └── node_state_machine.py  # 9 状态 18 事件 FSM
├── executionplane/           # L3 执行平面 (6,371 行)
│   ├── pool.py               # ActivityPool
│   ├── activities/
│   │   ├── base.py           # BaseActivity ABC
│   │   ├── contracts.py      # Activity 契约 (端口隔离)
│   │   ├── iqa/              # 图像质量评估 (683 行)
│   │   ├── ppa/              # 图像预处理 (477 行)
│   │   └── annotation/       # Label Studio MCP 标注 (637 行)
│   ├── capabilities/         # 算法实现 (CV 规则/预处理器/MLLM)
│   ├── weldmap/              # WeldMap 黑板 (CAS + Event Sourcing)
│   ├── interface/            # 用户 API (同步/异步)
│   ├── config/               # QualityStandard Registry
│   ├── integrations/         # Label Studio MCP Client
│   └── examples/             # 使用示例
├── shared/                   # 跨层共享 (915 行)
│   ├── mcp/                  # MCP 传输层
│   └── labelstudio/          # Label Studio 认证
└── docs/                     # 设计文档
    ├── ARCHITECTURE.md       # 架构流程图
    ├── CODEBASE_ANALYSIS.md  # 代码盘点
    └── WeldEvent_Briefing.md # CTO 技术汇报
```

---

## 测试

```bash
# 运行全部测试
python -m pytest cognitiveplane/tests/ controlplane/tests/ executionplane/tests/

# 运行离线评估
python -m cognitiveplane.governance.eval_dataset

# E2E 测试（需完整环境）
WELDEVENT_RUN_E2E=1 python -m pytest cognitiveplane/tests/test_e2e_l1_l2_l3.py
```

```bash
# 运行治理层测试（8 模块 + 状态机 + NL->signal 链 + 确认门）
python -m pytest controlplane/tests/test_dag_runner_workflow.py controlplane/tests/test_node_state_machine.py cognitiveplane/tests/test_control/test_workflow_control_governance.py -q
```

测试覆盖: 695+ 通过，覆盖 ReActEngine/ToolRegistry/AgentLoop/MCP 基础设施/Guardrails/ValidationPipeline/Memory/WeldMap/L2-L3 集成 + 8 治理模块/状态机/heartbeat/embedding/cache/NL->signal 治理链。

---

## 当前状态与限制

### 已完成的

| 模块 | 成熟度 |
|------|--------|
| L1 ReActEngine 主循环（18 工具 + Session Notes + ID 持久化 + ToolFailureReflector） | ★★★★☆ |
| L1 AgentLoop + WebSocket（双 task 模型 + Session 持久化对齐 HTTP 路径） | ★★★★☆ |
| L1 18 个 L1 工具 + 11 个 MCP 标注工具 | ★★★★☆ |
| L1 前端 chat.html（弹窗动态文案 + WS 优先 + SubAgent 面板） | ★★★★☆ |
| L1 三层 Guardrails（AfterTool + Output + 5 条焊接规则） | ★★★★☆ |
| L1 LLM-as-a-Judge 评估（4 维度 + 46 用例 GOLDEN_SET + Langfuse 挂载） | ★★★☆☆ |
| L1 WorkflowObserver + EventBus | ★★★★☆ |
| L2 Temporal workflow（Kahn 拓扑排序 + 四阶段执行 + 20 个 action signal） | ★★★★☆ |
| L2 8 个治理模块（pause_scope/batch_hold/revoke/relabel/override/delegate/standard/case_correction） | ★★★★☆ |
| L2 治理事件溯源（6 字段事件驱动 + supersedes 链 + project(up_to=N) 回溯 + shadow 校验） | ★★★★☆ |
| L2 工作流版本更迭（参数热更新 + 拓扑变更 cancel+relaunch + PlanVersionRegistry 版本链 + SPEC_REVISION 事件） | ★★★★☆ |
| L2 节点状态机（9 状态 18 事件 + 硬门 pause_scope + PENDING 守卫） | ★★★★☆ |
| L2 Heartbeat 流式进度（4 阶段进度上报 + query_status 暴露） | ★★★★☆ |
| L2 NL -> agent -> signal 治理链（自然语言意图映射 + 确认门） | ★★★★☆ |
| L2 Mock 安全（P0-2: mock 返回 MARGINAL 从不 OK） | ★★★★☆ |
| L2 Saga 补偿 + DLQ + Token 预算 | ★★★★☆ |
| L1 Embedding 接入（ChromaRAGAdapter CaseLibraryQueryPort + SearchCasesTool） | ★★★★☆ |
| L1 Prompt Caching（per-purpose 模型路由 + cache_prefix_tokens 门控注入） | ★★★★☆ |
| L3 IQA（4 项 CV 检查 + MLLM 深度视觉 + WeldMap 写入） | ★★★★☆ |
| L3 PPA（IQA 结果双路径回退 + 11 种预处理双实现） | ★★★★☆ |
| L3 Annotation（11 action + 4 级 version_id 回退 + MCP 集成） | ★★★★☆ |
| L3 WeldMap 黑板（CAS 乐观锁 + Event Sourcing + 6 域） | ★★★☆☆ |
| Shared MCP 传输层（Stdio + HTTP + SSE + Label Studio 认证） | ★★★★☆ |

### 部分完成

| 模块 | 现状 |
|------|------|
| L3 6/9 Activity | MEA/RDA/VDA/RVA/MTA/HCA 为 mock，走 fallback 路径 |
| WeldMap 生产实现 | 仅 InMemory，Redis/MinIO+DB 未实现 |
| Eval 完整评估 | GOLDEN_SET 不跑 Agent（预写 reply），无法测试工具命中率 |
| Context Compaction | 已编码但未充分接入 AgentLoop |
| LLMResponseCache | 单进程 LRU，不跨进程共享 |
| 状态机硬强制 | shadow-first 迁移中，仅 pause_scope + PENDING 守卫为硬门 |
| 工作流版本更迭 | 参数级变更热更新已实现 ✅；拓扑变更走 PlanRevisionProposal 版本链 ✅；Temporal Update API / patched() 版本感知分支仍未接入 🟡 |
| ChromaRAG embedding | 使用 chroma 默认 embedding（未显式配置 embedding_function），接入自定义 embedding 时需统一维度 |

### 已知待实现（按优先级）

**生产化短板**（从试点到生产的关键 gap）:
- **L4 跨工作流知识层**: `standard_update` / `case_library_correction` 仅扫当前 workflow audit_trail，跨工作流影响分析需持久化聚合层
- **记忆持久化**: `TrajectoryStore` 当前纯内存 list，进程重启即丢；经验回流需落盘或入向量库
- **状态机硬强制**: 本体是 shadow，生产级需从软断言切硬强制（非法转移应阻断 workflow）
- **可观测性**: Langfuse 无 key 时降级 no-op，生产环境需配置 key 启用 trace
- **artifact 版本化存储**: revoke_approval 的 external 档（已转正式）只能留痕标记，需版本快照支持回滚

**功能待完善**:
- auto_annotate 拆分为 6 独立 Activities（当前为复合节点）
- 多 Agent 架构（Planner/Reflector/SubAgentDelegator 代码已写但未接入）
- Prompt 模板化/版本管理
- 权限/认证系统（多操作员 RBAC）
- 多 operator 并发冲突处理（同一 workflow 同时 pause + revoke 的隔离）

---

## 架构哲学

1. **架构替 LLM 做看不见的事** — ApprovalGate、Context Compaction、Session Notes、DSML fallback、同轮工具上限、ToolFailureReflector，全部架构层固化，不依赖 LLM 自觉。
2. **三平面 specialization** — L1 推理 / L2 编排 / L3 执行各司其职，跨层通过 WeldMap + Temporal signal 通信。
3. **boundary-pinning 边界钉死** — L1 不直接执行工业 verb，L2 不直接调 LLM，L3 不直接暴露给用户。L1 与 L3 共享 `shared/` 传输层但不共享业务封装。
4. **MCP 协议化工具层** — Label Studio 通过 MCP 协议接入，L1 通过 ToolPolicy 分级控制可见性。
5. **双路径统一治理** — HTTP /stream 与 WS /ws 共享 hooks/skills/approval/compactor/guardrails。
6. **三层 Guardrails 防护** — BeforeTool(工具入参) + AfterTool(工具结果) + Output(LLM 输出) + 焊接领域规则。
7. **LLM-as-a-Judge 闭环** — 4 维度实时评分 + 46 用例离线回归 + Langfuse 分数追踪。
8. **确定性缓存** — 仅缓存 temperature==0 请求，key 含 tools+response_format SHA256。
9. **自然语言治理** - 用户用自然语言介入工作流，agent 理解意图后调 control_workflow 触发 signal。前端只展示不操作，所有干预经对话发起。第二档不可逆操作强制确认门，责任留痕。
10. **shadow-first 迁移** - 状态机先镜像列表状态 + 软断言，验证一致性后再切硬强制，避免一次性引入阻断风险。
11. **治理事件溯源** - 治理状态从可变字段改为事件流投影。supersedes 链解决叠加语义（pause+rework 同一节点），project(up_to=N) 支持回溯任意历史时刻。事件流只增不改，审计完整。不引入新框架，语义层解决多治理状态叠加。
12. **责任留痕** - 每个治理干预产生不可变 DecisionRecord，含 actor/reason/timestamp。override/revoke/case_correction 的确认门记录"用户显式确认"而非"LLM 理解"，责任链清晰。

---

---

## 技术与算法来源总表

> 按 L1/L2/L3 三层分层组织，每层内部按模块/文件细分。每项标注：**借鉴来源** · **本系统实现** · **文件位置**。全面审查代码后整理。

---

## L1 · 认知平面 (cognitiveplane)

### L1.1 ReActEngine 核心推理循环

**文件**: `control/engine/react.py` (~1,622 行)

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **ReAct 循环** | ReAct: Reasoning + Acting (Yao et al., ICLR 2023, arXiv:2210.03629) | Thought -> Action -> Observation 循环，直到 finish。三级回退：Tier1=Function Calling LLM，Tier2=关键词路由，Tier3=规则兜底 |
| **Context Engineering** | Anthropic "Context Engineering" (2024) | 每轮执行后派生 Session Notes（摘要 + 关键信息提取）；长对话超阈值触发 Context Compaction |
| **Course-correct 进度感知** | Anthropic "course-correct early and often" (2024) | 进度感知：检测 LLM 循环或停滞，主动干预而非等耗尽 token |
| **卡住检测 + 失败反思** | Anthropic "give Claude a way to verify" (2024) | 检测连续失败，生成失败反思 note 注入下一轮上下文 |
| **Agent Skills 语义匹配** | Claude Code SKILL.md 渐进式披露 (2026) + OpenAI Codex sub-agent discovery + Microsoft Semantic Kernel plugins | LLM-first 语义分类选 Skill（非关键词匹配），未匹配时 fallback 到通用模式（所有工具可见） |
| **轨迹存储** | trae-agent AgentExecution 导出 | per-step 推理轨迹记录（P0-3） |

### L1.2 Session Notes 与记忆增强

**文件**: `control/engine/session_notes.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **Session Notes 派生** | Anthropic Context Engineering：每轮执行后生成简短笔记 | 摘要 + 关键决策 + 待办，注入下一轮 system prompt |
| **失败反思** | Anthropic "give Claude a way to verify" | 失败后生成结构化反思（不是原始错误），引导 LLM 换策略 |

### L1.3 System Prompt 构建

**文件**: `control/engine/system_prompt.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **Prompt Caching (cache-tail)** | Claude Code cache-tail reminder + Anthropic prompt caching API (2024) | system prompt 末尾注入 `[cache-tail]` 提示；`CACHE_BOUNDARY` 标记分隔缓存前缀与动态部分 |
| **7 步推理流程注入** | trae-agent 7 步法 + Claude Code feature-dev 分阶段流程 | system prompt 引导 LLM 按 7 步推理（理解 -> 检索 -> 规划 -> 执行 -> 验证 -> 反思 -> 回复） |
| **Project Document 发现** | Codex `agents_md.rs` + Claude Code `CLAUDE.md` | AGENTS.md 发现与加载，预算上限截断（参考 Codex `project_doc_max_bytes`） |

### L1.4 ApprovalGate 确认门

**文件**: `control/engine/approval.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **架构级确认门** | Claude Code human-in-the-loop 架构保证 | 不依赖 system prompt 约束 LLM"等确认"，架构在工具执行前拦截 APPROVAL_REQUIRED_TOOLS，emit approval_request 并阻塞，直到前端 /chat/approve resolve |

### L1.5 工具执行与护栏

**文件**: `control/engine/tool_execution.py`, `governance/guardrails.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **三层 Guardrails** | Pydantic AI guardrails (RETRY action) + 焊接领域规则 (NB/T47014-2011, GB/T3323-2005) | BeforeTool(参数/权限校验) + AfterTool(危险参数检测：电流>500A、预热<100°C) + Output(LLM 输出违规检测)，动作 PASS/WARN/REJECT/RETRY |
| **BeforeTool Hook** | OpenHands PreToolUse (hooks/types.py) + Cline hooks | ALLOW/DENY only，不支持参数修改（与 OpenHands/Cline 对齐验证） |
| **ToolFailureReflector** | Plan §3.5 原则 5 | LLM 不收原始 jsonschema 错误，收结构化失败反思 |

### L1.6 多 Agent 规划与协作

**文件**: `control/planner/ledger.py`, `control/planner/advanced.py`, `control/planner/versioning.py`

| 技术 | 借鉴来源 | 实现 | 状态 |
|------|---------|------|------|
| **Dual-Ledger 规划器** | Magentic-One (arXiv:2411.04468) §3.1-3.3 dual ledger architecture | MagenticPlanner：外循环 FactsLedger 更新 facts/constraints（可回滚），内循环 ProgressLedger 跟踪 specialist 进度 | ✅ 已接入 |
| **停滞检测** | Magentic-One §3.3 stagnation detection | 检测 specialist 无进展，触发重新分配 | ✅ 已接入 |
| **动态委派** | Magentic-One dynamic delegation + Anthropic orchestrator-workers | SpecialistRole 动态分配，任务路由 | ✅ 已接入 |
| **Orchestrator-Workers** | Anthropic "Building Effective Agents" orchestrator-workers pattern (2024) | ParallelSpecialistRunner 类已定义 | 🟡 未实例化 |
| **ReWOO 批量规划** | ReWOO (Xu et al., 2023, arXiv:2310.18323) - separate reasoning from observation | 先推理生成完整计划（不调工具），再批量执行，减少 LLM 调用轮次 | ✅ 已接入 |
| **Reflexion 自我反思** | Reflexion (Shinn et al., NeurIPS 2023, arXiv:2303.11366) | 失败后生成 text-based 失败教训（ReflexionMemory），注入下一轮上下文 | ✅ 已接入 |
| **Self-Refine** | Self-Refine (Madaan et al., NeurIPS 2023, arXiv:2303.17651) | SelfRefineCycle 类已定义 | 🟡 未调用 |
| **PlanVersion 不可变快照** | 调研报告分层纪律 §2 + Temporal workflow determinism | frozen 后不可改，变更需新版本（parent_version_id 链） | ✅ 已接入 dag_runner |
| **ContextManifest** | Pydantic AI Harness context manifest (调研报告 §5.4) | 冻结 model_id + embedding_model + context_hash，保证 replay 一致 | ✅ 已接入 dag_runner |
| **RevisionProposal** | 调研报告 §7.2 | 唯一修改入口：区分拓扑变更 vs 参数变更，含 impact_nodes | ✅ 已接入 dag_runner |
| **Cancel + Relaunch** | Temporal workflow versioning + Update API (1.25+) | 拓扑变更走 cancel+relaunch，保留已完成节点 | ✅ 已接入 dag_runner |

### L1.7 记忆系统

**文件**: `memory/hierarchy.py`, `memory/trajectory_store.py`, `memory/compaction.py`, `memory/blocks.py`, `memory/archive.py`, `memory/dual_write.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **分层记忆 L0-L5** | Generative Agents (Park et al., UIST 2023) + Letta/MemGPT memory blocks | MemoryLevel 枚举 + PROMOTION_RULES 定义 | 🟡 仅定义，无运行时实例 |
| **Episodic Memory + Reflection** | Generative Agents reflection + retrieval (Park et al., UIST 2023) | TrajectoryMemoryStore：存储 episodic 记忆，可检索相似历史 episode |
| **Context Compaction** | Pydantic AI Harness compaction (调研报告 §5.5) | 超阈值时压缩历史对话，保留关键信息块 |
| **Block-Based Working Memory** | Letta/MemGPT memory blocks | MemoryBlocks 分块管理 |
| **Agent-Controlled Archival** | Generative Agents archival memory | ArchiveTool：agent 主动归档 |
| **Dual-Write Persistence** | 7-plane redesign §10 | 同步写内存 + 异步落盘 |
| **Memory Confidence** | 7-plane redesign §10 | 记忆置信度标记，影响晋升决策 |

### L1.8 审计与学习

**文件**: `audit/decision_record.py`, `audit/execution_record.py`, `audit/governance_event.py`, `audit/pattern_learning.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **DecisionRecord 审计** | Event Sourcing (Greg Young, ~2005) + ISO 3834 compliance | 每个治理干预产生不可变记录（actor/reason/timestamp/context） |
| **ExecutionRecord 重放** | Temporal event history replay + Magentic-One replay debugging | 完整执行历史，支持重放和经验学习 |
| **治理事件溯源** | Event Sourcing (Greg Young) + 本系统自创（不引入 Restate/LangGraph） | GovernanceEventLog + GovernanceProjection：6 字段事件驱动，supersedes 链，project(up_to=N) 回溯 |
| **Pattern Learning** | Reflexion (Shinn et al., NeurIPS 2023) + CBR (Aamodt & Plaza, 1994) | PatternLearner 类已定义 | 🟡 未实例化 |
| **ArtifactVersion 版本化** | DVC data versioning + MLflow model lineage | artifact 版本链，provenance 保留 |
| **BatchHoldRecord 隔离** | 制造质量体系扣留/隔离 (quarantine) + fan-out 隔离 | 可疑批次扣留，结果隔离不阻塞其他 |
| **PauseScopeRecord 分级** | pause/resume/cancel 三分法 (分级版) + 信号批量 | 三级暂停：workflow/station/batch |

### L1.9 能力层 (Capability)

**文件**: `capability/openai_provider.py`, `capability/evaluation.py`, `capability/tracking.py`, `capability/response_cache.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **per-purpose 模型路由** | L1-Interaction-Layer-Business-Requirements §3.6.4 | resolve_model(purpose) 按 purpose 选模型（reasoning/classification/explanation/embedding/vision） |
| **Anthropic cache_control 注入** | Anthropic prompt caching API (2024) | 给稳定前缀消息打 `cache_control` 标记，boundary 处也打标 |
| **Evaluator-Optimizer** | Anthropic "Building Effective Agents" evaluator-optimizer (2024) | ZeroCostEvaluator：工作流执行后基于 node 完成状态零成本打分 |
| **LLMCallTracker 成本追踪** | L1-Interaction-Layer-Business-Requirements §3.6.9 | 按 purpose 聚合 token/成本/延迟 |
| **确定性缓存** | Anthropic prompt caching cost control | 仅缓存 temperature==0 请求，key 含 tools+response_format SHA256 |

### L1.10 交互层 (Interaction)

**文件**: `interaction/`, `interaction/api/`, `interaction/signals/`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **双路径接入** | 7-plane redesign §7 FastAPI + plan §2.3 | HTTP /stream (SSE) + WS /ws (WebSocket) 双路径，共享 hooks/skills/approval |
| **EventBus 事件总线** | L1-Interaction-Layer-Business-Requirements §6.3 | WebSocket/SSE 双路径事件推送 |
| **Session 隔离** | 7-plane redesign §7 (Fix P1-8) | operator-isolated sessions，多操作员不串 |
| **Multimodal Payload** | L1-Interaction-Layer-Business-Requirements §2.7 | text + image_url 混合 payload |
| **ToolRegistry 渐进式披露** | Claude Code / Codex progressive disclosure | 按 phase 控制工具可见性，phase-all 工具始终可见 |

### L1.11 工具层

**文件**: `control/tools/`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **ManagePlanTool** | Claude Code TodoWrite | LLM 自主任务拆解 | 🟡 类存在，未注册到 ToolRegistry |
| **DesignWorkflowTool** | Airflow DAG 设计 + 声明式工具组合 (macro) | LLM 传 nodes 直接编排，工具做能力校验和 WorkflowSpec 构建 |
| **InjectContextTool** | Magentic-One Task Ledger 可更新约束 (§3.2) | L1 工具未注册（inject_context 作为 L2 signal 存在） | 🟡 未注册 |
| **DelegateTool** | Codex sub-agent 委派 + Claude Code feature-dev plugin 3 种专用 agent | DelegateTool + SubAgentRunner 类已定义 | 🟡 未注册，SubAgentRunner 未实例化 |
| **ArchiveMemoryTool** | 7-plane redesign §10 Agent-Controlled Archival | agent 主动归档不再需要的信息 |
| **WorkflowControlTool** | Airflow dynamic DAG + Temporal Update API | NL -> signal 转发，8 类治理 action |
| **ActivityCatalogTool** | boundary-pinning §1.3/§1.5 Activity Pool + 工业 MCP | 声明式工具组合 + 可复用 macro |

### L1.12 知识层

**文件**: `knowledge/adapters/chroma_rag.py`, `knowledge/ports.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **CBR 案例检索** | Case-Based Reasoning (Aamodt & Plaza, 1994) | ChromaDB `weld_cases` collection，5 条焊缝种子案例，defect_type + similarity_context 检索 |
| **RAG 知识检索** | 7-plane redesign §4 Port Ownership Map | ChromaDB 3 collection (vision/reasoning/cases)，Port/Adapter 可替换后端 |

### L1.13 评估框架

**文件**: `governance/evaluation.py`, `governance/eval_dataset.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **LLM-as-a-Judge** | Langfuse scoring (https://langfuse.com/docs/scores/overview) + LLM-as-a-Judge pattern (2026) | 4 维度异步打分 (helpfulness/safety/tool_efficiency/grounding)，焊接领域专用 judge prompt |
| **GOLDEN_SET 回归** | 46 用例 5 类场景 | 正常工艺/危险建议/违规参数/国标查询/缺陷诊断 |
| **截断 JSON 修复** | 本系统 P1-8 修复 | `_repair_truncated_json`，max_tokens 512 -> 1024 |

### L1.14 治理与安全

**文件**: `governance/guardrails.py`, `governance/policy_ratchet.py`, `governance/permissions.py`, `governance/tool_policy.py`, `governance/validators/`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **PolicyRatchet 棘轮** | 棘轮机制 (append-only, no auto-GC) | YAML 草稿存储，错误记录不阻断 |
| **RBAC 权限** | 静态 RBAC | 三角色：inspector(只读)/engineer(读+设计+确认)/safety(提级+覆盖) |
| **ToolPolicy 分级** | 7-plane redesign §9 | 按 operator 角色控制工具可见性 |
| **四级验证管线** | 7-plane redesign §5 | Safety -> Rule -> Shadow -> Consistency |
| **SafetyValidator** | 本系统设计 | BLOCK: critical_count>=3 / confidence<0.3 / 安全关键词 |
| **ShadowValidator** | 本系统设计 | 独立 shadow 决策，alignment_score<0.6 -> WARN |
| **ConsistencyValidator** | 本系统设计 | WeldMap 状态 + 历史决策一致性比对 |

### L1.15 声明式 Subagent 与 Skill 系统

**文件**: `control/subagent.py`, `control/skills.py`, `control/declaration.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **声明式 Subagent** | Codex TOML agent config + Anthropic orchestrator-workers | `.weldevent/agents/` TOML 风格定义，DeclarationLoader 统一加载 |
| **Agent Skills** | Claude Code SKILL.md (2026) + Codex sub-agent discovery + Microsoft Semantic Kernel plugins | `.weldevent/skills/*.md` YAML frontmatter + Markdown，LLM-first 语义匹配 |

---

## L2 · 控制平面 (controlplane)

### L2.1 RunWorkflowSpec 核心工作流

**文件**: `runtime/dag_runner_workflow.py` (~3,092 行)

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **Temporal 耐久执行** | Temporal (gRPC) - workflow determinism, event history replay | 唯一 workflow 实现，sandbox 限制（不能用 time.time/os.environ/random，用 workflow.now()） |
| **Temporal Signal** | Temporal Signal API | 20 个 action signal（human_review/pause_scope/batch_hold/rework_node/modify_spec...） |
| **Temporal Query** | Temporal Query API | query_status() 暴露完整工作流状态 |
| **Kahn 拓扑排序** | Kahn's algorithm (1962) | WorkflowSpec nodes -> 拓扑排序 -> 分层并行执行 |
| **四阶段节点执行** | 数据库两阶段提交 (draft->commit) + Temporal Activity 生命周期 | PREPARE(构建input+幂等键) -> EXECUTE(调activity) -> VALIDATE(schema+LLM语义评估) -> COMMIT(存储+审计) |
| **Evaluator-Optimizer (VALIDATE)** | Anthropic "Building Effective Agents" evaluator-optimizer (2024) | LLM 语义评估 quality_score，MARGINAL 降级，趋势检测收敛/恶化 |
| **Conditional Branching** | Argo Workflows `when` clause + Airflow BranchPythonOperator | node.input.when 表达式求值 |
| **Dead Letter Queue** | Kafka DLQ + MapReduce backup tasks (Dean & Ghemawat, OSDI 2004) | 重试 3 次后隔离，下游取默认值不阻塞 |
| **Saga 补偿** | Saga pattern (Garcia-Molina & Salem, SIGMOD 1987) | 节点失败时按逆序执行已记录补偿动作 |
| **依赖污染传播 BFS** | Airflow downstream propagation | rework_node/revoke_approval/relabel_request 用 BFS 遍历 depends_on 图 |
| **Token 预算** | Claude Code token budget + Anthropic prompt caching cost control | 累计追踪，80% 预警，100% 降级 |
| **Batch Signal 处理** | Temporal signal batching | checkpoint 处批量应用信号，避免信号间竞态 |
| **Heartbeat 流式进度** | Temporal heartbeat pattern | 四阶段边界报进度 (0.2/0.6/0.85/1.0) |
| **治理事件溯源** | Event Sourcing (Greg Young, ~2005) | 6 字段事件驱动，supersedes 链，project(up_to=N) 回溯 |
| **Shadow 校验** | 本系统自创（复用 _assert_sm_consistency 模式） | 投影 vs 字段比对，mismatch 只记 warning 不阻断 |
| **P0-2 Mock 安全** | 本系统设计 | 所有 mock 返回 MARGINAL + mock=True，绝不返回 OK |
| **幂等键** | Temporal 最佳实践 | idempotency_key 防重复执行 |
| **分级暂停** | Magentic-One check-in + Temporal Reset | pause_scope 三级 (workflow/station/batch)，只阻塞目标节点，其余继续；RESUME_SCOPE supersedes PAUSE_SCOPE |
| **批次冻结** | 制造质量体系 quarantine + fan-out 隔离 | batch_hold 可疑批次扣留，RELEASE_HOLD/REWORK_BATCH/QUARANTINE_BATCH supersede BATCH_HOLD |
| **节点重做回溯** | Airflow downstream propagation BFS | rework_node BFS 找下游闭包清除旧结果重跑；REWORK_NODE_COMPLETED supersedes REWORK_NODE |
| **撤回已批准** | Temporal Reset (回退到指定历史点) | revoke_approval BFS 找已消费 artifact 的下游，标 AFFECTED_BY_REVOKE |
| **人工覆盖** | 人工校准 (human calibration) | ground_truth_override 绕过 Evaluator-Optimizer，质检员直接拍板，machine_verdict 保留 |
| **标签修正** | rework_node 标签专项版 | relabel_request 只回标注环节，复用既有结果，不重跑昂贵分析 |
| **上下文注入中断恢复** | Magentic-One Task Ledger 可更新约束 | inject_context 中途补充信息，INJECT_CONTEXT 事件累积，只影响未执行节点 |
| **supersedes 链** | Event Sourcing snapshot + rewrite pattern | resume/release/rework_completed 不删旧事件，追加新事件声明作废，投影按链过滤 |
| **回溯时间旅行** | Temporal event history replay + project() | project(up_to=N) 从事件流 reduce 出历史某时刻完整治理状态，支持 A/B 对比 |
| **叠加可见性** | 本系统自创 (node_interventions) | 投影显示每节点受哪些 active 事件影响，解决 pause+rework 同节点叠加 |
| **参数级热更新** | 数据库迁移模式 (schema migration) + Temporal Update API | modify_spec 自动区分参数变更 vs 拓扑变更；参数级热更新未执行节点，不 cancel |
| **Plan 版本注册表** | Temporal workflow versioning + PlanVersionRegistry | 启动创建 FROZEN 版本，拓扑变更走 propose_revision 创建新版本，旧版本 superseded |
| **SPEC_REVISION 事件** | Event Sourcing 扩展 | spec 版本更迭记入事件流，project(up_to=N) 还原任意时刻 spec 版本 |

### L2.2 节点状态机

**文件**: `runtime/node_state_machine.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **显式状态机** | Temporal 工作流状态模型 | 9 状态 / 18 事件，shadow-first 迁移（镜像列表状态 + 软断言） |
| **shadow-first 迁移** | 本系统设计 | 先镜像验证一致性，再切硬强制（避免一次性引入阻断风险） |

### L2.3 Activity 适配层

**文件**: `adapter/dag_activities.py`, `adapter/mocks.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **Temporal RetryPolicy** | Temporal RetryPolicy | Activity 层 maximum_attempts=1，瞬时故障 re-raise 让 Temporal 重试 |
| **L2->L1 事件回传** | boundary-pinning §6.2 + Temporal sandbox 限制 | workflow 不能直接调 HTTP，通过 activity 回传节点事件 |
| **Evaluator-Optimizer (L2 侧)** | Anthropic "Building Effective Agents" (2024) | dag_activities 内置 evaluator-optimizer 验证 |

### L2.4 Domain 模型

**文件**: `domain/workflow_spec.py`, `domain/activity.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **Kahn 拓扑排序** | Kahn's algorithm - BFS by dependency level | WorkflowSpec nodes 分层排序 |
| **Temporal sandbox 安全** | Temporal workflow determinism | 不用 uuid4/time.time/placeholder（违反确定性），用 workflow_spec_from_dict |

---

## L3 · 执行平面 (executionplane)

### L3.1 IQA 图像质量评估

**文件**: `capabilities/cv_rules.py`, `capabilities/numpy_cv_checker.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **Laplacian 方差对焦检测** | OpenCV 文档标准方法 | variance of Laplacian，有 OpenCV / 纯 numpy 手动卷积核双实现 |
| **IQA 四项 CV 检查** | Complete_architecture_V1.docx §3.1 规则层 | 对焦/亮度/对比度/噪声，确定性函数延迟 <10ms |
| **CV 规则可替换** | boundary-pinning 设计 | 默认 OpenCV/numpy，测试可用 Mock 替代 |

### L3.2 PPA 图像预处理

**文件**: `capabilities/vision/preprocessor.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **CLHE/CLAHE 自适应增强** | OpenCV CLAHE | IQA 结果驱动增强策略，双路径回退（OpenCV -> PIL） |
| **双路径回退** | 本系统设计 | 无 OpenCV 时尝试 PIL，保证降级可用 |

### L3.3 MLLM 深度视觉

**文件**: `capabilities/mllm_provider.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **MLLM 多模态分析** | Complete_architecture_V1.docx §3.1 深度视觉层 | Volc Doubao Vision Pro 32K，独立于 ReAct 主循环（避免占用推理上下文） |
| **认知 MCP vs 工业 MCP 隔离** | boundary-pinning §1.1 | L1 认知 MCP 与 L3 工业 MCP 不共享实现 |

### L3.4 WeldMap 黑板

**文件**: `weldmap/models.py`, `weldmap/client.py`, `weldmap/in_memory.py`, `weldmap/watcher.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **黑板模式** | Complete_architecture_V1.docx 第二章 | 6 域跨 Activity 状态共享（IQA 写入 -> PPA 读取） |
| **CAS 乐观锁** | Compare-And-Swap 并发控制 | 版本号乐观锁，冲突时 retry，asyncio.Lock 保护原子性 |
| **ArtifactVersion 版本化** | DVC data versioning + MLflow model lineage | 焊缝 artifact 版本链，provenance 保留 |
| **Event Sourcing** | Complete_architecture_V1.docx §2.3 | WeldMap 事件溯源（与 L1 治理事件源独立） |
| **Watcher 模式** | Complete_architecture_V1.docx §2.3 | 监听 WeldMap 变更，触发回调 |

### L3.5 Annotation 标注

**文件**: `integrations/label_studio/client.py`, `activities/annotation/activity.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **MCP 协议集成** | Model Context Protocol (https://spec.modelcontextprotocol.io/) | Label Studio MCP Server v2.0，Streamable HTTP 传输 |
| **短生命周期 MCP client** | Temporal activity 无状态语义 | 每次 execute() 创建新 MCP client 连接，不跨 activity 保持 |
| **4 级 version_id 回退** | 本系统设计 | 标注版本回退机制 |
| **11 action MCP 工具** | boundary-pinning §1.1 工业 MCP | 数据集查询/作业管理/子任务/AI 标注 |

### L3.6 Activity Pool

**文件**: `pool.py`, `activities/base.py`, `activities/contracts.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **Activity Pool 分派** | boundary-pinning §1.3/§1.4/§6.2 | 按 capability 名分派到具体 Activity 实例，所有 activity 共享同一 WeldMapClient |
| **Port/Adapter 隔离** | 端口/适配器六边形架构 | `contracts.py` 独立副本打破 L3->L2 直接 import |

---

## 跨层 · Shared + Bridge

### Shared 共享传输层

**文件**: `shared/mcp/protocol.py`, `shared/mcp/client.py`, `shared/labelstudio/`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **MCP 协议** | Model Context Protocol (https://spec.modelcontextprotocol.io/) | Stdio + HTTP + SSE 三种传输 |
| **Label Studio 认证** | 本系统设计 | JWT Token + 用户名/密码自动登录 |

### Bridge L1<->L2 桥接

**文件**: `bridge/temporal_client.py`, `bridge/workflow_launcher.py`, `bridge/event_connector.py`

| 技术 | 借鉴来源 | 实现 |
|------|---------|------|
| **WorkflowSpec DTO 传输** | boundary-pinning §6.2 + §5 | dict 序列化跨 Temporal 边界，L1 构建 -> L2 执行 |
| **直通模式** | boundary-pinning §6.2 | 废弃 legacy `BrainDecision -> WorkflowTemplate` 翻译步骤，WorkflowSpec 直通 Temporal |

---

## 前沿 Agent 框架对标总表

| 前沿框架/论文 | 借鉴的核心思想 | 本系统对应实现 | 接入状态 |
|-------------|--------------|--------------|---------|
| **ReAct** (Yao et al., ICLR 2023) | Thought-Action-Observation 循环 | ReActEngine 三级回退 | ✅ 已接入 |
| **Magentic-One** (arXiv:2411.04468) | Dual-ledger (Facts+Tasks) 规划 | MagenticPlanner 外/内循环 | ✅ 已接入 |
| **Anthropic "Building Effective Agents"** (2024) | Evaluator-Optimizer / Orchestrator-Workers | Evaluator-Optimizer ✅ / ParallelSpecialist 🟡 | 🟡 部分 |
| **Anthropic Context Engineering** (2024) | Session Notes / course-correct / verify | session_notes + 进度感知 + 卡住检测 | ✅ 已接入 |
| **Reflexion** (Shinn et al., NeurIPS 2023) | text-based 失败教训记忆 | ReflexionMemory ✅ / PatternLearning 🟡 | 🟡 部分 |
| **Self-Refine** (Madaan et al., NeurIPS 2023) | 生成-评估-改进迭代 | SelfRefineLoop 类已定义 | 🟡 未调用 |
| **ReWOO** (Xu et al., 2023) | 推理与观察分离 | ReWOOPlan 批量规划 | ✅ 已接入 |
| **Generative Agents** (Park et al., UIST 2023) | 分层记忆 + reflection + retrieval | TrajectoryStore ✅ / MemoryHierarchy 🟡 | 🟡 部分 |
| **CBR** (Aamodt & Plaza, 1994) | 案例检索推理 | ChromaRAG weld_cases | ✅ 已接入 |
| **Event Sourcing** (Greg Young, ~2005) | append-only 事件流 + 投影 | GovernanceEventLog + DecisionRecord | ✅ 已接入 |
| **Saga** (Garcia-Molina & Salem, SIGMOD 1987) | 逆序补偿 | _saga_compensations | ✅ 已接入 |
| **Temporal** (framework) | Signal/Query/RetryPolicy/Reset/versioning | RunWorkflowSpec 全机制 | ✅ 已接入 |
| **Airflow** | downstream propagation / dynamic DAG | BFS 依赖传播 / modify_spec | ✅ 已接入 |
| **Argo Workflows** | when clause 条件分支 | Conditional Branching | ✅ 已接入 |
| **Kafka DLQ** | 死信队列隔离 | _dead_letter | ✅ 已接入 |
| **MapReduce** (Dean & Ghemawat, OSDI 2004) | backup tasks | DLQ 下游默认值 | ✅ 已接入 |
| **Pydantic AI Harness** | context manifest / compaction / tool-effect ledger | ContextManifest + Compaction + EventLog | ✅ 已接入 |
| **Claude Code** | TodoWrite / cache-tail / SKILL.md / human-in-the-loop | cache-tail + Skills + ApprovalGate ✅ / ManagePlanTool 🟡 | 🟡 部分 |
| **Codex** | TOML agent config / sub-agent / project_doc | project_doc ✅ / SubAgent + DelegateTool 🟡 | 🟡 部分 |
| **Letta/MemGPT** | memory blocks | MemoryBlocks | ✅ 已接入 |
| **Microsoft Semantic Kernel** | plugins | Agent Skills 参考 | ✅ 已接入 |
| **OpenHands** | PreToolUse hooks | BeforeTool Hook 对标 | ✅ 已接入 |
| **Cline** | hooks | BeforeTool Hook 对标 | ✅ 已接入 |
| **trae-agent** | 7 步法 / AgentExecution 导出 | system prompt 7 步 + 轨迹导出 | ✅ 已接入 |
| **Temporal Update API** (1.25+) | validator + handler 飞行中修改 | modify_spec 参数热更新 (借鉴 Update 模式，未用原生 API) | 🟡 借鉴思想 |
| **Temporal patched()** | 版本感知分支 | PlanVersionRegistry 版本链 ✅ / patched() 代码分支 🟡 | 🟡 部分 |
| **Restate/LangGraph** | 单状态持久化/时间旅行 | 明确不引入（不解决多治理状态叠加） | ❌ 不采用 |
