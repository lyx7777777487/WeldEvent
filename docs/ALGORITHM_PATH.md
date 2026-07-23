# WeldEvent 算法路径结构

> 基于 Magentic 双账本动态规划 + Temporal 耐久执行 + Pydantic AI Harness 能力化
> 融合 WORKFLOW_LIFECYCLE_DESIGN 的 8 设计域、18 前沿模式、60 用户场景
> 创建：2026-07-16

---

## 〇、文档定位

本文档回答一个问题：**从用户开口说话到拿到结果，系统内部的数据和决策沿着什么路径流转，每一步走什么算法，遇到岔路怎么选。**

三技术不是并列的"参考来源"，而是 L1/L2/L3 三层各自的的结构骨架：

- **Pydantic AI Harness** 是 L1 的运行时结构 -- Agent 不是"一个 ReAct loop + 一堆工具"，而是由可组合 capability 构成的 harness。每次执行都走 capability 生命周期，Step Persistence 贯穿全程，Planning 驱动认知决策，Compaction 管控上下文，Guardrails 守卫每个边界。
- **Magentic-One** 是 L1 的认知策略 -- 双账本循环（Task Ledger + Progress Ledger）跑在 Harness 之上，Specialist 委派是 Harness 的 subagent capability，重规划是 Planning capability 的状态转换。
- **Temporal** 是 L2 的耐久执行底座 -- 接收 L1 冻结的 PlanVersion，用 NodeAttempt 四阶段可靠执行每个节点。

不是"借鉴三者思想"，而是"用三者构建系统"。

---

## 一、全局路径总览

```
用户输入
  │
  ▼
┌═════════════════════════════════════════════════════════════════════┐
║  L1 Cognitive Plane -- Pydantic AI Harness 运行时                    ║
║                                                                       ║
║  ┌─────────────────────────────────────────────────────────────┐     ║
║  │  Harness Capability 生命周期 (每次请求都走一遍)                │     ║
║  │                                                             │     ║
║  │  InputGuardrail ──► ModelRequestTransform ──► LLM 调用        │     ║
║  │       │                    │                    │            │     ║
║  │  (allow/block/         (注入 plan reminder    (ReAct 核心)    │     ║
║  │   replace)               + session notes                    │     ║
║  │                          + workflow status)                 │     ║
║  │       │                    │                    │            │     ║
║  │       ▼                    ▼                    ▼            │     ║
║  │  StepPersistence ──► StepPersistence ──► StepPersistence     │     ║
║  │  (AgentRunStarted)    (ModelRequested)    (ToolStarted)     │     ║
║  │                                                             │     ║
║  │       ┌──────────────────────────────────────┐              │     ║
║  │       │  Tool 执行                            │              │     ║
║  │       │  ┌─ToolEffectRecord: started         │              │     ║
║  │       │  ├─Tool 执行 (search/design/delegate)│              │     ║
║  │       │  ├─AfterToolHook: allow/block/retry  │              │     ║
║  │       │  └─ToolEffectRecord: completed/     │              │     ║
║  │       │     failed/unknown_after_crash       │              │     ║
║  │       └──────────────┬───────────────────────┘              │     ║
║  │                      │                                      │     ║
║  │  ContinuableSnapshot (tool-call/return 配对完整时保存)        │     ║
║  │  Compaction (token 预算接近上限时触发)                       │     ║
║  │  OutputGuardrail (allow/block/replace/retry)                │     ║
║  │                      │                                      │     ║
║  │  StepPersistence: AgentRunCompleted                          │     ║
║  └──────────────────────┼──────────────────────────────────────┘     ║
║                         │                                             ║
║    ┌────────────────────┼────────────────────┐                       ║
║    │            意图分类结果                    │                       ║
║    └────────────┬───────┬──────────┬─────────┘                       ║
║                 │       │          │                                  ║
║              qa    workflow   supplement/control                      ║
║                 │       │          │                                  ║
║    ┌────────────┘       │          └──► inject_context /             ║
║    │                    │               control_workflow signal        ║
║    ▼                    ▼              (直接送 L2 STAGE 4)             ║
║  直接问答          STAGE 2: 动态规划                                    ║
║  (Harness 内执行    (Magentic 双账本                                    ║
║   单轮 ReAct)        跑在 Harness 上)                                   ║
║                                                                       ║
║  ┌──────────────────────────────────────────────────┐                 ║
║  │  STAGE 2: Magentic 双账本循环 (Harness subagent)   │                 ║
║  │                                                    │                 ║
║  │  Planning capability 驱动 Task Ledger 构建          │                 ║
║  │  Subagent capability 委派 Specialist               │                 ║
║  │    每个 Specialist 是一个独立 AgentRun               │                 ║
║  │    parent_run_id = Manager run_id                   │                 ║
║  │    每个 run 走完整 Harness 生命周期                  │                 ║
║  │  Progress Ledger 从 StepEvent 流投影                 │                 ║
║  │  重规划 = Planning capability 状态转换               │                 ║
║  │  产出: PlanDraft + ContextManifest                 │                 ║
║  └──────────────────────┬─────────────────────────────┘                 ║
║                         │                                              ║
║  ┌──────────────────────▼─────────────────────────────┐                 ║
║  │  STAGE 3: 计划冻结                                   │                 ║
║  │  PlanDraft -> PlanVersion (不可变)                  │                 ║
║  │  ContextManifest (冻结上下文快照)                    │                 ║
║  │  DecisionRecord 记录审批                              │                 ║
║  └──────────────────────┬─────────────────────────────┘                 ║
╚═════════════════════════╪═════════════════════════════════════════════╝
                          │ PlanVersion (不可变契约)
┌═════════════════════════▼═════════════════════════════════════════════┐
║  L2 Control Plane -- Temporal 耐久执行                                 ║
║                                                                       ║
║  ┌──────────────────────────────────────────────────────────┐        ║
║  │  STAGE 4: 执行调度                                        │        ║
║  │                                                          │        ║
║  │  拓扑排序 -> 并行分层                                     │        ║
║  │  per-node: NodeAttempt 四阶段                              │        ║
║  │    Prepare -> Execute -> Validate -> Commit               │        ║
║  │                                                          │        ║
║  │  信号: pause/resume/cancel/                               │        ║
║  │    human_review/inject_context/rework_node               │        ║
║  └──────────────────────┬───────────────────────────────────┘        ║
║                         │                                            ║
║  ┌──────────────────────▼───────────────────────────────────┐        ║
║  │  STAGE 5: 结果聚合与学习                                   │        ║
║  │  ArtifactVersion 汇总                                     │        ║
║  │  WorkflowExecutionRecord -> Memory                        │        ║
║  └──────────────────────────────────────────────────────────┘        ║
╚═════════════════════════════════════════════════════════════════════╝
                          │
┌═════════════════════════▼═════════════════════════════════════════════┐
║  L3 Execution Plane                                                    ║
║  ActivityPool: IQA / PPA / Annotation / Compensator                    ║
║  WeldMap Blackboard: 业务数据 / 执行控制 / 版本审计                     ║
╚═════════════════════════════════════════════════════════════════════╝
```

