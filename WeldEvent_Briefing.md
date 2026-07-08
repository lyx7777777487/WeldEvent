# WeldEvent 三层 Agent 系统实现情况

> CTO 技术汇报文档 | 2026-06-30 | 版本 v0.3.0

---

## 1. 概述

WeldEvent 是一个**工业焊缝质检智能决策引擎**，采用三层 Agent 架构：

| 层 | 名称 | 职责 | 技术栈 |
|---|---|---|---|
| L1 | 认知平面 (Cognitive Plane) | LLM 推理、工具调用、工作流编排、用户交互 | FastAPI + DeepSeek-Chat + Volc Doubao Vision + SSE/WebSocket |
| L2 | 控制平面 (Control Plane) | 工作流 DAG 执行、可靠性保障、重试/审计 | Temporal (gRPC) |
| L3 | 执行平面 (Execution Plane) | 工业视觉活动（IQA 图像质量评估、PPA 预处理、Label Studio 标注） | OpenCV/NumPy + MCP 协议 |

**核心设计原则（来自 `docs/2026-06-15-capability-loops-redesign.md`）：**

> "LLM decides what to do; the architecture decides what cannot be done."
>
> 系统不是 Pipeline，而是 LLM 的能力基座 — 提供工具、记忆、安全边界，让 LLM 自主决策每一步。
>
> **固定（架构决定）：** 可用工具集、安全边界（什么需要审批）、记忆存取方式。
> **动态（LLM 决定）：** 调用哪个工具、何时调用、如何分析推理、何时查询记忆、何时停止。

---

## 2. 架构全景

