# WeldEvent 三层架构深度分析

> 基于代码实读，非文档转述。分析算法技术思想、解耦边界、扩展能力。

---

## 一、全景架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        用户 / 前端 (chat.html)                       │
│              SSE /chat/stream  +  WS /ws  +  /chat/approve           │
└────────────────────────────┬────────────────────────────────────────┘
                             │ HTTP / WebSocket
┌────────────────────────────┴────────────────────────────────────────┐
│  L1 · COGNITIVE PLANE（认知平面）  FastAPI :8000                      │
│                                                                      │
│  ┌──────────┐  ┌───────────┐  ┌──────────┐  ┌───────────────────┐   │
│  │SkillReg  │  │ ReAct     │  │ Approval │  │  SystemPrompt    │   │
│  │ 7 skills │─►│ Engine    │─►│  Gate    │  │  Builder (4源)    │   │
│  │ LLM 语义  │  │ 3-tier    │  │ 6 tools  │  │  worldview注入    │   │
│  │ 分类     │  │ fallback  │  │ asyncio  │  │  +session notes   │   │
│  └──────────┘  └─────┬─────┘  │ .wait()  │  └───────────────────┘   │
│                      │        └──────────┘                          │
│                ┌─────┴─────┐                                          │
│                │ToolReg    │  30 tools (14 L1 + 10 MCP + 6 内部)      │
│                │phase 门控  │  JSON Schema 校验 + Tier-A/B 重试       │
│                └─────┬─────┘                                          │
│                      │                                                │
│  ┌──────────┐  ┌─────┴─────┐  ┌──────────┐  ┌───────────────────┐   │
│  │Guardrails│  │ MCP       │  │ Policy   │  │  Context          │   │
│  │ 3层护栏   │  │ Registry  │  │ Ratchet  │  │  Compaction       │   │
│  │ before/   │  │ Label     │  │ 棘轮草稿  │  │  长对话压缩        │   │
│  │ after/    │  │ Studio    │  │          │  └───────────────────┘   │
│  │ output    │  │ 10 tools  │  │          │                          │
│  └──────────┘  └───────────┘  └──────────┘                          │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  Bridge: EventConnector → TemporalWorkflowLaunchPort        │    │
│  │  WorkflowSpec DTO → temporal_client.start_workflow()         │    │
│  │  (L1 永不 import temporalio SDK)                              │    │
│  └──────────────────────────┬───────────────────────────────────┘    │
└─────────────────────────────┼───────────────────────────────────────┘
                              │ gRPC (WorkflowSpec dict)
┌─────────────────────────────┴───────────────────────────────────────┐
│  L2 · CONTROL PLANE（控制平面）  Temporal Worker                    │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  RunWorkflowSpec (Temporal Workflow)                         │   │
│  │  ├─ topological_sort(nodes) → 拓扑排序                        │   │
│  │  ├─ for node in sorted:                                       │   │
│  │  │   ├─ human_task → wait HumanGate signal (≤30min)          │   │
│  │  │   ├─ execute_node activity → L3 ActivityPool.dispatch()    │   │
│  │  │   ├─ on_failure: abort / escalate / continue / retry      │   │
│  │  │   └─ emit_workflow_event → HTTP POST → L1 → SSE → 前端     │   │
│  │  ├─ signal: pause / resume / cancel_by_user                  │   │
│  │  └─ RetryPolicy(max=3, backoff=2.0s)                         │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  ┌──────────────────┐    ┌────────────────────────────────────┐      │
│  │ WorkflowSpec DTO │    │ execute_node → ActivityPool       │      │
│  │ (镜像副本, SSOT)  │    │ capability 别名解析 → L3 activity  │      │
│  └──────────────────┘    │ mock fallback (MARGINAL+mock=True)│      │
│                           └────────────────────────────────────┘      │
└─────────────────────────────┬───────────────────────────────────────┘
                              │ in-process dispatch