---

## 二、Harness 运行时结构（L1 骨架）

L1 不是"一个 ReAct loop 加工具列表"。它是 Pydantic AI Harness 风格的可组合 capability 体系。每个请求都走 capability 生命周期，五种 capability 横向贯穿所有 Stage。

### 2.1 Capability 模型

一项 capability 可以贡献：工具、静态指令、生命周期 hook、每次运行的局部状态、对 model request 的变换、对 tool/output 的处理。

```
┌─────────────────────────────────────────────────────────────┐
│  Harness Capability 组合体                                   │
│                                                             │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐           │
│  │ Planning   │  │ Step       │  │ Compaction │           │
│  │ capability │  │ Persistence│  │ capability │           │
│  │            │  │ capability │  │            │           │
│  │ ·write_plan│  │ ·StepEvent │  │ ·tool-pair │           │
│  │  工具       │  │  append    │  │  检查       │           │
│  │ ·cache-tail│  │ ·Snapshot  │  │ ·sliding   │           │
│  │  reminder  │  │  (provider │  │  window    │           │
│  │ ·状态机:    │  │  valid)    │  │ ·大输出剥离 │           │
│  │  pending/  │  │ ·Effect    │  │ ·结构化总结 │           │
│  │  in_prog/  │  │  Ledger   │  │ ·预算预警   │           │
│  │  completed │  │  (含       │  │            │           │
│  │            │  │  unknown_  │  │            │           │
│  │ 驱动:      │  │  after_    │  │            │           │
│  │ ·单轮 plan │  │  crash)    │  │            │           │
│  │ ·Magentic  │  │            │  │            │           │
│  │  双账本    │  │贯穿每次     │  │贯穿每次     │           │
│  │  Task/Prog │  │请求全程     │  │请求全程     │           │
│  │  Ledger    │  │            │  │            │           │
│  └────────────┘  └────────────┘  └────────────┘           │
│                                                             │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐           │
│  │ Guardrails │  │ Subagent   │  │ Media      │           │
│  │ capability │  │ capability │  │ capability │           │
│  │            │  │            │  │            │           │
│  │ ·Input:    │  │ ·delegate  │  │ ·image_ref │           │
│  │  allow/    │  │  工具       │  │  内容寻址  │           │
│  │  block/    │  │ ·parent_   │  │ ·不塞入    │           │
│  │  replace   │  │  run_id    │  │  history   │           │
│  │ ·AfterTool:│  │  lineage  │  │ ·只传引用  │           │
│  │  allow/    │  │ ·每个      │  │            │           │
│  │  block/    │  │  subagent  │  │            │           │
│  │  retry     │  │  走完整    │  │            │           │
│  │ ·Output:   │  │  Harness   │  │            │           │
│  │  allow/    │  │  生命周期   │  │            │           │
│  │  block/    │  │            │  │            │           │
│  │  retry     │  │            │  │            │           │
│  └────────────┘  └────────────┘  └────────────┘           │
│                                                             │
│  守卫每个 Stage 边界    驱动 STAGE 2   管控大文件             │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 单次请求的 Capability 生命周期

每次用户消息进入 L1，都走以下流程。这不是"额外的处理步骤"，而是 Harness 的执行结构：

```
用户消息到达
    │
    ▼
1. InputGuardrail (Guardrail capability)
   ├─ prompt injection 检测 -> BLOCK (S6-6)
   ├─ 敏感信息 -> REPLACE (脱敏)
   └─ ALLOW
    │
    ▼
2. ModelRequestTransform (Planning + Compaction + Media capability)
   ├─ Compaction: token 预算检查
   │   ├─ > 80% -> 剥离大输出 / 截断
   │   ├─ > 90% -> 强制压缩
   │   └─ 确保 tool-call/tool-return 配对完整
   ├─ Planning: 注入 AgentWorkPlan (cache-tail reminder)
   │   └─ 计划不写入 durable history, 只在 prompt 尾部
   ├─ Media: image_ref 替换 (不塞 base64 进 history)
   └─ Session notes + workflow status 注入
    │
    ▼
3. StepPersistence: AgentRunStarted
   ├─ run_id = uuid4()
   ├─ parent_run_id = 上层的 run_id (如果有 subagent 委派)
   └─ conversation_id = session_id
    │
    ▼
4. LLM 调用 (ReAct 核心)
   ├─ StepPersistence: ModelRequested
   ├─ LLM 返回: tool_call 或 final_answer
   └─ 如果 tool_call -> 步骤 5
       如果 final_answer -> 步骤 8
    │
    ▼
5. Tool 执行
   ├─ StepPersistence: ToolStarted
   ├─ ToolEffectRecord: status = "started"
   │   ├─ idempotency_key (如果有关联的 L2 NodeAttempt)
   │   └─ target (被操作的资源 ID)
   ├─ ── 执行工具 (search/design/delegate/analyze/...)
   │   └─ 如果是 delegate: 启动子 AgentRun (步骤 3 递归)
   ├─ ToolEffectRecord: status = "completed" | "failed" | "unknown_after_crash"
   ├─ AfterToolHook (Guardrail capability):
   │   ├─ 输出含敏感数据 -> REPLACE
   │   ├─ 输出异常 -> BLOCK + error 给 LLM
   │   ├─ 输出可改善 -> RETRY + instruction
   │   └─ ALLOW
   └─ StepPersistence: ToolCompleted/ToolFailed
    │
    ▼
6. ContinuableSnapshot (Step Persistence capability)
   ├─ 只在 tool-call/tool-return 配对完整时保存
   ├─ 确保 provider 接受的 history 是合法的
   ├─ 用于崩溃后从此点安全续跑
   └─ 不恢复 capability state / retry counter (由 Temporal 管)
    │
    ▼
7. 回到步骤 2 (下一轮 ReAct 迭代)
   └─ ModelRequestTransform 重新注入 (plan 可能已更新)
    │
    (final_answer 时)
    ▼
8. OutputGuardrail (Guardrail capability)
   ├─ 幻觉/不当建议 -> BLOCK
   ├─ 可优化 -> RETRY + instruction
   ├─ 合规 -> ALLOW
   └─ ⚠️ 流式已发出的内容无法撤回
    │
    ▼
9. StepPersistence: AgentRunCompleted
   ├─ 完整 StepEvent 轨迹已记录
   ├─ ToolEffectRecord 已更新
   └─ ContinuableSnapshot 已保存
    │
    ▼