```
┌─────────────────────────────────────────────────────────────────┐
│                     L1 认知平面 (Cognitive Plane)                 │
│                                                                   │
│  chat.html (多 Agent UI)                                         │
│  ├── Chat Agent (对话面板, purple)  ── 对话、工具调用、图片分析    │
│  └── Execution Agent (执行面板, cyan) ── 工作流实时监控、停止      │
│       │                                                           │
│  FastAPI /api/v1/chat/stream (SSE)  /ws (WebSocket)              │
│       │                                                           │
│  ReActEngine ── 3-Tier 能力检测 → LLM 推理 → ToolRegistry 执行   │
│       │                                                           │
│  ToolRegistry (12 tools)                                         │
│  ├── search_standards / search_cases / search_process             │
│  ├── analyze_image (Volc Vision)                                  │
│  ├── design_workflow → WorkflowSpec (DAG)                        │
│  └── launch_workflow → EventConnector → L2 Temporal              │
│                                                                   │
│  Memory Hierarchy (短期/长期记忆)   Knowledge Adapter (领域知识)   │
│  SafetyHook + PolicyHook (安全治理)  PolicyRatchet (故障记录)     │
└────────────────────────────┬──────────────────────────────────────┘
                             │  gRPC (WorkflowSpec)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    L2 控制平面 (Control Plane)                    │
│                                                                   │
│  Temporal Server (localhost:7233)                                │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  RunWorkflowSpec (DAG Runner)                               │ │
│  │  ├── topological_sort(nodes)   拓扑排序                      │ │
│  │  ├── execute_node activity     逐节点执行                    │ │
│  │  │   ├── tool_task   → L3 ActivityPool                      │ │
│  │  │   ├── human_task  → wait_condition(HumanGate, 1h timeout)│ │
│  │  │   └── brain_task  → placeholder                          │ │
│  │  ├── on_failure: abort / continue / escalate / retry        │ │
│  │  └── RetryPolicy(max_attempts=3, backoff=2.0s)              │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                   │
│  Queries:  query_status()     Signals:  human_gate(node_id, bool) │
│  Cancel:   terminate(reason)                                      │
└────────────────────────────┬──────────────────────────────────────┘
                             │  execute_node Activity
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                   L3 执行平面 (Execution Plane)                   │
│                                                                   │
│  ActivityPool ── capability → activity dispatch                  │
│  ├── defect_detection / iqa  → IqaActivity (真实 CV + MLLM)     │
│  │   ├── 分辨率检查 / 曝光检查 / 对焦检查 / 完整性检查 (~10ms)   │
│  │   └── MLLM 深度视觉 (遮挡/污渍/异物/反光, 1-3s)              │
│  ├── preprocess / ppa       → PpaActivity (自适应预处理)        │
│  │   └── 亮度/对比度/去噪/锐化/去反光                            │
│  └── annotation             → AnnotationActivity (Label Studio)  │
│                                                                   │
│  WeldMap (跨 Activity 状态共享内存)                               │
│  └── IQA 写质量报告 → PPA 读取 → 调整预处理策略                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. L1 认知平面 — 详细实现状态

### 3.1 ReAct 推理引擎 (`cognitiveplane/control/react.py`, 1413 行)

**三层能力检测（启动时确定，运行时不切换）：**

| Tier | 名称 | 条件 | 行为 |
|------|------|------|------|
| 1 | REACT_FUNCTION_CALLING | LLM 支持 function calling | LLM 接收工具定义，自主推理并调用 `tool_calls`，循环直到不再需要工具 |
| 2 | STRUCTURED_OUTPUT | LLM 支持 JSON mode 但不支持 function calling | 意图分类（5 类）+ 规则化工具路由 |
| 3 | EMBEDDING_RULES | 无 LLM | 中英文关键词匹配 → 最佳可用工具 |

**每轮迭代三阶段：**
1. **顺序校验：** JSON 解析 → Schema 验证 → Hook 检查（SafetyHook, PolicyHook）。被拒工具注入 LLM 上下文以便重试。
2. **并发执行：** 所有通过校验的工具 `asyncio.gather` 并发执行。
3. **顺序结果处理：** 按 LLM 原始 tool_call 顺序返回结果（LLM 对上下文顺序敏感）。

**工具可靠性分层（每次调用独立）：**
- **Tier-A（可重试）：** 信息获取类工具（search_*/read_weldmap/explain_decision/web_search/archive_memory/analyze_image）— schema 失败重试 1 次。
- **Tier-B（精确）：** 行动类工具（design_workflow/launch_workflow/escalate）— schema 失败立即拒绝。

**两条执行路径（System Prompt 闸门）：**
- **短路径：** QA/聊天/看图 → `analyze_image` / `search_*` / `read_weldmap` → 直接回答。**绝不调用 `design_workflow` 或 `launch_workflow`。**
- **长路径：** 质检/标注/执行 → `analyze_image` → `design_workflow` → `request_confirmation` → `launch_workflow`。**必须在 launch 前获得用户确认。**

**安全参数：**
- `max_iterations=6`（超限优雅降级）
- `TOOL_TIMEOUT_SECONDS=90.0` per tool
- `PolicyRatchet` 记录每次 schema/tool 失败到 YAML 草案存储

**双模式：**
- `run()`：同步模式，完整 ReAct 循环 → `InteractionResponse`
- `run_stream()`：异步生成器，逐事件 SSE 推送（thinking → tool_call → tool_result → token → workflow_progress → final）

**System Prompt 自动注入四源世界观：**
1. `WELDEVENT.md` — 项目约定
2. `OPERATOR.md` — 操作员偏好
3. `CASE_BRIEF` — 当前案例上下文
4. `Memory.search` — 历史修正/相似案例

### 3.2 AgentLoop — WebSocket 运行时 (`cognitiveplane/control/agent_loop.py`, 362 行)

双任务模型，支持三种消息类型：

| 消息类型 | 行为 |
|----------|------|
| `chat` | 启动 `react_task`，运行 `ReActEngine.run()`，推送结果 |
| `feedback` | 写入 Memory → 队列化 FeedbackSummary → 下一轮 ReAct 通过 Memory.search 注入 |
| `interrupt` | 取消当前 `react_task`（`asyncio.Task.cancel()`），立即终止 LLM 推理 |

**治理对齐：** WebSocket 路径使用与 HTTP 路径相同的 `[SafetyHook(), PolicyHook()]` 链，确保入口无关的工具授权一致性。

### 3.3 工具注册表 — 12 个已注册工具

| 工具 | Phase | 类型 | 描述 | 实现状态 |
|------|-------|------|------|----------|
| `search_standards` | 1 | Tier-A | 查询焊接标准 (NB/T47014, GB/T3323, ISO 3834) | ✅ Stub 适配器中预置 70+ 条目 |
| `search_cases` | 1 | Tier-A | 检索历史缺陷案例 | ✅ |
| `search_process` | 1 | Tier-A | 查询焊接工艺参数 (GMAW/GTAW/SMAW) | ✅ |
| `read_weldmap` | 1 | Tier-A | 读取当前案例 WeldMap 状态 | ✅ |
| `explain_decision` | 1 | Tier-A | 解释过往决策 | ✅ |
| `archive_memory` | 1 | Tier-A | 保存到操作员记忆 | ✅ |
| `web_search` | 1 | Tier-A | DuckDuckGo 网络搜索 | ✅ |
| `analyze_image` | 2 | Tier-A | 多模态焊缝图像分析 (Volc Doubao Vision Pro 32K)。自动缩放到 max 1024px, JPEG q=80 | ✅ 12 张真实焊缝图 E2E 通过 |
| `design_workflow` | 3 | Tier-B | 从 L3 Activity Catalog 能力生成 WorkflowSpec DAG | ✅ LLM 编排 + 关键词回退 |
| `launch_workflow` | 3 | Tier-B | 提交 WorkflowSpec 到 L2 Temporal，轮询进度，收集 progress_events | ✅ |
| `request_confirmation` | 1 | — | 启动工作流前请求用户确认 | ✅ |
| `escalate` | 1 | Tier-B | 升级到人工操作员 | ✅ |

**Phase Gating 机制：** 工具带 `phase` 标记。`ToolRegistry.current_phase=3`。`phase > current_phase` 的工具注册但不暴露给 LLM — 基础设施就绪但 LLM 不可见。

### 3.4 L3 活动目录 (`cognitiveplane/control/tools/activity_catalog.py`)

9 个 L3 能力已定义，LLM 在设计工作流时可引用：

| 能力 | 中文名 | 实现状态 |
|------|--------|----------|
| `defect_detection` (IQA) | 图像质量评估 | ✅ 真实 CV+MLLM |
| `preprocess` (PPA) | 图像预处理 | ✅ 真实 |
| `annotation` | 标注任务 | ✅ Label Studio MCP |
| `mea` | 金相分析 | ⚠️ Mock |
| `rda` | 实时缺陷分析 | ⚠️ Mock |
| `vda` | 视频缺陷分析 | ⚠️ Mock |
| `rva` | 实时视频分析 | ⚠️ Mock |
| `mta` | 材料测试分析 | ⚠️ Mock |
| `hca` | 历史案例关联分析 | ⚠️ Mock |

### 3.5 LLM 供应商架构

| 用途 | 供应商 | 模型 | 关键约束 |
|------|--------|------|----------|
| 文本推理 | DeepSeek | deepseek-chat (32K ctx) | OpenAI 兼容 API，function calling |
| 视觉分析 | Volc Engine (字节) | Doubao Vision Pro 32K | 独立调用，不在 ReAct 主循环上下文中 |
| Web 搜索 | DuckDuckGo | — | 自动提供者 |

**关键设计规则：** 视觉调用永远不进入 ReAct 主循环消息。工具层在调用 `analyze_image` 时独立调 `vision_complete`。

### 3.6 桥接层 (L1 → L2)

**EventConnector**（`cognitiveplane/bridge/event_connector.py`, 211 行）：
- 接收 `design_workflow` 产出的 `WorkflowSpec`
- 提交前通知 WeldMap gateway（`notify_workflow_trigger`）— 事件溯源
- 提供：`query_status()`, `send_human_gate_signal()`, `cancel_workflow()`, `is_healthy()`, `aclose()`

**TemporalWorkflowLaunchPort**（`cognitiveplane/bridge/temporal_client.py`, 293 行）：
- 延迟客户端初始化（`asyncio.Lock` 双重检查）
- `submit(spec)`：`client.start_workflow("RunWorkflowSpec", ...)`
- `cancel_workflow(workflow_id, reason)`：`handle.terminate(reason=...)`
- 连接错误检测 + 客户端失效 + 自动重连
- 错误脱敏（绝不向 LLM 泄露 `host:port`）

### 3.7 Chat API 端点 (`cognitiveplane/interaction/api/chat.py`, 523 行)

| 端点 | 方法 | 用途 | 状态 |
|------|------|------|------|
| `/` | POST | JSON 同步聊天 | ✅ |
| `/upload` | POST | multipart/form-data 文件上传聊天（图片/PDF/Word/Excel/CSV/ZIP） | ✅ |
| `/stream` | POST | SSE 流式聊天（打字机 token + 工具气泡 + 工作流进度） | ✅ |
| `/ws` | WebSocket | Phase 3 AgentLoop 双向通信（chat/feedback/interrupt） | ✅ |
| `/cancel` | POST | 取消 Temporal 工作流 | ✅ |

**图像会话管理：** `ImageSessionRegistry` 追踪每个会话已上传的图像，跨请求保持 `image_ref` 引用。

### 3.8 实时反馈与持续学习

**当前状态：** WebSocket 路径的 `feedback` 消息类型已实现（写入 Memory → 队列化 FeedbackSummary → 下一轮 ReAct 注入），但仅在 AgentLoop 层，未与视觉分析/标注流程打通。

**目标架构 — 非阻塞双向反馈闭环：**

```
┌──────────┐     SSE stream      ┌──────────────┐
│  Agent   │ ──────────────────► │  前端执行面板  │
│ (Server) │                     │  (chat.html)  │
│          │ ◄────────────────── │               │
└──────────┘   POST /feedback    │  用户可修改:   │
      │       (用户修正/勾画)     │  · 标注框调整  │
      │                          │  · 分类纠正    │
      ▼                          │  · 通过/驳回   │