┌─────────────────────────────┴───────────────────────────────────────┐
│  L3 · EXECUTION PLANE（执行平面）                                    │
│                                                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐                 │
│  │ IQA         │  │ PPA         │  │ Annotation   │                 │
│  │ 分辨率/曝光  │  │ 亮度/对比度 │  │ Label Studio │                 │
│  │ 对焦/完整性 │  │ 去噪/锐化   │  │ MCP 10 tools │                 │
│  │ +MLLM(可选) │  │ 去反光      │  │              │                 │
│  └──────┬──────┘  └──────┬──────┘  └──────────────┘                 │
│         │                │                                           │
│         ▼                ▼                                           │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  WeldMap 黑板 (6 域 CAS + Event Sourcing)                     │   │
│  │  weldmap:///{wf_id}/                                          │   │
│  │    ├── image/quality/   ← IQA 写入 (CAS 乐观锁)               │   │
│  │    ├── mask/            ← PPA 写入                            │   │
│  │    ├── annotations/     ← Annotation 写入                     │   │
│  │    ├── validation/                                            │   │
│  │    ├── rendering/                                             │   │
│  │    └── decision/                                              │   │
│  │  InMemory 实现 (生产换 Redis)                                  │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 二、L1 认知平面 — 算法与技术思想

### 2.1 三层 ReAct 回退引擎

这是整个系统最核心的设计。不是简单的 "LLM 调工具"，而是三级保障：

```
用户消息
    │
    ▼
┌──────────────────┐  LLM 可用 + 支持 Function Calling
│ Tier-1: ReAct FC │  LLM 自主推理 → 调工具 → 观察结果 → 再推理
│ (首选, 自主决策)  │  max_iterations=25, temperature=0.3
└────────┬─────────┘
         │ LLM 不可用 / 不支持 FC
         ▼
┌──────────────────┐  LLM 可用但不支持 Function Calling
│ Tier-2: 结构化输出│  LLM 做意图分类 → 规则路由到工具
│ (降级, 分类路由)  │  intent → tool_map 映射
└────────┬─────────┘
         │ LLM 完全不可用
         ▼
┌──────────────────┐  无 LLM, 纯关键词匹配
│ Tier-3: 关键词规则│  keyword_map → score → best_match
│ (兜底, 规则匹配)  │  友好格式化回复
└──────────────────┘
```

**技术思想：** LLM 是"锦上添花"而非"唯一路径"。工业场景不能因为 API 挂了就完全瘫痪。三层回退确保系统在任何 LLM 可用性下都能工作。

### 2.2 ApprovalGate — 架构级人工确认

```
LLM 决定调 launch_workflow
        │
        ▼
┌───────────────────┐
│ ToolRegistry 检查  │  tool_name in APPROVAL_REQUIRED_TOOLS?
│ 是否需要审批        │  {launch_workflow, create_job, create_task,
└────────┬──────────┘   trigger_ai, upload_images, assign_task}
         │ 是
         ▼
┌───────────────────┐
│ ApprovalStore     │  create(approval_id, ...)
│ .create()         │  → emit "approval_request" 事件 → SSE → 前端弹窗
│                   │  → await req.event.wait()  ← 架构层阻塞!
└────────┬──────────┘  LLM 无法绕过, 不是 prompt 层面"请你等一下"
         │
    ┌────┴────┐
    │ 前端用户 │  POST /chat/approve {decision: "approved"/"rejected"}
    └────┬────┘
         │
         ▼
┌───────────────────┐
│ req.event.set()   │  阻塞解除, ReAct loop 恢复执行
│ 5min 超时自动取消  │
└───────────────────┘
```

**技术思想：** 不依赖 system prompt 告诉 LLM "等用户确认"（LLM 可能不听），而是架构在工具执行前物理阻塞。这是 Claude Code 风格的 human-in-the-loop 保证。

### 2.3 Session Notes — 跨轮记忆注入

每轮工具执行后，架构自动生成一行简短笔记注入下一轮 system prompt：

```
第1轮: search_standards → "已查询 search_standards：5 条结果"
第2轮: analyze_image   → "已分析图片：发现 夹渣, 气孔, 未熔合"
第3轮: design_workflow → "已设计方案（3节点, id=wf-abc）-> 待 launch_workflow 启动"
第4轮: launch_workflow → "工作流 wf-abc 已执行（COMPLETED）"

→ 下一轮 system prompt 自动包含:
  ## 已完成步骤备忘
  - 已查询 search_standards：5 条结果
  - 已分析图片：发现 夹渣, 气孔, 未熔合
  - 已设计方案（3节点, id=wf-abc）
  - 工作流 wf-abc 已执行（COMPLETED）
```