10. 返回给用户
```

### 2.3 关键设计约束

- **Planning 的 AgentWorkPlan 不等于 PlanVersion**：AgentWorkPlan 是 L1 内部可变的认知工作状态（一个 AgentRun 内），PlanVersion 是跨层不可变的执行契约（用户批准后）。AgentWorkPlan 编译为 PlanDraft，再冻结为 PlanVersion。

- **ContinuableSnapshot 不是完整 checkpoint**：不会恢复 capability state、graph node state、retry counter 或流式中间状态。这些由 Temporal Activity 管理。它只保证 provider 接受的 history 合法。

- **ToolEffectRecord 的 unknown_after_crash 不等于"没执行"**：不假装没执行，不盲目重试。需要人工确认或用幂等键检查外部系统状态。

- **Compaction 不等于丢失审计**：剥离的大输出移到外部存储（Artifact/WeldMap/EventLog），保留 reference。结构化总结生成 SessionSummary（含来源范围、模型版本、hash），不是匿名 assistant message。

---

## 三、STAGE 1: 意图理解与需求解析

### 3.1 算法入口

用户消息进入 Harness capability 生命周期（Section 2.2）。在 InputGuardrail 和 ModelRequestTransform 之后，ReAct LLM 循环的第一步是意图分类。

### 3.2 意图分类算法

```
输入: user_message + session_history + image_refs + workflow_event_bus_status
输出: UserIntent {
  type: "qa" | "workflow_design" | "workflow_control" | "workflow_supplement" | "annotation"
  goal: str
  scope: str | None
  constraints: dict
  image_refs: list[str]
  active_workflow_id: str | None
}

算法:
  1. 检查是否有正在运行的 workflow (WorkflowEventBus.get_session_workflow_summary)
     └─ 有 + 用户说"暂停/继续/取消/进度" -> type = "workflow_control"
     └─ 有 + 用户补充信息(标准/参数/上下文) -> type = "workflow_supplement"
     └─ 有 + 用户问无关问题 -> type = "qa" (S3-11 场景)

  2. 检查是否涉及工业操作
     └─ 用户提到"检测/质检/工作流/跑一遍/处理" -> type = "workflow_design"
     └─ 用户提到"标注/标数据/标注任务" -> type = "annotation"
     └─ 其他 -> type = "qa"

  3. 信息完备性检查 (S0-1 场景)
     └─ workflow_design 但缺 image_refs -> 弹窗要求上传
     └─ workflow_design 但 scope 不明 -> request_confirmation 弹窗
     └─ 缺标准 -> 默认 GB/T 3323 + 弹窗确认

  4. 历史复用检查 (S0-4 场景)
     └─ session_history 中有未启动的 spec -> 建议复用
     └─ Memory 中有类似 pattern -> 建议参考
```

### 3.3 Skill 匹配

意图分类后，Skill Registry 根据 triggers 匹配对应 skill：

```
1. 短输入 (< 5 tokens): LLM 分类 + keyword fallback
2. 正常输入: keyword 优先匹配 + LLM 确认
3. 多 skill 匹配: 取 priority 最高的
4. 无匹配: 走 general mode (所有 phase-visible 工具可用)
```

### 3.4 关键路径分支

```
type == "qa"                    -> 直接问答 (Harness 内单轮 ReAct, 工具执行走 §2.2 生命周期)
type == "workflow_design"      -> 进入 STAGE 2
type == "workflow_control"     -> control_workflow 工具 -> L2 signal -> 回到 STAGE 4
type == "workflow_supplement"  -> inject_context 工具 -> L2 signal -> 回到 STAGE 4
type == "annotation"           -> annotation_interactive skill (交互式标注)
```

### 3.5 Harness 能力在此 Stage 的作用

| Capability | 作用 |
|-----------|------|
| InputGuardrail | prompt injection 拦截 (S6-6) |
| Planning | AgentWorkPlan 初始化 (如果是多轮 ReAct) |
| StepPersistence | 记录 AgentRunStarted, 意图分类结果 |
| Compaction | 如果 session_history 过长, 先压缩 |
| Media | image_refs 注入到 prompt, 不塞 base64 |

---

## 四、STAGE 2: 动态规划（Magentic 双账本，跑在 Harness 上）

### 4.1 何时启动双账本

```
简单路径 (单轮 design_workflow, Harness 内一步完成):
  └─ LLM 能在第一轮确定性地选择 capability 并构建 spec
     例: "检查这张焊缝图" -> IQA -> PPA, 直接设计

复杂路径 (启动 Magentic 双账本):
  └─ LLM 无法确定最优方案，需要多轮探索
     例: "评估这批数据质量并制定检测方案"

判断算法:
  1. 需求含"评估/分析/制定方案/建议"等探索性动词 -> 复杂
  2. 涉及多个数据集或大量图像 (> 10 张) -> 复杂
  3. 涉及标注 + 训练 + 评估组合 -> 复杂
  4. 其他 -> 简单
```

### 4.2 双账本与 Harness 的关系

Magentic 双账本不是独立于 Harness 的子系统，它跑在 Harness 的 Planning + Subagent capability 之上：

```
Magentic 概念              Harness capability 映射

Task Ledger          <──   Planning capability 的持久状态
                           write_plan 工具写入
                           cache-tail reminder 注入

Progress Ledger      <──   StepPersistence 的 StepEvent 流投影
                           从 AgentRun + ToolEffectRecord 聚合

Specialist 委派      <──   Subagent capability (delegate 工具)
                           每个 Specialist 是独立 AgentRun
                           parent_run_id = Manager run_id
                           每个 run 走完整 §2.2 生命周期

重规划               <──   Planning capability 状态转换
                           AgentWorkPlan 状态机: pending -> re-plan

方案收敛             <──   Planning capability 输出 PlanDraft
                           编译为 PlanVersion (STAGE 3)
```

### 4.3 外循环: Task Ledger 构建（Planning capability 驱动）

```
输入: UserIntent
输出: TaskLedger

TaskLedger = {
  ledger_id: str (uuid)
  goal: str
  facts: list[str]             # 从 StepPersistence 的历史 StepEvent 提取
  subtasks: list[SubTask]      # LLM 分解
  constraints: dict
  version: int                  # 重规划时递增
}

算法:
  1. goal 直接取自 UserIntent.goal
  2. facts 从 session_history + image_refs + Memory 检索
     - 已上传图像数量/分辨率 (Media capability 提供 image_refs)
     - 已有标注覆盖度
     - 历史执行记录 (search_workflow_patterns)
  3. subtasks 由 LLM 分解 (通过 Planner specialist 的 ReAct 循环)
     每个子任务: { id, description, specialist, status: "pending" }
  4. constraints 从 UserIntent.constraints 提取
     缺失的约束用默认值 + 标注"需确认"

  Harness 交互:
  - write_plan(task_ledger) -> AgentWorkPlan 更新
  - cache-tail reminder 让 LLM 始终看到当前 ledger
  - StepPersistence 记录 TaskLedger 版本变更