┌──────────┐                     └──────────────┘
│  Memory  │  每一条修正三层写入:
│  Context │  → L1 短期（当前 case 上下文）
│          │  → L2 中期（批量标注的模式漂移）
│          │  → L3 长期（特定缺陷类型的标注偏好）
└──────────┘
```

**关键设计原则：**
- Agent 处理下一张时**不等待**用户反馈前一张 — 非阻塞流水线
- 反馈异步到达后，在下一轮 ReAct 前合并最新反馈到上下文
- 每个修正事件同时写入 Memory L1 + L2 + 反馈历史队列

**目标检测反馈场景：**
1. Agent 调视觉 MLLM → 检测结果（bbox + 缺陷类型 + 置信度）SSE 实时推送到前端
2. 用户看到检测框，发现漏标/错标 → 直接在界面上修改
3. 修改通过 `POST /feedback` 发回 → 写入 Memory → 注入当前 ReAct 上下文
4. 后续图片检测时 prompt 已包含修正记录 → 自动适配用户偏好

**辅助标注持续学习场景：**
1. Annotator Agent 分析首张焊缝图 → 预标注结果（裂纹 90% / 气孔 70%）→ 推送到前端
2. 标注员发现"气孔"实际是"夹渣" → 修正标签
3. 修正回传 → Agent 学习：此纹理特征对应夹渣而非气孔
4. 第 2 张图：Agent 已调整判断 → 推新预标注
5. 持续循环，标注到第 N 张时 Agent 已与标注员形成协作默契

**动态工作流适应：** 用户中途说"除了裂纹，这批再查一下咬边"→ Designer Agent 动态插入检测节点 → Temporal 增量执行 → 前端 exec-panel 实时追加新行，无需重建面板。

---

## 4. L2 控制平面 — 详细实现状态

### 4.1 RunWorkflowSpec (`controlplane/runtime/dag_runner_workflow.py`, 279 行)

通用 DAG 执行器，接收 L1 产出的 `WorkflowSpec`：

- **拓扑排序** — 基于 `depends_on` 边，DFS 着色循环检测
- **顺序节点执行** — 当前 Phase 2 实现，Phase 4+ 将添加独立节点的并行执行
- **三种节点类型：**
  - `tool_task` → `execute_node` activity → L3 ActivityPool（真实或 mock）
  - `human_task` → `workflow.wait_condition` 等 HumanGate signal（1 小时超时）
  - `brain_task` / `wait_task` → placeholder 响应
- **`on_failure` 处理：** `abort`（立即失败）/ `escalate`/`continue`/`retry`（标记失败，继续下游）
- **Temporal 查询：** `query_status()` → `{status, completed_nodes, failed_nodes, node_results, human_gate_pending}`
- **Temporal Signal：** `human_gate(node_id, approved)` — 操作员审批
- **Activity RetryPolicy：** `maximum_attempts=3, initial_interval=1s, backoff=2.0`

### 4.2 Worker (`controlplane/worker.py`, 78 行)

- 连接 Temporal Server（`localhost:7233`, namespace `default`, task queue `control-plane`）
- 导入 `executionplane.pool.create_default_pool()` 连接真实 L3 activities
- 回退到 mock（如果 executionplane 不可用）
- 注册 2 个 workflow + 9 个 activity
- `SIGINT`/`SIGTERM` 优雅关闭

### 4.3 WorkflowSpec L1/L2 解耦

L1 和 L2 各自维护独立的 `WorkflowSpec` 定义：
- L1：`cognitiveplane/shared/dto_workflow.py`（Pydantic）
- L2：`controlplane/domain/workflow_spec.py`（dataclass frozen）
- 两个模块通过 `workflow_spec_from_dict()` / `workflow_spec_to_dict()` 序列化/反序列化
- **设计原因：** 两层有独立的 `pyproject.toml`，禁止跨模块 import — 保证解耦

### 4.4 执行期人工介入（目标架构）

工作流确认执行后仍可随时人工干预——不是执行前的一次性审批，而是执行全程的交互式控制。

**三层干预粒度：**

| 粒度 | 场景 | 前端操作 | 后端机制 |
|------|------|---------|---------|
| **工作流级** | 方向不对，终止重来 | 停止按钮（已实现） | Temporal terminate + 全部节点标记 cancelled |
| **节点级** | 参数/标准需调整，换算法 | 节点右键菜单：暂停并修改 / 替换 / 跳过 / 重跑 | InterventionGate Signal → pause → modify params → resume/replace/skip/rerun |
| **数据级** | 某张图结果不符合预期 | 节点展开 → 逐项修正：改标签 / 调阈值 / 标记异常 | rerun_item + 上下文注入 + 下游级联感知 |

**InterventionGate vs HumanGate：**
- HumanGate（已实现）：设计阶段的预定义审批点，LLM 设计工作流时决定哪里需人工确认
- InterventionGate（新增）：执行期任意节点任意时刻的外部中断，不依赖预判

**单图级干预数据流（批量场景的核心）：**
```
用户修正 weld_003 标签（裂纹→咬边）
  ├─→ Memory L1: "weld_003 实际是咬边，非裂纹"（后续图 prompt 自动包含）
  ├─→ signal: rerun_item(node="detect_defects", item="weld_003", correction=...)
  ├─→ 下游 annotate_label 感知变更 → weld_003 自动重新标注
  └─→ 前端 exec-panel: weld_003 行 ⟳ rerunning → ✓ undercut 88%