还有 **失败反思** (derive_failure_reflection)：工具失败时生成指导性建议，如"图像模糊（Laplacian<50），后续 PPA 必须启用锐化"。

**技术思想：** 解决 LLM 跨轮"失忆"问题。不靠增大 context window，而是用规则化摘要（零额外 LLM 调用）保持上下文连续性。借鉴 Anthropic Context Engineering。

### 2.4 四源世界观构建

System prompt 不是静态模板，而是每轮动态组装：

```
┌──────────────────────────────────────────────────────┐
│  SystemPromptBuilder.build() 每轮组装:                │
│                                                       │
│  1. 场景专业化指令  ← Skill MD 文件 / SubAgent override│
│  2. 世界观 (4源)    ← WELDEVENT.md (项目规约)          │
│                     ← OPERATOR.md (操作员偏好)         │
│                     ← CASE_BRIEF (当前 case)           │
│                     ← Memory.search (历史相似案例)     │
│  3. 项目诊断状态    ← session["case_data"] 阶段追踪    │
│  4. L3 能力目录     ← Activity Catalog (编排边界)      │
│  5. 已设计方案      ← session["current_plan"] 防重复   │
│  6. Session Notes   ← session["notes"] 已完成步骤       │
│  7. 工作流状态      ← WorkflowEventBus 长路径状态注入  │
│  8. 工具列表        ← ToolRegistry LLM 可见工具         │
└──────────────────────────────────────────────────────┘
```

### 2.5 三层 Guardrails

```
用户消息 ──► [input guardrail] ──► LLM 推理
                                      │
                                调工具前 ──► [BeforeToolHook] SafetyHook + PolicyHook
                                      │
                                工具执行
                                      │
                                工具返回 ──► [AfterToolHook] 危险参数检查
                                      │           (焊接电流>500A → REJECT)
                                LLM 生成最终回复
                                      │
                                输出前 ──► [OutputGuardrail] 违规建议检查
                                      │           ("无需检测直接出厂" → REJECT)
                                      ▼
                                用户看到回复
```

### 2.6 其他关键机制

| 机制 | 位置 | 作用 |
|------|------|------|
| ToolFailureReflector | tool_execution.py | schema 校验失败自动修复参数，design_workflow 专用策略 |
| PolicyRatchet | governance/policy_ratchet.py | 失败模式 append-only 记录，草稿区等运维评审 |
| ContextCompaction | memory/compaction.py | 长对话接近 token 上限时 LLM 自总结压缩 |
| LLMResponseCache | capability | 同 session 同 prompt 缓存，避免重复调用 |
| SAME_TOOL_LIMIT=3 | react.py | 防止 LLM 循环调用同一工具 |
| SkillRegistry | skills.py | LLM 语义分类选 skill，限制工具白名单 |
| SubAgent delegation | delegate tool | 4 个子 agent 独立上下文处理复杂子任务 |

---

## 三、L2 控制平面 — DAG 编排与可靠性

### 3.1 拓扑排序 DAG 执行

```
WorkflowSpec (L1 产出)
    │
    ▼
topological_sort(nodes)
    │  检测: 重复 node_id / 未知依赖 / 环
    ▼
┌──────────────────────────────────────────────┐
│  for node in sorted_nodes:                    │
│                                               │
│    if node.type == "human_task":              │
│      → wait_condition(human_gate signal)      │
│      → ≤30min 超时 → FAILED                    │
│                                               │
│    if node.type == "tool_task":               │
│      → execute_node activity                  │
│      → ActivityPool.dispatch(capability)       │
│      → RetryPolicy(max=3, backoff=2.0s)       │
│                                               │
│    emit_workflow_event → HTTP POST → L1        │
│      → SSE → 前端实时显示节点进度                │
│                                               │
│    status = OK / MARGINAL / NG / ERROR        │
│    on_failure: abort → 终止                    │
│                escalate → 继续(标记失败)        │
│                continue → 继续                 │
│                retry → 继续                     │
└──────────────────────────────────────────────┘
```