```

### 4.4 内循环: Specialist 委派（Subagent capability 驱动）

```
输入: TaskLedger + ProgressLedger
输出: 更新后的 ProgressLedger + 子任务结果

ProgressLedger = {
  completed: list[{subtask_id, specialist, result_ref}]
  in_progress: SubTask | None
  blocked: list[{subtask_id, reason}]
  next: SubTask | None
  delegations: list[{agent, run_id, parent_run_id, result_ref}]
  stall_count: int
}

内循环算法 (最多 N 轮):
  for iteration in range(MAX_INNER_ITERATIONS=10):

    1. 选择下一个 Specialist
       next = ProgressLedger.next or first pending subtask
       如果都完成 -> 外循环检查是否达目标

    2. 委派给 Specialist (Subagent capability)
       ┌─────────────────────────────────────────────────┐
       │  delegate 工具启动子 AgentRun:                     │
       │  - run_id = uuid4()                             │
       │  - parent_run_id = Manager run_id                │
       │  - 子 run 走完整 §2.2 Harness 生命周期:           │
       │    InputGuardrail -> ModelRequestTransform ->    │
       │    StepPersistence(AgentRunStarted) ->            │
       │    LLM 调用 -> Tool 执行 ->                       │
       │    AfterToolHook -> ContinuableSnapshot ->       │
       │    OutputGuardrail ->                             │
       │    StepPersistence(AgentRunCompleted)            │
       │                                                  │
       │  Specialist 角色:                                 │
       │  ┌──────────────┐  schema/质量/分布画像            │
       │  │ Profiler     │  工具: analyze_image/           │
       │  │              │         analyze_dataset          │
       │  ├──────────────┤                                  │
       │  │ Strategist   │  标签/抽样/预标注方案             │
       │  │              │  工具: search_cases/            │
       │  │              │         search_standards         │
       │  ├──────────────┤                                  │
       │  │ Critic       │  风险/预算/副作用审查            │
       │  │              │  工具: web_search/               │
       │  │              │         read_weldmap              │
       │  ├──────────────┤                                  │
       │  │ Planner      │  方案设计/方案比较               │
       │  │              │  工具: design_workflow           │
       │  └──────────────┘                                  │
       └─────────────────────────────────────────────────┘

    3. 获取 Specialist 观察结果
       result_ref = specialist_run.result
       result 含: findings, evidence_ids, recommendations

       ProgressLedger 从 StepEvent 流投影:
       completed.append({subtask_id, specialist, result_ref})
       delegations.append({agent, run_id, parent_run_id, result_ref})

    4. 重规划检测 (Planning capability 状态转换)
       ┌─────────────────────────────────────────────────┐
       │  检测条件:                                       │
       │  a. Specialist 返回失败或矛盾结论                 │
       │  b. stall_count >= 3 (连续 3 轮无新进展)          │
       │  c. 用户中途补充了新约束(STAGE 1 检测到)          │
       │  d. Specialist 发现 TaskLedger 的事实有误         │
       │                                                  │
       │  触发重规划:                                     │
       │  1. AgentWorkPlan 状态 -> re-plan                │
       │  2. 更新 TaskLedger (version++)                  │
       │  3. 重新分解 subtasks                            │
       │  4. 已完成的结果保留(不重复执行)                 │
       │  5. 重置 stall_count                             │
       │  6. StepPersistence 记录重规划事件               │
       └─────────────────────────────────────────────────┘

    5. 收敛检测
       ┌─────────────────────────────────────────────────┐
       │  收敛条件:                                       │
       │  a. 所有 subtasks completed                      │
       │  b. Planner 产出了至少一个可行 WorkflowSpec       │
       │  c. Critic 审查通过(无高风险项)                   │
       │                                                  │
       │  收敛 -> 编译 PlanDraft (步骤 4.5)               │
       │  未收敛且未超迭代上限 -> 继续内循环               │
       │  超迭代上限 -> 用当前最佳方案编译 PlanDraft       │
       └─────────────────────────────────────────────────┘
```

### 4.5 编译: PlanDraft

```
输入: TaskLedger (最终版) + ProgressLedger (最终版) + Specialist 结果集
输出: PlanDraft

PlanDraft = {
  draft_id: str
  task_ledger: TaskLedger (snapshot)
  progress_ledger: ProgressLedger (snapshot)
  workflow_spec: WorkflowSpec       # Planner specialist 产出
  context_manifest: ContextManifest # Compaction capability 冻结的上下文
  budget_estimate: { tokens, cost_usd, estimated_duration_s }
  risk_assessment: str              # Critic specialist 产出
  alternatives: list[WorkflowSpec]  # 备选方案
  evidence: list[EvidenceRef]      # StepPersistence 中的证据引用
}

ContextManifest = {
  manifest_id: str
  llm_model: str
  llm_version: str
  session_summary_hash: str         # Compaction 生成的 summary hash
  skill_context: str
  image_refs: list[str]             # Media capability 的引用
  standards: list[str]
  tool_versions: dict
  created_at: str
}
```

### 4.6 安全约束

Magentic manager 不可自行：
- 新增高成本节点（必须走 PlanVersion 审批）
- 发布/覆盖标签集（必须走 L2 policy gate）
- 修改已批准的 PlanVersion（必须发 PlanRevisionProposal）
- 跨任务推广单次反馈学到的策略（必须走 PolicyCandidate 离线验证）

---

## 五、STAGE 3: 计划冻结与审批

### 5.1 冻结算法

```
输入: PlanDraft + 用户 approve 决策
输出: PlanVersion (不可变)

PlanVersion = {
  version: int
  parent_version: int | None
  plan_draft: PlanDraft (frozen)
  workflow_spec: WorkflowSpec (frozen)
  context_manifest: ContextManifest (frozen)
  budget_estimate: dict (frozen)
  risk_assessment: str (frozen)
  approved_by: str
  approved_at: str
  status: "approved" | "superseded" | "cancelled"
}

冻结后:
  - workflow_spec 不可修改
  - context_manifest 不可修改
  - DecisionRecord 记录审批事件 (不可变)
  - 提交给 L2 Temporal 执行
  - 修改只能通过 PlanRevisionProposal -> 新 PlanVersion
```

### 5.2 审批交互

```
PlanDraft 生成 -> emit plan_review event -> 前端弹窗

弹窗内容: objective + nodes + budget + risk + alternatives + evidence

用户选项:
  [确认启动]         -> PlanVersion(approved) -> STAGE 4
  [编辑参数后启动]   -> 修改 spec -> 重新冻结
  [换一个方案]       -> 用 alternative -> 重新冻结
  [拒绝]             -> spec 标记 rejected -> 回 STAGE 2 或结束
  (5min 超时)        -> S2-1: 保留 spec 30min, 告知可随时启动
```

### 5.3 PlanRevisionProposal（执行中修改方案）

```
触发: 工作流执行中, L1 发现需要修改