```

**三级回溯策略（上游变更后下游怎么办）：**

| 策略 | 触发条件 | 行为 | 适用场景 |
|------|---------|------|---------|
| **浅回溯** | 仅修改当前节点参数且输出无变化 | 重跑当前节点，下游不变 | 换阈值、换 prompt |
| **深回溯** | 修改影响下游依赖 | 从修改点沿 DAG 依赖边级联重跑所有受影响节点 | 换算法、改标签分类 |
| **全回溯** | 根本性变更 | 终止当前 workflow，用新参数重新提交 | 方向性错误 |

Temporal 原生支持 workflow replay。业务回溯更轻量：标记"失效节点"→ 下游 `depends_on` 感知失效 → 自动重跑，同时保留原始执行结果在不可变的 Temporal history 中作为审计记录。

**标准/阈值动态变更：** 用户说"全批按 ISO 5817 B 级验收"→ Designer Agent 识别受影响节点（iqa / detect_defects / grade_assessment）→ 对未开始/进行中/已完成节点分别处理 → 级联回溯 → 前端实时标记受影响的节点。

**实现路径：** Phase 4a: InterventionGate + pause/resume Signal + 参数修改重跑 + 跳过节点。Phase 4b: depends_on 失效传播 + 自动回溯 + 审计记录分离。Phase 5: 逐项展开 UI + 右键修正菜单 + 逐图上下文注入。

---

## 5. L3 执行平面 — 详细实现状态

### 5.1 ActivityPool (`executionplane/pool.py`, 234 行)

能力到活动的分发器：
- `defect_detection` / `iqa` → IqaActivity
- `preprocess` / `ppa` → PpaActivity
- `annotation` / `label_studio` → AnnotationActivity
- `dispatch(node_input)` → `ActivityInput` → `activity.run()` → `ActivityOutput`
- `create_default_pool()` 工厂注册 IQA（真实）、PPA（真实）、Annotation（真实，可通过 env 禁用）
- 未注册能力在 L2 `execute_node` 中回退到 mock

### 5.2 IQA — 图像质量评估 (`executionplane/activities/iqa/activity.py`, 638 行)

真实 CV 判定 + MLLM 深度视觉：

**确定性规则层（~10ms）：**
- 分辨率检查
- 曝光检查（平均灰度）
- 对焦检查（Laplacian 方差）
- 完整性检查（焊缝区域比例）

**MLLM 深度视觉（1-3s，仅在规则层置信度不足时触发）：**
- 遮挡检查 / 镜头污染 / 异物检测 / 反光识别 / 异常纹理

**路由决策：** `AUTO_PASS` / `SUGGEST_REVIEW` / `MANDATORY_REVIEW` / `REJECT`

**WeldMap 持久化：** 写 `ImageQualityReport` → 下游 PPA 读取 → 调整预处理策略

### 5.3 PPA — 图像预处理 (`executionplane/activities/ppa/activity.py`)

- 从 WeldMap 读取 IQA 结果
- 自适应策略：亮度/对比度校正、去噪 + 锐化、边框填充、去反光
- 写预处理图像（Phase 4+ 接入 MinIO）

### 5.4 Annotation — 标注活动 (`executionplane/activities/annotation/activity.py`)

Label Studio MCP 集成：
- 4 个操作：`create_task`, `push_prediction`, `fetch_annotations`, `export_dataset`
- 通过 MCP 协议与外部 Label Studio MCP Server 进程通信
- 连接失败触发 Temporal 重试策略

---

## 6. 前端 — 多 Agent UI

### 6.1 双 Agent 架构

| | Chat Agent | Execution Agent |
|---|---|---|
| 面板位置 | 主聊天区（左） | 执行面板（右，380px 滑入） |
| 配色 | Purple (`#8b5cf6`) | Cyan (`#06b6d4`) |
| 交互模式 | 对话、流式 token、工具气泡 | 状态节点 DAG、进度条、汇总 |
| 职责 | 理解需求、调用工具、编排工作流 | 监控执行、展示结果、支持停止 |
| 接口 | `/chat/stream` SSE | 同一条 SSE（event 分流） |

### 6.2 三场景模式

| 模式 | 输入方式 | 典型行为 | 状态 |
|---|---|---|---|
| 对话模式 | 文本 + 文件上传 | 对话式 QA，图片分析，工作流编排 | ✅ 完整实现 |
| 批量模式 | ZIP 数据集上传 | 并行处理大批量图像 | 🏗️ UI 骨架 |
| 实时模式 | Webhook 推送 | 流水线持续监听，实时决策 | 🏗️ UI 骨架 |

### 6.3 SSE 流式处理

- `fetch` + `ReadableStream` 手动解析 SSE（EventSource 不支持 POST）
- 事件分发：`thinking` / `tool_call` / `tool_result` / `token` / `workflow_progress` / `final` / `error`
- 流式 token 仅局部刷新最后一个气泡 DOM（性能友好，避免全量重渲染）
- 工具调用中文可读标签（如 "正在分析图片..." / "正在编排质检工作流..."）

### 6.4 执行面板

- `workflow_progress: started` 事件初始化面板，列出全部节点
- 节点逐行更新：⏳ pending → ▶ running（cyan 呼吸灯）→ ✓ done / ✗ failed
- 进度条 + 统计（x/y 完成）
- 完成时自动显示结果汇总
- **停止按钮：** 前端 AbortController 中断 SSE + 调 `POST /api/v1/chat/cancel` 终止 Temporal 工作流
- 面板可关闭 → 顶部显示 toggle 按钮（执行中 x/y 计数）

**目标 — 执行期介入 UI（Phase 4+）：**

```
┌─────────────────────────────────────────────┐
│ 执行面板                          [停止] [×] │
│  Workflow: WX-2026-001 焊缝批量质检          │
│  ████████████░░░░░░  8/12 节点完成           │
├─────────────────────────────────────────────┤
│  ✓ iqa_inspection        完成 (50/50)        │
│  ▶ detect_defects        运行中 (23/50)       │
│    ├─ weld_003.jpg  ✗ 裂纹 45%  [查看] [修正]│  ← 用户刚修正
│    ├─ weld_004.jpg  ⟳ 重跑中...              │  ← 上游修正触发回溯
│    └─ ...                                    │
│  ⏳ annotate_label        等待上游            │
│    └─ ⚠ 3 项待回溯（上游变更）               │  ← 级联影响提示
│  操作栏:                                      │
│  [修改节点参数] [跳过此节点] [替换节点]         │
│  [调整全局阈值] [标记异常样本] [导出修正记录]   │
└─────────────────────────────────────────────┘
```