### 3.2 双重 HumanGate

```
L1 层: ApprovalGate (工具层)          L2 层: HumanGate (执行层)
┌──────────────────────┐              ┌──────────────────────┐
│ launch_workflow 调用  │              │ human_task node 执行前 │
│ → asyncio.Event 阻塞  │              │ → Temporal signal 等待 │
│ → 前端弹窗确认         │              │ → @workflow.signal     │
│ → approved/rejected   │              │ → human_gate(node_id)  │
└──────────┬───────────┘              └──────────┬───────────┘
           │                                      │
           └────────── 两道独立校验 ──────────────┘
```

### 3.3 工作流控制信号

LLM 可通过 `workflow_control` 工具发 Temporal signal：

| signal | 作用 |
|--------|------|
| `pause` | 暂停工作流，等待 resume |
| `resume` | 恢复暂停的工作流 |
| `cancel_by_user` | 用户取消，返回 FAILED |

### 3.4 L2→L1 事件回传

```
Temporal workflow (sandbox, 不能直接 HTTP)
    │
    ▼
emit_workflow_event activity (可以 HTTP)
    │  POST /api/v1/chat/workflow/events
    ▼
L1 WorkflowEventBus
    │  按 session_id 路由
    ▼
前端 SSE /chat/workflow/stream/{session_id}
    │  实时显示节点执行进度
    ▼
WorkflowObserver → NotificationStore → 推送通知
```

---

## 四、L3 执行平面 — 工业视觉与黑板

### 4.1 ActivityPool 分派

```
execute_node(node_input)
    │
    ▼
ActivityPool.dispatch()
    │  capability 别名解析
    │  iqa/defect_detection/image_quality → "iqa"
    │  ppa/preprocess/image_preprocess → "ppa"
    │  annotation/label_studio → "annotation"
    ▼
┌──────────────────────────────────────────┐
│  已实现 (真实执行)                          │
│  ├─ IQA: CV规则 + MLLM(可选)              │
│  ├─ PPA: 自适应预处理                      │
│  └─ Annotation: Label Studio MCP           │
│                                           │
│  未实现 (mock fallback)                     │
│  ├─ MEA: 缺陷检测                          │
│  ├─ RDA: 渲染                              │
│  ├─ VDA: 验证                              │
│  ├─ RVA: 路由                              │
│  ├─ MTA: 模型                              │
│  └─ HCA: 人工审核                          │
│  → 返回 MARGINAL + mock=True (绝非 OK!)    │
└──────────────────────────────────────────┘
```

### 4.2 IQA 图像质量评估算法

```
输入: image_path
    │
    ▼
┌─────────────────────────────────────┐
│  1. 分辨率检查                        │
│     width×height ≥ 2048×1536?       │
│     → RuleResult (pass/fail)         │
│                                     │
│  2. 曝光检查                          │
│     mean_gray ∈ [45, 90]?           │
│     → RuleResult                     │
│                                     │
│  3. 对焦检查                          │
│     Laplacian 方差 ≥ 50?             │
│     → RuleResult                    │
│                                     │
│  4. 完整性检查                        │
│     焊缝区域是否存在? 裁切? 遮挡?      │
│     → RuleResult                    │
└──────────────┬──────────────────────┘
               │
               ▼  overall_confidence < 阈值?
┌─────────────────────────────────────┐
│  5. MLLM 深度视觉 (可选)              │
│     火山引擎 Doubao Vision            │
│     检测: 遮挡/污染/异物              │
│     → deep_vision_findings           │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  6. 路由决策                          │
│     AUTO_PASS (置信度高, 直接通过)    │
│     SUGGEST_REVIEW (建议复核)        │
│     MANDATORY_REVIEW (必须复核)      │
│     REJECT (拒绝, 质量太差)           │
└──────────────┬──────────────────────┘
               │
               ▼
    WeldMap.write(wf_id, "image/quality", report)
```

**技术思想：** 规则层为主（确定性 CV 函数 <10ms），MLLM 为辅（语义级检测，条件触发）。不把所有判断都交给 MLLM，保证确定性和速度。