算法:
  1. L1 不能直接修改正在运行的 Temporal workflow
  2. L1 生成 PlanRevisionProposal (含 changes + impact_analysis)
  3. Planning capability 更新 AgentWorkPlan 状态 -> re-plan
  4. 弹窗给用户审批
  5. 批准 -> 新 PlanVersion (version++) -> cancel+relaunch
  6. 拒绝 -> 继续用原 PlanVersion

  StepPersistence 记录修改决策链。
  DecisionRecord 记录用户的选择。
```

---

## 六、STAGE 4: 执行调度（Temporal DAG Runner + NodeAttempt）

### 6.1 拓扑排序与并行分层

```
输入: WorkflowSpec.nodes
输出: list[list[WorkflowNode]]  # 分层, 同层可并行

算法:
  1. 按 depends_on 构建依赖图
  2. Kahn 算法分层:
     Layer 0 = 无依赖节点
     Layer N = 依赖 Layer 0..N-1 中某些节点的节点
  3. 同层无依赖关系 -> 可并行

  4. 条件分支检查:
     for node in layer:
       if node.condition:
         if not evaluate_condition(node.condition, upstream_results):
           skip node (SKIPPED)
           下游 depends_on 此节点的也跳过

  5. 幂等检查 (resume_from 场景):
     for node in layer:
       if checkpoint exists and result valid:
         复用 checkpoint 结果, 跳过执行

  状态隔离 (MAF Superstep 借鉴):
  - 并行节点各自有独立 NodeExecutionState
  - WeldMap 写入只在 Commit 阶段发生
  - 并行节点不在同一步修改同一份 WeldMap 数据
```

### 6.2 NodeAttempt 四阶段算法（核心）

```
┌─────────────────────────────────────────────────────────────────┐
│  NodeAttempt 生命周期                                             │
│                                                                 │
│  attempt_id = f"{workflow_id}:{node_id}:{attempt_number}"       │
│  idempotency_key = attempt_id                                   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────┐       │
│  │  Phase A: PREPARE                                    │       │
│  │                                                      │       │
│  │  1. 依赖检查: 所有 depends_on 节点已 Commit?          │       │
│  │     └─ 未完成 -> wait_condition                       │       │
│  │     └─ 已 SKIPPED -> 用默认值替代                      │       │
│  │  2. 条件检查: node.condition 求值                     │       │
│  │     └─ False -> 标记 SKIPPED, 返回                   │       │
│  │  3. 注入合并:                                        │       │
│  │     node.input = {**node.input, **injected_context} │       │
│  │     只合并尚未执行节点的注入                         │       │
│  │  4. 冻结输入版本:                                    │       │
│  │     frozen_input = deepcopy(node.input)             │       │
│  │     frozen_manifest = ContextManifest.snapshot()    │       │
│  │  5. 生成幂等键:                                      │       │
│  │     idempotency_key = f"{wf_id}:{node_id}:{attempt}"│       │
│  │  6. 预算检查:                                        │       │
│  │     if token_budget and used + est > budget:        │       │
│  │       -> emit budget_warning                         │       │
│  │       -> review_policy 升级为 REQUIRED               │       │
│  │  7. Checkpoint 检查 (resume 场景):                   │       │
│  │     if checkpoint.result.valid:                     │       │
│  │       -> 复用结果, 跳到 Phase D (Commit)             │       │
│  └─────────────────────────────────────────────────────┘       │
│                          │                                      │
│                          ▼                                      │
│  ┌─────────────────────────────────────────────────────┐       │
│  │  Phase B: EXECUTE                                    │       │
│  │                                                      │       │
│  │  1. emit node_start event -> SSE -> 前端             │       │
│  │  2. ToolEffectRecord: status="started"               │       │
│  │     (关联 idempotency_key)                            │       │
│  │  3. 调用 L3 ActivityPool.dispatch(node_input)        │       │
│  │     ┌─ 幂等检查:                                      │       │
│  │     │  if idempotency_key in effect_ledger:          │       │
│  │     │    return cached_result                        │       │
│  │     └─ 执行 activity:                                │       │
│  │        - IQA: CV 规则 + 可选 MLLM                    │       │
│  │        - PPA: 读 IQA ArtifactVersion -> 调整策略     │       │
│  │        - Annotation: Label Studio MCP               │       │
│  │     - heartbeat 进度推送:                            │       │
│  │       heartbeat({progress, stage, message})         │       │
│  │     - 只写 draft artifact (不 Commit)              │       │
│  │  4. 超时处理:                                        │       │
│  │     start_to_close_timeout=60s                       │       │
│  │     └─ 超时 -> Cancel -> RetryPolicy 重试             │       │
│  │  5. ToolEffectRecord 更新:                            │       │
│  │     └─ 成功: "completed"                             │       │
│  │     └─ 失败: "failed"                               │       │
│  │     └─ 崩溃: "unknown_after_crash"                  │       │
│  │     (unknown_after_crash: 不假装没执行,不盲目重试)   │       │
│  │  6. emit node_end event (含结果摘要)                 │       │
│  └─────────────────────────────────────────────────────┘       │
│                          │                                      │
│                          ▼                                      │
│  ┌─────────────────────────────────────────────────────┐       │
│  │  Phase C: VALIDATE                                   │       │
│  │                                                      │       │
│  │  1. Schema 校验:                                     │       │
│  │     result.data 是否符合 capability output_schema    │       │
│  │     └─ 不符合 -> status=ERROR -> on_failure          │       │
│  │  2. Status 映射: OK / MARGINAL / NG / ERROR          │       │
│  │  3. ReviewPolicy 决策:                               │       │
│  │     ┌──────────────────────────────────────┐         │       │
│  │     │ AUTO:       直接通过 -> Phase D       │         │       │
│  │     │ REQUIRED:   暂停, 等待 human_review   │         │       │
│  │     │ CONDITIONAL:                           │         │       │
│  │     │   OK -> auto 通过                     │         │       │
│  │     │   MARGINAL/NG -> 暂停等 human_review   │         │       │
│  │     │ NEVER:     直接通过 (纯查询节点)       │         │       │
│  │     └──────────────────────────────────────┘         │       │
│  │  4. 人工审查 (如果暂停):                              │       │
│  │     emit node_review_request -> 前端审查卡片          │       │
│  │     -> wait_condition(human_review signal)           │       │
│  │     -> 超时 30min -> auto cancel                      │       │
│  │     审查结果 ReviewResult:                            │       │
│  │     ┌──────────────────────────────────────┐         │       │
│  │     │ APPROVE:           -> Phase D         │         │       │
│  │     │ REWORK:            -> 新 NodeAttempt  │         │       │
│  │     │   (改参数重跑, attempt++)             │         │       │
│  │     │ MODIFY_DOWNSTREAM:  -> Phase D         │         │       │
│  │     │   + 修改下游 node.input               │         │       │
│  │     │ REJECT:            -> on_failure       │         │       │
│  │     │ ESCALATE:          -> 暂停整个 workflow │         │       │
│  │     └──────────────────────────────────────┘         │       │
│  └─────────────────────────────────────────────────────┘       │
│                          │                                      │
│                    (审查通过 / AUTO)                              │
│                          ▼                                      │
│  ┌─────────────────────────────────────────────────────┐       │
│  │  Phase D: COMMIT                                     │       │
│  │                                                      │       │
│  │  1. 原子登记 ArtifactVersion:                        │       │
│  │     artifact = ArtifactVersion(                       │       │
│  │       version_id=uuid4(),                            │       │
│  │       parent_version=last_committed,                 │       │
│  │       node_id, attempt, data,                        │       │
│  │       lineage=Lineage(                               │       │
│  │         inputs=[frozen_manifest, dep_artifacts],     │       │
│  │         transform=capability,                        │       │
│  │         outputs=[artifact_version_id],               │       │
│  │       ),                                             │       │
│  │     )                                                │       │
│  │  2. 写入 WeldMap (原子):                              │       │
│  │     weldmap.write(workflow_id, node_id, artifact)   │       │
│  │  3. 更新 NodeExecutionState: status="committed"      │       │
│  │  4. DecisionRecord: decision="commit"                │       │
│  │     (actor="system" 或 "user" 如果经审查)             │       │
│  │  5. emit node_committed event                        │       │
│  │  6. 下一节点可从此 ArtifactVersion 读取               │       │
│  └─────────────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────────────┘
```

### 6.3 on_failure 决策算法

```
节点失败 (status=NG/ERROR/超时/异常)
    │
    ▼