**需新增能力：** 节点展开 → 逐项（逐图）查看进度；每项右键修正/重跑/标记异常；⚠ 回溯影响提示；全局操作栏（修改标准、替换节点）。

---

## 7. 测试覆盖

| 里程碑 | 测试数 | 状态 |
|--------|--------|------|
| Phase 2 完成 | 674 | ✅ 全部通过 |
| Phase 3C (MCP 基础设施) | 805 | ✅ 全部通过 |
| Phase 3D (AgentLoop + WebSocket) | 809 passed / 3 skipped | ✅ 阶段纪律 PASSED |
| Boundary-Pinning 止血 | 809 | ✅ 阶段纪律 PASSED (0 warnings) |

**关键 E2E 验证：**
- 12 张真实焊缝图片端到端通过（IQA + PPA + SSE 流式进度）

---

## 8. 先进 Agent 技术审计

> **诚实原则：** 以下逐项对比 WeldEvent 当前实现与工业界/学术界先进 Agent 技术。
> 标记说明：✅ 已接入主循环 | 🏗️ 代码已实现但未接入 | ⚠️ 仅接口/存根 | ❌ 尚未开始

### 8.1 上下文持久化与记忆（Context Persistence & Memory）

**业界标准：** MemGPT/Letta 的 OS-style 记忆管理、LangGraph 的持久检查点、多级记忆架构（工作记忆/语义记忆/情景记忆）。

| 能力 | 状态 | 实现文件 | 说明 |
|------|------|----------|------|
| **L0-L5 记忆层级** | 🏗️ | `cognitiveplane/memory/` | 完整定义了 6 级记忆层级：L0 会话缓冲 / L1 短期总结 / L2 中期压缩 / L3 语义提取 / L4 长期向量 / L5 工具记忆。类型定义完备。 |
| **MemoryManager** | 🏗️ | `manager.py` | 实现了基于 Token 预算的记忆管理器，支持优先级驱逐、标签（tag）归类。 |
| **ContextCompactor** | 🏗️ | `compactor.py` | 4 种压缩策略：trim（裁剪旧消息）/ summarize（LLM 摘要）/ extract（提取关键事实）/ rag_replace（RAG 替换历史）。全部编码完成。 |
| **InMemoryMemoryRepository** | ⚠️ | `repository.py` | 内存存储实现，但 `semantic_search` 返回常量 `similarity_score=1.0`（未接入实际 embedding）。 |
| **记忆接入 ReAct 主循环** | ❌ | — | MemoryManager/ContextCompactor **未被 AgentLoop 调用**。当前 ReAct 循环仅使用原始消息列表 + LLM 自带上下文窗口，无主动记忆管理。 |

**差距分析：** 记忆基础设施的代码质量高（定义清晰、策略完整），但关键断裂点在**接入层**。AgentLoop 未在每轮 ReAct 前调用 `MemoryManager.retrieve()` 或 `ContextCompactor.compact()`。这是低风险高收益的激活项 — 代码已写，仅需接入。

### 8.2 RAG / 知识检索

**业界标准：** 向量数据库（Chroma/Pinecone/Milvus）+ Embedding 模型 + 混合检索（BM25 + 向量）+ Reranker 精排。

| 能力 | 状态 | 实现文件 | 说明 |
|------|------|----------|------|
| **VectorStore 接口** | 🏗️ | `cognitiveplane/memory/interfaces.py` | 定义了 `add()` / `search()` / `delete()` 等完整接口，语义清晰。 |
| **向量存储实现** | ❌ | — | 接口定义了但无具体实现（MemoryRepository 中 `semantic_search` 是存根）。无 Chroma/Milvus/Pinecone 集成。 |
| **KnowledgeAdapter** | ⚠️ | `cognitiveplane/knowledge/adapter.py` | `StubKnowledgeAdapter` 使用关键词子串匹配（`if keyword in document.lower()`），非语义检索。 |
| **Embedding 模型** | ❌ | — | 未集成任何 embedding 模型（text-embedding-3-small 或本地 BGE）。 |
| **多模态 RAG** | ❌ | — | 图像检索（焊接缺陷 4W+ 样本的视觉检索）尚未开始。 |
| **知识检索接入 ReAct** | ❌ | — | ReAct 循环未调用任何知识检索步骤，LLM 完全依赖自身训练知识。 |

**差距分析：** 这是当前最薄弱的环节。焊接领域有 4W+ 缺陷样本可作为 RAG 语料，但整个知识检索链路（embedding → 向量库 → 检索 → 注入 prompt）无一实现。KnowledgeAdapter 的 stub 实现（关键词匹配）远非语义检索。**这是 P0 优先级：选择一个向量库 + embedding 模型跑通最小链路。**

**目标架构 — 领域知识库（Domain KB）：**

```
┌────────────────────────────────────────────────────────────┐
│                     领域知识库 (Domain KB)                    │
├────────────┬────────────┬────────────┬─────────────────────┤
│  焊接标准库  │  历史案例库  │  工艺流程库  │   缺陷分类体系       │
│  Standards │  Cases     │  Processes │   Taxonomy          │
├────────────┼────────────┼────────────┼─────────────────────┤
│ GB/T 3323  │ 4W+ 缺陷   │ WPS 工艺   │ 裂纹/气孔/夹渣/      │
│ ISO 5817   │ 样本图片    │ 规程       │ 未熔合/咬边/...     │
│ AWS D1.1   │ 诊断记录    │ 焊材参数    │ 等级 I/II/III/IV    │
│ 行业规范    │ 修复方案    │ 热处理要求   │ 合格/返修/报废        │
├────────────┼────────────┼────────────┼─────────────────────┤
│ Explorer   │ Explorer   │ Explorer   │ Annotator + Reviewer │
│ 检索标准    │ 相似案例    │ 流程约束    │ 标签字典 + 判定标准   │
└────────────┴────────────┴────────────┴─────────────────────┘
```

**知识库与 Memory 的分工：**
- **Knowledge Base**: 静态/慢变的领域事实（标准、规范、分类体系）— 共享、版本化
- **Memory**: 动态/快变的经验记录（会话、用户偏好、最近修正）— 私有、实时更新