### 4.3 WeldMap 黑板模式

```
                    ┌───────────────────────┐
          写入 ────►│  WeldMap (CAS + ES)    │◄──── 读取
                    │                       │
  IQA ──── write ─►│  image/quality/        │◄── read ──── PPA
                    │  mask/                │
  PPA ──── write ──►│  annotations/         │◄── read ──── Annotation
                    │  validation/          │
                    │  rendering/            │
                    │  decision/             │
                    └───────────────────────┘
                             │
                    CAS (Compare-And-Swap):
                    读时拿 version → 写时校验 version
                    冲突 → TRIGGER_CAS_CONFLICT → PolicyRatchet
                    
                    Event Sourcing:
                    每次写入产生 WeldMapEvent
                    StateWatcher 订阅通知
```

**技术思想：** Agent 间不直接通信，通过黑板共享状态。IQA 写质量报告 → PPA 读报告决定预处理策略。CAS 保证并发安全，Event Sourcing 支持审计回溯。

---

## 五、解耦边界分析

### 5.1 六大解耦点

```
解耦点 1: L1 ↔ L2 (WorkflowSpec DTO)
┌──────────────────────────────────────────────┐
│  L1 产出: WorkflowSpec (Pydantic/dataclass)  │
│  传输: dict (JSON 安全, 跨 Temporal 边界)     │
│  L2 消费: workflow_spec_from_dict()           │
│  镜像副本: cognitiveplane/shared/dto_workflow  │
│           controlplane/domain/workflow_spec    │
│  SSOT 校验: _SSOT_CHECKSUM 一致性断言          │
│  ✅ L1 不 import temporalio SDK                │
│  ✅ L2 不 import cognitiveplane                 │
└──────────────────────────────────────────────┘

解耦点 2: L2 ↔ L3 (ActivityPool + capability)
┌──────────────────────────────────────────────┐
│  L2 只传: capability 字符串 + input dict       │
│  L3 解析: ActivityPool.resolve(capability)   │
│  别名映射: iqa/defect_detection → "iqa"        │
│  注入方式: configure_activity_pool() 全局单例   │
│  ✅ L2 不感知 L3 具体实现                       │
│  ✅ L3 可独立替换 (换 CV 库/换 ML 模型)          │
└──────────────────────────────────────────────┘

解耦点 3: L1 ↔ MCP (MCPRegistry)
┌──────────────────────────────────────────────┐
│  MCPServer ABC: list_tools() / call_tool()    │
│  三层 ToolPolicy 判定: query/auto/approval     │
│  list_changed 事件: 动态发现工具变更            │
│  ✅ 外部工具热插拔 (Label Studio 等)            │
│  ✅ 工具策略自动分级                            │
└──────────────────────────────────────────────┘

解耦点 4: L3 ↔ WeldMap (黑板模式)
┌──────────────────────────────────────────────┐
│  WeldMapClient ABC: read/write/initialize     │
│  InMemoryWeldMapClient (开发)                  │
│  → 可换 RedisWeldMapClient (生产)              │
│  → 可换 MinIO+DB (分布式)                      │
│  ✅ Activity 间无直接依赖, 只依赖黑板契约        │
└──────────────────────────────────────────────┘

解耦点 5: L1 ↔ LLM (LLMProvider 接口)
┌──────────────────────────────────────────────┐
│  LLMProvider ABC: complete() / stream()       │
│  supports_function_calling / supports_vision  │
│  当前: DeepSeek (文本) + 火山引擎 (视觉)        │
│  ✅ 可换 OpenAI / Claude / 本地模型              │
│  ✅ 3-tier 回退自动适应能力差异                  │
└──────────────────────────────────────────────┘

解耦点 6: Skill ↔ 代码 (.weldevent/ 声明式)
┌──────────────────────────────────────────────┐
│  .weldevent/skills/*.md  (YAML frontmatter)   │
│  .weldevent/agents/*.md  (SubAgent 声明)      │
│  DeclarationLoader 统一加载                    │
│  ✅ 新增 skill 不改代码, 只加 MD 文件           │
│  ✅ skill = prompt + 工具白名单 + 触发词         │
└──────────────────────────────────────────────┘
```