错误分类:
    瞬时故障 (timeout/connection/rate limit)
    -> retry: 新 NodeAttempt, 相同参数, attempt++
       RetryPolicy: max=3, backoff=1s/2s/4s
       失败 >= 3 次 -> 死信队列 (隔离 + 跳过 + 下游用默认值)

    参数错误 (schema_invalid/not_found)
    -> rework: 新 NodeAttempt + 改参数
       emit node_rework_request -> 前端弹窗
       用户提供新参数 -> rework_node signal

    能力不足 (capability failed repeatedly)
    -> fallback: 换 capability 新 NodeAttempt
       链: iqa(MLLM) -> iqa_cv_only -> skip
       结果标注 "降级执行" (不假装正常)

    业务异常 (NG result)
    -> escalate: 暂停 workflow, 等人工决策

    可忽略失败 (非关键节点)
    -> continue: 标记失败, 下游继续 (用默认值)

    致命错误 (安全关键件失败)
    -> abort: 终止 workflow + Saga 补偿
       (逆序执行已完成节点的补偿)
```

### 6.4 信号处理

```
┌─────────────┬───────────────────────────────────────┬──────────┐
│ 信号         │ 算法                                   │ 作用阶段  │
├─────────────┼───────────────────────────────────────┼──────────┤
│ pause       │ _pause_state="paused", 节点间检查      │ 节点间   │
│ resume      │ _pause_state="running", 唤醒等待       │ 节点间   │
│ cancel_      │ _pause_state="cancelled", 终止        │ 节点间   │
│ by_user     │ 三分法: requested/cancelled/effect_   │          │
│             │ unknown                               │          │
│ human_      │ 设置 ReviewResult, 唤醒等待            │ Validate │
│ review      │ decision: approve/rework/modify/reject │          │
│ inject_     │ _injected_context[key]=value         │ Prepare  │
│ context     │ 只影响未执行节点, 冲突->弹窗确认        │          │
│ rework_     │ 1.副作用补偿 2.依赖污染BFS 3.重置下游  │ Commit后 │
│ node        │ 4.从该节点重新执行                      │          │
└─────────────┴───────────────────────────────────────┴──────────┘
```

### 6.5 回溯与重跑算法

```
输入: rework_node signal (node_id, param_overrides)

1. 副作用补偿:
   for effect in node.side_effects:
     if effect.compensator:
       execute_compensation(effect)  # 逆序
     else:
       标记 "无法补偿, 需人工清理"
     effect.compensated = True

2. 依赖污染分析 (BFS):
   affected = {rework_node_id}
   changed = True
   while changed:
     changed = False
     for node in spec.nodes:
       if node.node_id not in affected:
         if any(dep in affected for dep in node.depends_on):
           affected.add(node.node_id)
           changed = True

3. 重置受影响节点:
   for node_id in affected:
     node_state.status = "pending"
     node_state.result = None
     node_state.attempt += 1
     _processed_nodes.discard(node_id)

4. 应用参数覆盖 + 重新执行 (走完整 NodeAttempt 四阶段)
```

---

## 七、STAGE 5: 结果聚合与学习

### 7.1 结果汇总

```
输入: workflow 完成 (COMPLETED 或 FAILED)

1. 遍历所有节点的 ArtifactVersion
2. 生成自然语言摘要 (LLM summarize)
3. emit workflow_completed/failed event
4. 前端展示完整结果

WorkflowExecutionRecord = {
  record_id, workflow_id, plan_version,
  spec_snapshot, context_manifest,
  outcome, duration_s,
  node_outcomes: [{node_id, capability, status, duration_s,
    error, review_decision, user_feedback, param_overrides,
    degraded, attempt_count, artifact_version_id}],
  user_modifications: [{action, node_id, reason, timestamp}],
  lessons: list[str]
}
```

### 7.2 持续学习

```
输入: 多次 WorkflowExecutionRecord

1. Pattern 提取 (异步):
   - capability 组合频率统计
   - 常见失败模式统计
   - 审查决策分布统计

2. Pattern 存储 -> Memory Store

3. 下次 design_workflow 时:
   - search_workflow_patterns 查询类似场景
   - 在新 spec 中自动应用 recommendations
   - 弹窗告知: "基于 N 次经验, 已优化..."

4. Pattern 生命周期:
   - 新 pattern: 执行 >= 3 次后提取
   - 更新: 每次新执行后更新统计
   - 废弃: 30 天无新执行 -> 降权
```

---

## 八、跨层不可变契约总表

连接三层的纽带。L1 Harness 产出的可变认知状态，通过这些契约冻结为 L2/L3 的不可变事实：

```
L1 Harness (可变)          冻结点              L2/L3 (不可变)

AgentWorkPlan      ──►   PlanVersion        ──►  Temporal workflow
(Planning capability)    (用户批准)               执行蓝图

ContextManifest    ──►   ContextManifest    ──►  NodeAttempt Prepare
(Compaction 冻结)        (冻结快照)               可复现上下文

ToolEffectRecord   ──►   NodeAttempt        ──►  L3 Activity
(Effect Ledger)          (L2 执行实例)            幂等键检查