**接入路径：** Phase 4: Web Search API（Explorer 搜公开标准）→ Phase 5: RAG 语义检索（Explorer + Annotator 搜 4W+ 样本）→ Phase 6: Agent 从反馈中自主提取新知识条目

### 8.3 规划与推理（Planning & Reasoning）

**业界标准：** ReAct / Plan-and-Execute / ReWOO / Tree-of-Thought / LLMCompiler / Reflexion（自我反思）。

| 能力 | 状态 | 实现文件 | 说明 |
|------|------|----------|------|
| **ReAct 循环** | ✅ | `cognitiveplane/brain/react_loop.py` | 核心循环：观察 → 思考 → 行动 → 观察。与 LLM 工具调用完整集成。 |
| **Planner 模块** | 🏗️ | `cognitiveplane/brain/planner.py` | 完整的规划器：可分解目标为子任务、评估依赖关系、生成执行计划。代码质量高。 |
| **Reflector 模块** | 🏗️ | `cognitiveplane/brain/reflector.py` | 反思器：分析执行结果、提取经验教训、生成改进建议。 |
| **BrainStateMachine** | 🏗️ | `cognitiveplane/brain/state_machine.py` | 脑状态机：定义了 IDLE/PLANNING/EXECUTING/REFLECTING/WAITING_FEEDBACK 等完整状态转换。 |
| **WorkflowSpec 设计** | ✅ | `design_workflow` 工具 | LLM 自动生成 DAG 工作流规格（节点、依赖、条件分支、失败处理）。已接入主循环。 |
| **Planner 接入 ReAct** | ❌ | — | Planner 模块**未被 AgentLoop 调用**。当前 ReAct 完全依赖 LLM 自身推理能力，无结构化规划步骤。 |
| **Reflector 接入 ReAct** | ❌ | — | Reflector 模块**未被 AgentLoop 调用**。无执行后反思环节。 |
| **BrainStateMachine 接入** | ❌ | — | 已定义但 AgentLoop 未使用。当前用简单的字符串状态（`thinking` / `acting` / `confirming`）。 |

**差距分析：** 规划与推理是典型的「高质量代码 → 未接入」模式。Planner/Reflector/StateMachine 三者加起来约 800 行代码，设计精良，但 AgentLoop 完全绕过它们。**接入 Planner（ReAct 前先规划）是最直接的提升路径 — 代码已就绪，改动量小。**

### 8.4 工具使用与学习

**业界标准：** 工具注册表/动态工具发现、工具执行沙箱、工具使用记忆（从历史成功/失败中学习）、自我纠正。

| 能力 | 状态 | 实现文件 | 说明 |
|------|------|----------|------|
| **ToolRegistry** | ✅ | `cognitiveplane/control/tools/registry.py` | 完整的工具注册/发现/执行基础设施。 |
| **MCP 工具** | ✅ | `IQAInspectionTool` / `PPADiagnosisTool` | MCP 协议的检测/诊断工具，含视觉 LLM 调用。已接入主循环。 |
| **Phase Gating** | ✅ | `registry.py` | 按 Phase 控制工具可见性，防止 LLM 访问未就绪的工具。 |
| **工具结果缓存** | ❌ | — | 无工具调用结果缓存（如相同图片不重复分析）。 |
| **工具使用记忆** | ❌ | — | LLM 无法从历史工具调用成功/失败中学习，每次调用独立。 |
| **工具执行沙箱** | ❌ | — | 无 Docker/代码沙箱隔离。当前设计下工具均为内部 Python 函数，安全性依赖代码审查。 |

**差距分析：** 工具基础设施坚实。工具结果缓存（避免重复调用视觉 LLM 浪费 Token）是最直接的 ROI 优化。工具使用记忆可在 ContextCompactor 接入后自然解决。

### 8.5 多智能体架构（Multi-Agent）

**业界标准：** 子代理委派（AutoGen/CrewAI）、专家代理池、代理间通信协议、层级 Supervisor 模式。

**当前能力审计：**

| 能力 | 状态 | 实现文件 | 说明 |
|------|------|----------|------|
| **SubAgentDelegator** | 🏗️ | `cognitiveplane/brain/sub_agent.py` | 完整子代理委派器：可创建独立 ReAct 子代理执行子任务，支持结果合并。 |
| **多代理通信** | ❌ | — | 无代理间通信协议。SubAgentDelegator 使用单进程内调用，非分布式代理通信。 |
| **专家代理池** | ❌ | — | 无预定义专家代理，当前为同一个 LLM 实例。 |
| **SubAgentDelegator 接入** | ❌ | — | 未接入 AgentLoop。 |
| **前端多 Agent UI** | ✅ | `chat.html` | Chat Agent + Execution Agent 双面板独立显示。 |

**差距分析：** 当前为单一 ReAct 代理 + 工具调用。但 SubAgentDelegator 已编码并通过测试，是实现基础。

**CTO 指导方向：四角色专家 Agent 协作模式**

CTO 建议引入 3-4 个有明确边界和独立能力集的专家 Agent，由 Supervisor（Chat Agent）统一路由，而非继续扩展单一超级 Agent：

| 角色 | 职责 | 核心工具 | 激活阶段 |
|------|------|---------|---------|
| **Explorer（探索者）** | 信息检索、知识查询、标准搜索 | WebSearch → RAG 检索 → 知识库查询 | 设计前期 |
| **Designer（设计者）** | WorkflowSpec 生成、方案编排、动态调整 | `design_workflow` / `launch_workflow` / 动态插节点 | 设计阶段 |
| **Annotator（标注者）** | Label Studio MCP 操作、逐张预标注、实时学习 | Label Studio MCP / per-image 建议生成 | 执行阶段 |
| **Reviewer（审核者）** | 质量审核、一致性检查、异常标记 | `query_status` / 历史对比 / 标准校验 | 执行+验收 |

**角色演进路径：**

| 角色 | Phase 4（MVP） | Phase 5（增强） | Phase 6（自治） |
|------|---------------|----------------|----------------|
| **Explorer** | Web Search API → 注入设计上下文 | 接入内部知识库 + RAG 语义检索 | 接入 Memory（L3-L4），从历史决策中学习 |
| **Designer** | 接收 Explorer 信息 + 用户需求 → 生成 WorkflowSpec | 动态工作流：用户中途插入需求 → 实时修改 DAG | 自驱动：根据 Explorer 发现自主提议工作流 |
| **Annotator** | 连接 Label Studio MCP，逐张预标注 | 用户每张修改后实时学习 → 后续建议逐张优化 | 冷启动到熟练：对特定缺陷形成稳定判断 |
| **Reviewer** | 标注完成后的质量检查 | 交叉验证：多图对比 + 焊接标准对照 | 基于历史案例的异常模式发现 |