### 5.2 解耦度评估

| 边界 | 解耦方式 | 耦合风险 | 评级 |
|------|---------|---------|------|
| L1→L2 | DTO + ABC Port | SSOT 双副本需手动同步 | ⭐⭐⭐⭐ |
| L2→L3 | 全局单例注入 | configure_activity_pool 全局可变 | ⭐⭐⭐ |
| L1→MCP | ABC + 动态发现 | 无直接耦合 | ⭐⭐⭐⭐⭐ |
| L3→WeldMap | ABC 接口 | InMemory 无持久化 | ⭐⭐⭐⭐ |
| L1→LLM | ABC 接口 | capability 探测分散 | ⭐⭐⭐⭐ |
| Skill | 声明式 MD | 无代码耦合 | ⭐⭐⭐⭐⭐ |

---

## 六、扩展能力分析

### 6.1 可扩展点全景

```
                        ┌──────────────┐
                 ┌──────│ 新增 Skill   │ MD 文件, 零代码
                 │      └──────────────┘
                 │      ┌──────────────┐
                 ├──────│ 新增 SubAgent │ MD 文件, delegate 调用
                 │      └──────────────┘
                 │      ┌──────────────┐
                 ├──────│ 新增 L1 工具  │ BrainTool 子类 + register
                 │      └──────────────┘
┌─────────┐      │      ┌──────────────┐
│  L1 扩展 │─────├──────│ 新增 MCP      │ MCPServer 实现 + register
└─────────┘      │      └──────────────┘
                 │      ┌──────────────┐
                 ├──────│ 新增护栏规则  │ 添加 regex pattern
                 │      └──────────────┘
                 │      ┌──────────────┐
                 └──────│ 换 LLM        │ LLMProvider 实现
                        └──────────────┘

                        ┌──────────────┐
                 ┌──────│ 新增 Activity │ BaseActivity 子类 + register
                 │      └──────────────┘
┌─────────┐      │      ┌──────────────┐
│  L3 扩展 │─────┼──────│ 换 WeldMap   │ WeldMapClient 实现 (Redis)
└─────────┘      │      └──────────────┘
                 │      ┌──────────────┐
                 └──────│ 新增 CV 规则  │ CVRuleChecker 扩展
                        └──────────────┘

┌─────────┐      ┌──────────────┐
│ L2 扩展  │─────│ 并行节点执行  │ Phase 4+ wait_condition 并发
└─────────┘      └──────────────┘

┌─────────┐      ┌──────────────┐
│ 基础设施 │─────│ 多进程部署    │ ApprovalStore → Redis
└─────────┘      └──────────────┘
```

### 6.2 扩展成本评估

| 扩展目标 | 需要改什么 | 改动范围 | 成本 |
|---------|-----------|---------|------|
| 新增一个 Skill | `.weldevent/skills/xxx.md` | 1 个文件 | 极低 |
| 新增一个 SubAgent | `.weldevent/agents/xxx.md` | 1 个文件 | 极低 |
| 新增一个 L1 工具 | BrainTool 子类 + register | 2 处 | 低 |
| 新增一个 L3 Activity | BaseActivity 子类 + register + pool | 3 处 | 中 |
| 接入新的 MCP Server | MCPServer 实现 + register | 2 处 | 中 |
| 换 LLM Provider | LLMProvider 实现 + bootstrap 配置 | 2 处 | 中 |
| 换 WeldMap 后端 | WeldMapClient 实现 | 1 个类 | 中 |
| 多进程部署 | ApprovalStore→Redis + WeldMap→Redis | 2 处 | 高 |
| 并行 DAG 节点 | dag_runner_workflow.py | 核心 workflow | 高 |

### 6.3 当前未实现/计划中的能力