StepEvent          ──►   DecisionRecord     ──►  审计层
(Step Persistence)       (人类决策)                不可变记录

ArtifactVersion    ◄──   L3 Commit          ──►  WeldMap
                        (原子登记)                版本 + 谱系
```

| 契约 | 生产者 | 消费者 | 不可变性 | Harness capability 来源 |
|------|--------|--------|---------|----------------------|
| PlanVersion | L1 (用户批准) | L2 | 永久 | Planning |
| ContextManifest | L1 (冻结) | L2 | 永久 | Compaction + Media |
| NodeAttempt | L2 | L3 | 永久 | StepPersistence (Effect Ledger) |
| ArtifactVersion | L3 (commit) | L2/L1 | 永久 | - |
| ToolEffectRecord | L1+L2 | 审计层 | 永久 | StepPersistence |
| DecisionRecord | 跨层 | 审计层 | 永久 | StepPersistence |

---

## 九、异常路径汇总

| 异常 | 检测点 | Harness 能力 | 回退路径 | 场景 |
|------|--------|------------|---------|------|
| LLM API 超时 | STAGE 1/2 | StepPersistence | retry 2 次 -> 告知用户 | S6-2 |
| design 反复失败 | STAGE 2 | Planning | 3 次熔断 -> 转人工 | S1-5 |
| 用户超时不确认 | STAGE 3 | - | 保留 spec 30min | S2-1 |
| activity 超时 | STAGE 4-B | Effect Ledger | RetryPolicy 3 次 -> on_failure | S3-10 |
| L3 全挂 | STAGE 4-B | - | ERROR (非 mock OK) -> escalate | S3-12 |
| MLLM 降级 | STAGE 4-B | - | fallback 链 | S3-3 |
| 工具崩溃副作用未知 | STAGE 4-B | Effect Ledger | 标记 unknown_after_crash -> 人工确认 | 调研 §5.4 |
| Agent 崩溃 | STAGE 1/2 | ContinuableSnapshot | 从安全快照续跑 | 调研 §5.4 |
| 上下文溢出 | 全程 | Compaction | 80% 预警 -> 90% 压缩 -> 95% 拒绝 | 调研 §5.5 |
| 恶意输入 | STAGE 1 | InputGuardrail | BLOCK | S6-6 |
| 工具输出异常 | 全程 | AfterToolHook | BLOCK/RETRY/REPLACE | 调研 §5.6 |
| 输出幻觉 | STAGE 5 | OutputGuardrail | BLOCK/RETRY | 调研 §5.6 |
| Temporal 宕机 | STAGE 4 | - | /health degraded, 等恢复 replay | S6-1 |
| 崩溃后恢复 | STAGE 4 | ContinuableSnapshot | Temporal replay + snapshot 续跑 | S5-3 |
| 网络断开 | 前端 | - | SSE auto-reconnect + 历史回放 | S3-16 |

---

## 十、算法路径完整数据流

```
用户                L1 Harness                L1 Planner              L2 Temporal            L3 Activity
 │                   (capability 生命周期)       (Magentic 双账本)                                │
 │                    │                        │                      │                      │
 │  "评估这批焊缝数据"  │                        │                      │                      │
 │───────────────────►│                        │                      │                      │
 │                   │                        │                      │                      │
 │                   │  ┌─ Harness 生命周期 ──┐│                      │                      │
 │                   │  │ InputGuardrail     ││                      │                      │
 │                   │  │  (allow)           ││                      │                      │
 │                   │  │ ModelRequestTrans  ││                      │                      │
 │                   │  │  (注入 plan +     ││                      │                      │
 │                   │  │   notes + images)  ││                      │                      │
 │                   │  │ StepPersistence:   ││                      │                      │
 │                   │  │  AgentRunStarted   ││                      │                      │
 │                   │  │ LLM 调用           ││                      │                      │
 │                   │  │  -> type=workflow_  ││                      │                      │
 │                   │  │     design, 复杂    ││                      │                      │
 │                   │  └────────┬───────────┘│                      │                      │
 │                   │           │            │                      │                      │
 │                   │           ▼            │                      │                      │
 │                   │  ┌─启动双账本──────────┐│                      │                      │
 │                   │  │                    ││                      │                      │
 │                   │  │ Planning capability ││                      │                      │
 │                   │  │ 驱动 Task Ledger    ││                      │                      │
 │                   │  │ 构建                ││                      │                      │
 │                   │  │                    ││                      │                      │
 │                   │  │ Subagent capability ││                      │                      │
 │                   │  │ 委派 Specialist:    ││                      │                      │
 │                   │  │ ┌────────────┐     ││                      │                      │
 │                   │  │ │Profiler    │─────┼┼──────────────────────┼──────────────────────►│ analyze
 │                   │  │ │(子AgentRun)│     ││                      │                      │ dataset
 │                   │  │ │完整Harness │     ││                      │                      │
 │                   │  │ │生命周期    │     ││                      │                      │
 │                   │  │ └────────────┘     ││                      │                      │
 │                   │  │ ┌────────────┐     ││                      │                      │
 │                   │  │ │Strategist  │─────┼┼──────────────────────┼──────────────────────►│ search
 │                   │  │ │(子AgentRun)│     ││                      │                      │ cases
 │                   │  │ └────────────┘     ││                      │                      │
 │                   │  │ ┌────────────┐     ││                      │                      │
 │                   │  │ │Critic      │     ││                      │                      │
 │                   │  │ │(子AgentRun)│     ││                      │                      │
 │                   │  │ └────────────┘     ││                      │                      │
 │                   │  │ ┌────────────┐     ││                      │                      │
 │                   │  │ │Planner     │     ││                      │                      │
 │                   │  │ │design_wf   │     ││                      │                      │
 │                   │  │ └────────────┘     ││                      │                      │
 │                   │  │                    ││                      │                      │
 │                   │  │ Progress Ledger   ││                      │                      │
 │                   │  │ 从 StepEvent 流    ││                      │                      │
 │                   │  │ 投影               ││                      │                      │
 │                   │  │                    ││                      │                      │
 │                   │  │ 重规划检测:        ││                      │                      │
 │                   │  │  失败/无进展?      ││                      │                      │
 │                   │  │  是 -> 更新 Ledger  ││                      │                      │
 │                   │  │  否 -> 收敛        ││                      │                      │
 │                   │  │                    ││                      │                      │
 │                   │  │ 编译: PlanDraft    ││                      │                      │
 │                   │  │ +ContextManifest   ││                      │                      │
 │                   │  │  (Compaction 冻结)  ││                      │                      │
 │                   │  └────────┬───────────┘│                      │                      │
 │                   │           │            │                      │                      │
 │                   │  OutputGuardrail      │                      │                      │
 │                   │  (allow)              │                      │                      │
 │                   │  StepPersistence:     │                      │                      │
 │                   │  AgentRunCompleted    │                      │                      │
 │                   │           │            │                      │                      │
 │  方案弹窗           │◄──────────┘            │                      │                      │
 │◄───────────────────│  PlanDraft + budget   │                      │                      │
 │                   │  + risk + evidence     │                      │                      │
 │  "确认启动"         │                      │                      │                      │
 │───────────────────►│  冻结 PlanVersion      │                      │                      │
 │                   │  DecisionRecord       │                      │                      │
 │                   │  (审批记录)            │                      │                      │
 │                   │           │            │                      │                      │
 │                   │──► launch_workflow ────────────────────────►│                      │
 │                   │    (PlanVersion)      │                      │                      │
 │                   │                      │                      │                      │
 │                   │                      │                      │──► 拓扑排序 + 分层     │
 │                   │                      │                      │                      │
 │  "IQA 执行中..."   │◄──────────────────────────────────────────│  ┌─ NodeAttempt ─────┐│
 │◄───────────────────│  SSE: node_start     │                      │  │ A.PREPARE:        ││──► read
 │                   │                      │                      │  │  冻结输入          ││     image
 │                   │                      │                      │  │  幂等键           ││
 │                   │                      │                      │  │  注入合并          ││
 │                   │                      │                      │  └────┬─────────────┘│
 │                   │                      │                      │       │              │
 │                   │                      │                      │  ┌────▼─────────────┐│
 │                   │                      │                      │  │ B.EXECUTE:       ││──► CV rules
 │                   │                      │                      │  │  L3 activity     ││──► MLLM(opt)
 │                   │                      │                      │  │  只写 draft      ││
 │                   │                      │                      │  │  heartbeat       ││
 │                   │                      │                      │  │  EffectRecord:   ││
 │                   │                      │                      │  │   started->done  ││
 │                   │                      │                      │  └────┬─────────────┘│
 │                   │                      │                      │       │              │
 │  "IQA: MARGINAL"   │◄──────────────────────────────────────────│  ┌────▼─────────────┐│
 │◄───────────────────│  SSE: node_end       │                      │  │ C.VALIDATE:      ││
 │  审查弹窗           │◄──────────────────────────────────────────│  │  schema 校验      ││
 │◄───────────────────│  SSE: review_request │                      │  │  review_policy:  ││
 │                   │                      │                      │  │  CONDITIONAL     ││
 │                   │                      │                      │  │  MARG->暂停       ││
 │  "通过,PPA加锐化"   │                      │                      │  └────┬─────────────┘│
 │───────────────────►│──► human_review ──────────────────────────►│       │              │
 │                   │    signal             │                      │  ┌────▼─────────────┐│
 │                   │    APPROVE + overrides│                      │  │ D.COMMIT:       ││──► write
 │                   │                      │                      │  │  ArtifactVersion ││    WeldMap
 │                   │                      │                      │  │  + Lineage       ││    (versioned)
 │                   │                      │                      │  │  DecisionRecord ││
 │                   │                      │                      │  │  下游可消费       ││
 │                   │                      │                      │  └────┬─────────────┘│
 │                   │                      │                      │       │              │
 │  "PPA 执行中..."   │◄──────────────────────────────────────────│  ┌────▼─────────────┐│
 │◄───────────────────│  SSE: node_start     │                      │  │ A.PREPARE(PPA):  ││──► read IQA
 │                   │                      │                      │  │  读 ArtifactVer ││    Artifact
 │                   │                      │                      │  │  merge overrides ││
 │                   │                      │                      │  │  + inject_ctx    ││──► sharpen
 │                   │                      │                      │  │ B->C->D          ││
 │                   │                      │                      │  └────┬─────────────┘│
 │                   │                      │                      │       │              │
 │  "工作流完成"       │◄──────────────────────────────────────────│  emit completed    │
 │◄───────────────────│                      │                      │                    │
 │                   │  STAGE 5: 结果聚合    │                      │                    │
 │                   │  WorkflowExecution   │                      │                    │
 │                   │  Record -> Memory ──────────────────────────────────────────────────►│
 │                   │                      │                      │                    │
 │  下次设计时:        │                      │                      │                    │
 │  search_patterns   │                      │                      │                    │
 │  "基于经验,已优化"  │                      │                      │                    │