**Agent 颗粒度设计：** CTO 提出的关键架构问题——Agent 应管理整个工作包还是精确到单任务？

| 维度 | 工作包级 (Coarse) | 任务级 (Fine) | 选择 |
|------|------------------|---------------|------|
| 上下文完整性 | ✅ 全局目标 | ❌ 丢失全局视角 | 关键决策用 Coarse |
| 并行度 | ❌ 串行 | ✅ 多 Agent 并行 | 高吞吐用 Fine |
| 学习效率 | ❌ 信号混合 | ✅ 独立反馈 | 在线学习用 Fine |
| 编排复杂度 | ✅ 简单 | ❌ 需 Supervisor | 用 Supervisor 化解 |
| Token 成本 | ❌ 全量上下文 | ✅ 仅任务相关 | Fine 更省 Token |

**混合方案：** Supervisor 做工作包级决策（批量标注还是质检复核？分配 Annotator 还是 Reviewer？每批多少张？），再由 Annotator/Reviewer 逐任务执行。Agent 持有标签分类体系，自行决定几张图一批、谁标注、谁审核，交付给 Label Studio 的标签 schema + 预标注数据 + 任务分配建议。

**与现有代码的映射：** SubAgentDelegator 已实现委派机制，Planner 可作 Designer 引擎，Reflector 可驱动 Annotator 在线学习 + Reviewer 审核反思，StateMachine 可协调多 Agent 状态。激活路径：定义角色 prompt + 接入对应模块 + 建立 Agent 间通信协议。

### 8.6 可观测性（Observability）

**业界标准：** LangSmith/LangFuse 追踪、Token 用量监控、延迟火焰图、LLM 调用链追踪。

| 能力 | 状态 | 实现文件 | 说明 |
|------|------|----------|------|
| **Tracer 接口** | ⚠️ | `cognitiveplane/observability/tracer.py` | `NoOpTracer` — 所有方法为空操作。接口定义了但无实现。 |
| **Token 用量追踪** | ✅ | `react_loop.py` | AgentLoop 内置 Token 计数（prompt + completion），记录每轮消耗。 |
| **结构化日志** | ✅ | 全项目 | Python logging + 结构化字段（workflow_id / node_id / tool_name）。 |
| **LLM 调用链追踪** | ❌ | — | 无 LLM 调用级追踪（调用 → 工具调用 → 子调用 → 响应 → 后续影响）。 |
| **LangSmith/LangFuse 集成** | ❌ | — | 未集成任何外部追踪平台。 |
| **延迟监控** | ❌ | — | 无按环节的延迟统计（推理耗时 / 工具执行耗时 / 端到端耗时）。 |

**差距分析：** Tracer 是目前最大的「接口存在但未实现」项。NoOpTracer 使得所有追踪数据直接丢弃。Token 追踪在 AgentLoop 日志中可用但无结构化导出。**选型 LangFuse 或自建简单 Tracer 可快速填补此空白。**

### 8.7 Prompt 管理

**业界标准：** 版本化 Prompt 模板、A/B 测试、Prompt 自动优化（DSPy）、多语言本地化。

| 能力 | 状态 | 实现文件 | 说明 |
|------|------|----------|------|
| **System Prompt** | ✅ | `cognitiveplane/brain/react_loop.py` | 硬编码字符串。包含角色定义、工具使用指引、输出格式约束。 |
| **Prompt 模板化** | ❌ | — | 无模板引擎（Jinja2 / f-string 模板）。Prompt 直接写在代码中。 |
| **Prompt 版本管理** | ❌ | — | 无 Prompt 版本记录、变更追踪、回滚机制。 |
| **Prompt A/B 测试** | ❌ | — | 无框架支持。 |
| **动态 Prompt 构造** | ❌ | — | 无按场景/用户/上下文的 Prompt 动态组装（如根据 case 类型注入不同专业指引）。 |

**差距分析：** Prompt 管理处于原型阶段（硬编码字符串）。对焊接质检场景，核心 Prompt（缺陷判定标准、行业规范引用）的版本化是合规审计的前提。**低优先级但长期重要。**

### 8.8 汇总矩阵

| 技术领域 | 核心能力 | 当前状态 | 激活成本 |
|----------|----------|----------|----------|
| **Memory** | 多级记忆接入 ReAct | 🏗️ 代码已写，未接入 | **低** — 2-3 行接入代码 |
| **RAG** | 语义检索 + 向量库 | ❌ 仅接口/存根 | **高** — 选型 + 集成 |
| **Planning** | Planner + Reflector 接入 | 🏗️ 代码已写，未接入 | **低** — 已在 AgentLoop 旁 |
| **Multi-Agent** | 子代理委派 | 🏗️ 代码已写，未接入 | **中** — 单 Agent 已够用 |
| **Observability** | Tracer + 调用链 | ⚠️ NoOpTracer | **中** — 需选型 LangFuse |
| **Prompt Mgmt** | 模板化 + 版本化 | ❌ 硬编码 | **低** — Jinja2 提取 |
| **Tool Learning** | 缓存 + 工具记忆 | ❌ | **低** — 缓存直加 |
| **Self-Reflection** | Reflector 接入 | 🏗️ 代码已写，未接入 | **低** — 接入行 |

**核心结论：** WeldEvent 的 Agent 架构在**设计层面**已覆盖绝大部分先进技术（Planner/Reflector/ContextCompactor/SubAgentDelegator/StateMachine 均有代码实现），但**关键断裂点在接入层** — AgentLoop 是唯一的生产执行路径，上述模块均未被接入。这不是能力缺失，而是**分阶段交付策略**：先跑通 ReAct 闭环，再逐项激活高级能力。

---

## 9. 尚未实现

### 9.1 冻结项 — 等待 Boundary-Pinning Spec 定稿