| 能力 | 状态 | 影响 |
|------|------|------|
| MEA 缺陷检测 | mock fallback | 真实缺陷识别不可用 |
| RDA 渲染诊断 | mock fallback | 无可视化诊断 |
| VDA 验证决策 | mock fallback | 无自动验证 |
| RVA 路由分析 | mock fallback | 无智能路由 |
| MTA 模型推理 | mock fallback | 无 ML 推理 |
| HCA 人工审核 | mock fallback | 走 Temporal HumanGate |
| DAG 并行执行 | 串行实现 | 同层无依赖节点也串行 |
| WeldMap 持久化 | InMemory | 进程重启丢失 |
| ApprovalStore 多进程 | 进程内 dict | 多 worker 不可共享 |
| PolicyRatchet 正式规则 | 草稿区 only | 无自动生效 |
| Context Compaction 高级策略 | 基础实现 | 4 种策略可选 |

---

## 七、用户视角 — 能做到什么

### 7.1 已完整可用的用户场景

```
场景 1: 焊接标准查询
  用户: "查询 NB/T47014 中关于预热温度的规定"
  → search_standards → 返回标准条款
  → 或 search_reasoning_knowledge (RAG 向量检索)

场景 2: 焊缝图片分析
  用户: [上传图片] "这张图有没有问题"
  → analyze_image (MLLM 视觉理解)
  → 返回缺陷类型 + 建议

场景 3: 质检工作流设计 + 执行
  用户: "设计一个焊缝质检全流程"
  → design_workflow → 产出 DAG (IQA→PPA→Annotation)
  → launch_workflow → 架构弹窗确认
  → 用户确认 → Temporal 执行 → 节点进度实时推送
  → 工作流完成 → 自动汇报结果

场景 4: 交互式标注
  用户: "帮我标注这批焊缝图片"
  → list_datasets → 展示数据集
  → 弹窗: 用哪个数据集?
  → upload_images → 架构弹窗确认
  → create_job → 弹窗: 作业名/标签?
  → create_task → 弹窗: 标注员?
  → trigger_ai → 弹窗: 确认 AI 预标注?

场景 5: 大图分块标注
  用户: [上传 4K 大图] "这张图太大, 分块标注"
  → split_image (九宫格切分)
  → 分块创建标注任务 → 多人并行标注

场景 6: 项目诊断 (多轮追问)
  用户: "能不能帮我们做自动质检?"
  → 进入诊断流程, 逐轮追问:
    "你们的标准是什么?" → "数据有多少?" → "缺陷率多少?"
  → 基于回答给出技术路线建议

场景 7: 工作流控制
  用户: "暂停当前工作流"
  → workflow_control(pause) → Temporal signal → 工作流暂停
  用户: "继续" → resume signal

场景 8: 历史案例检索
  用户: "有没有类似夹渣缺陷的处理案例"
  → search_cases → 返回历史案例

场景 9: 联网搜索 (需审批)
  用户: "最新的焊缝检测标准有哪些"
  → web_search → 架构弹窗确认 → 联网搜索
```

### 7.2 降级场景 (LLM 不可用时)

```
LLM 完全挂掉:
  → Tier-3 关键词匹配仍可工作
  → "查询标准" → search_standards
  → "设计检测" → design_workflow (但无法智能编排, 走默认)
  → 回复格式化为友好文本

Temporal 挂掉:
  → L1 仍可工作 (问答/查图/标注)
  → launch_workflow 返回 "Temporal service unavailable"
  → health 端点显示 "degraded"

Label Studio 挂掉:
  → 标注工具不可用, 但 L1 app 正常启动
  → 其他功能不受影响
```

---

## 八、核心设计哲学总结

```
┌─────────────────────────────────────────────────────────────┐
│                    "LLM decides what to do;                  │
│              the architecture decides what cannot be done"   │
│                                                              │
│  固定 (架构决定):              动态 (LLM 决定):               │
│  ├─ 可用工具集                  ├─ 调哪个工具                  │
│  ├─ 安全边界 (审批门控)         ├─ 何时调用                    │
│  ├─ 记忆存取方式                ├─ 如何分析推理                 │
│  ├─ 工具执行顺序 (DAG)         ├─ 何时停止                    │
│  ├─ 护栏规则                   └─ 如何编排工作流               │
│  └─ 失败回退策略                                               │
│                                                              │
│  核心理念: 通过大量"架构替 LLM 做看不见的事"的工程化设计,       │
│  补强通用 ReAct 的脆弱点                                      │
└─────────────────────────────────────────────────────────────┘
```