```

---

## 十一、实现优先级

| 优先级 | 阶段 | 内容 | 依赖 | 预估 |
|--------|------|------|------|------|
| P0 | 4-C | ReviewPolicy + human_review signal | 无 | 2 天 |
| P0 | 4-C | on_failure 五种语义真实实现 | 无 | 1 天 |
| P0 | 4-B | 取消 mock OK -> 返回 ERROR | 无 | 0.5 天 |
| P0 | 4-A | inject_context signal + L1 工具 | 无 | 1 天 |
| P0 | 5 | activity heartbeat 流式进度 | 无 | 1 天 |
| P1 | 2 | Harness capability 生命周期落地 | P0 | 3 天 |
| P1 | 2 | StepPersistence + ToolEffectRecord | P0 | 3 天 |
| P1 | 2 | Planning capability (AgentWorkPlan + write_plan) | P0 | 2 天 |
| P1 | 2 | Compaction 安全升级 (tool-pairing + 预算预警) | 无 | 2 天 |
| P1 | 2 | Guardrail 结构化裁决 (allow/block/replace/retry) | 无 | 2 天 |
| P1 | 4 | NodeAttempt 四阶段重构 | P0 | 1 周 |
| P1 | 3 | PlanVersion + ContextManifest | P0 | 3 天 |
| P1 | 4 | 并行 fan-out/fan-in | P0 | 2 天 |
| P2 | 4 | rework_node + 依赖污染分析 | NodeAttempt | 3 天 |
| P2 | 4 | Saga 补偿 + SideEffectRecord | ToolEffectRecord | 3 天 |
| P2 | 4 | 条件分支 condition 实现 | 无 | 1 天 |
| P2 | 3 | PlanRevisionProposal + cancel+relaunch | PlanVersion | 3 天 |
| P2 | 4 | 死信队列 + 收敛 fan-in | 并行 | 2 天 |
| P2 | 2 | token 预算管理 | Compaction | 2 天 |
| P3 | 5 | WorkflowExecutionRecord + Pattern 学习 | NodeAttempt | 1 周 |
| P3 | 2 | Magentic 双账本完整循环 (Specialist 委派树) | PlanVersion + Subagent | 2 周 |
| P3 | 5 | 审计轨迹 + 合规导出 | ArtifactVersion | 1 周 |
| P4 | 2 | Specialist 重规划逻辑 | 双账本 | 1 周 |
| P4 | 4 | LLM 动态路由 | condition | 1 周 |
| P4 | 4 | 子工作流嵌套 | NodeAttempt | 1 周 |
| P5 | 4 | 推测执行 | 并行+fallback | 1 周 |
| P5 | 4 | 多租户隔离 | 全局 | 2 周 |