| 待实现 | 描述 | 阻塞原因 |
|--------|------|----------|
| **3.3 detect_defects** | 缺陷检测 MCP 工具 | 需要 `executionplane/` 仓库的 MCP Server（跨仓库依赖） |
| **3.4 annotate_label** | 标注标签 MCP 工具 | 同上 |
| **3.5 反馈闭环** | 人工修正注入下一轮 ReAct | AgentLoop feedback 路径已实现（写入 Memory + 队列），但生产级反馈闭环需要 spec 定稿后验证 |
| **Sub-Project E** | StdioClient 跨进程 MCP 通信 | 需要 MCP transport 层的完整设计 |
| **Sub-Project F** | executionplane MCP Server (IQA/PPA + Label Studio) | 需要 E 完成 + spec 定稿 |

### 9.2 未冻结但尚未实现

| 项目 | 描述 | 优先级 |
|------|------|--------|
| **RAG 向量检索** | embedding + 向量库 + 知识检索接入 ReAct | P0（4W+ 样本可作语料） |
| **ContextCompactor 接入** | 将已编码的 4 种压缩策略接入 AgentLoop | P1（低改造成本，高收益） |
| **Planner/Reflector 接入** | 将已编码的规划器/反思器接入 AgentLoop | P1 |
| **Tracer 实现** | NoOpTracer → 真实 LangFuse/自建实现 | P1 |
| **L2 并行节点执行** | RunWorkflowSpec 当前顺序执行 | P2 |
| **L3 批量数据集** | ZIP 上传 → Temporal fan-out 并行处理 | P2（UI 骨架已就绪） |
| **L3 实时流水线** | Webhook → 轻量决策环 → 持续执行 | P2（UI 骨架已就绪） |
| **MinIO 存储** | 原图持久化（当前 ImageStore 为内存实现） | P3 |
| **PostgreSQL 持久化** | 会话/记忆/状态持久化 | P3 |
| **Prompt 模板化** | 版本化 Prompt 管理 | P3 |
| **权限/认证系统** | 多操作员 RBAC | P3 |

### 9.3 已知架构待优化点

| 问题 | 影响 | 计划 |
|------|------|------|
| SSE `/stream` 路径无 AgentLoop 级中断 | 中断依赖客户端 AbortController + 服务器 `CancelledError` 传播 | 评测是否统一到 WebSocket |
| ReAct 循环无协作式取消检查点 | 长 LLM 调用期间取消延迟可达数十秒 | 添加 `asyncio.current_task().cancelled()` 检查点 |
| ToolRegistry 默认全注册 | LLM 可见工具集可能过宽 | boundary-pinning spec 定义精确工具路由 |
| MCPAdapter 继承 BrainTool | 天然鼓励 LLM 直连 MCP | 重构为 ToolPool 子组件 |

---

## 10. 技术亮点

1. **LLM 自主决策 + 架构强制约束** — LLM 拥有完全的工具选择自由，但 `design_workflow` / `launch_workflow` / `escalate` 等关键路径有不可绕过的安全边界。

2. **三层能力回退** — Function Calling → Structured Output → Keyword Rules，确保系统在弱 LLM 环境下仍可降级运行。

3. **Temporal 可靠性底座** — DAG 执行、自动重试、Human-in-the-loop 审批、完整审计链 — 工业级可靠性。

4. **L1/L2 解耦设计** — 两层独立 `pyproject.toml`，通过 dict 序列化过边界，防止认知平面污染控制平面。

5. **视觉与推理分离** — 视觉 MLLM 调用独立于 ReAct 主循环，避免视觉分析占用推理上下文窗口。

6. **双 Agent 前端** — Chat Agent（对话流）+ Execution Agent（执行监控）共享单条 SSE 连接，事件分流，支持停止/取消。

7. **架构前瞻性** — Planner/Reflector/ContextCompactor/SubAgentDelegator/StateMachine 等先进 Agent 模式已全部编码、通过测试、等待接入。这不是 PPT 设计，而是运行中的代码（809 tests passing）。

8. **多角色 Agent 协作设计** — Supervisor/Explorer/Designer/Annotator/Reviewer 四角色分工已规划完成，SubAgentDelegator 提供实现基础。混合颗粒度（工作包级决策 + 任务级执行）平衡全局上下文与并行效率。

9. **实时双向反馈闭环** — SSE 流式推送 + POST /feedback 回传 + Memory 三级写入 + 非阻塞流水线。Agent 不等待用户反馈即继续处理，反馈异步合并到下一轮上下文。

10. **执行期人工介入** — 超越执行前审批，支持节点级暂停/修改/替换/重跑 + 数据级逐图修正 + 三级回溯（浅/深/全）+ 标准动态变更 + Temporal 审计保护。

11. **809 测试 + 阶段纪律** — 每 Phase 有严格的阶段纪律检查，禁止跨 Phase 功能泄露。

---

## 11. 后续路径

```
Phase 3 (当前)                 Phase 4                       Phase 5+
──────────────────────────────────────────────────────────────────────────
Boundary-Pinning Spec 定稿     Explorer: Web Search → 注入    知识库自主管理
detect_defects                 Designer: 动态插入节点         Designer: 自驱动
annotate_label                  Annotator: 逐张在线学习        Agent 知识提取
反馈闭环验证                   Reviewer: 交叉验证             异常模式发现
                               InterventionGate + 回溯        冷启动→熟练
E/F 子项目                     RAG 向量检索                   自治任务分配
                               Planner/Reflector 接入         MinIO/PostgreSQL
                               ContextCompactor + Tracer      Prompt 模板化/RBAC
```

**关键瓶颈：** Boundary-Pinning Spec 定稿。一旦 spec 钉死五层边界，Phase 3 剩余任务可在 2-3 周内完成。

**P0 行动项（可并行进行，不被 Spec 冻结阻塞）：**
1. 向量库选型 + Embedding 模型集成 → RAG 最小链路跑通
2. ContextCompactor 接入 AgentLoop
3. Planner 接入 AgentLoop
4. Explorer Agent: Web Search API 接入 → 注入设计上下文

**审计→代码映射（已编码模块 → CTO 愿景角色）：**

| 审计中的技术 | 目标角色 |
|-------------|---------|
| SubAgentDelegator (🏗️) | Supervisor → Explorer/Designer/Annotator/Reviewer 委派 |
| Planner (🏗️) | Designer Agent 结构化规划引擎 |
| Reflector (🏗️) | Annotator 在线学习 + Reviewer 审核反思 |
| ContextCompactor (🏗️) | 多角色 Agent 间上下文压缩传递 |
| Memory Hierarchy (🏗️) | 实时反馈的三级记忆写入 |
| StateMachine (🏗️) | 多 Agent 协作的状态协调器 |
| RAG 向量检索 (❌) | Explorer + Annotator 的知识底座 |
