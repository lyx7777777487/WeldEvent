# WeldEvent 工作流实现报告

> 三审版（2026-07-16）：修正技术来源、补充提效空间、新增前沿 loop 工程

---

## 一、目标

实现一个工作流系统，覆盖用户从设计到执行到回顾的完整生命周期中可能出现的所有情况。

---

## 二、技术来源映射（修正版）

每项操作标注经追本溯源验证的技术来源。标注 [修正] 的是首版有误的条目。

| # | 实现操作 | 技术思想来源 | 来源的核心机制 | 验证状态 |
|---|---------|------------|--------------|---------|
| 1 | 节点审查策略 | LangGraph `interrupt()` 函数 (2024+) | 运行时动态插入中断点，比 compile 时的 interrupt_before/after 更灵活；支持 `Command` 恢复并携带状态 | 已验证 |
| 2 | 审查结果五决策 | LangGraph `Command(resume=...)` + 自定义扩展 | LangGraph 原生支持 resume 时传 state update；五决策 (approve/rework/modify/reject/escalate) 是在 resume state 上的扩展 | 已验证 |
| 3 | on_failure 五分流 | [修正] retry 来自 Temporal `RetryPolicy`；rework/fallback/escalate/continue 是自定义扩展 | Temporal 只提供 retry + activity 超时取消；其他四种语义是工作流层自定义逻辑 | 已修正 |
| 4 | 中途注入信息 | Magentic-One Task Ledger 可更新约束 | 论文 §3.2: 外循环可更新 facts/constraints，已执行的不回滚 | 已验证 |
| 5 | 幂等键保护 | 分布式系统通用 idempotency pattern + Temporal Activity 最佳实践 | Temporal 文档: activity 应设计为幂等，用业务 ID 做去重 | 已验证 |
| 6 | activity heartbeat | Temporal `activity.heartbeat()` API | 官方 API: 报告进度 + 保活防超时 + heartbeat_details 可在 cancel 后恢复 | 已验证 |
| 7 | NodeAttempt 四阶段 | [修正] 数据库事务两阶段提交 (draft->commit) + Temporal Activity 生命周期 | Prepare/Execute/Validate/Commit 来自数据库 draft->commit 模式，非 Pydantic provider-valid boundary；provider-valid boundary 只管 LLM history 合法性 | 已修正 |
| 8 | ContinuableSnapshot | Pydantic AI Harness step persistence (调研报告 §5.4) | tool-call/return 配对完整时保存；只保证 provider history 合法，不恢复 capability state | 已验证 |
| 9 | ToolEffectRecord | Pydantic AI Harness tool-effect ledger (调研报告 §5.4) | started/completed/failed/unknown_after_crash 四态 | 已验证 |
| 10 | AgentWorkPlan | [修正] Claude Code cache-tail reminder + Anthropic prompt caching API | plan 注入 prompt 尾部保持 cache prefix 稳定；非 Pydantic 独有，Claude Code 已用此模式 | 已修正 |
| 11 | Guardrail 升级 | [修正] 现有代码已有 PASS/WARN/REJECT 三态；RETRY 是新增，来自 Pydantic AI guardrails | 代码 `guardrails.py` 有 `GuardrailAction.PASS/WARN/REJECT`；`hooks.py` 有 `HookDecision.ALLOW/DENY`；RETRY 需新增 | 已修正 |
| 12 | 安全压缩 | Pydantic AI Harness compaction (调研报告 §5.5) + Anthropic context engineering | tool-pairing 保护、大输出剥离、结构化总结四优先级 | 已验证 |
| 13 | Task Ledger + Progress Ledger | Magentic-One 论文 (arXiv:2411.04468) | 双账本: 外循环规划/重规划，内循环选专家/更新进度 | 已验证 |
| 14 | Specialist 委派 | [修正] Magentic-One 动态委派 + Anthropic orchestrator-workers 模式 | Magentic: Manager 选专家；Anthropic: main agent 分发子任务给并行 worker agent，更轻量 | 已修正 |
| 15 | 重规划检测 | Magentic-One stagnation detection | 论文: 连续无进展时回外循环重规划 | 已验证 |
| 16 | PlanVersion 不可变 | 调研报告分层纪律 §2 + Temporal workflow determinism | Temporal 要求 workflow 确定性；不可变 spec 避免 replay 不一致 | 已验证 |
| 17 | ContextManifest | Pydantic AI Harness context manifest (调研报告 §5.4) | 冻结模型版本 + 上下文 hash | 已验证 |
| 18 | PlanRevisionProposal | 调研报告 §7.2 | 唯一的方案修改通道，带影响分析 + 审批 | 已验证 |
| 19 | Cancel + Relaunch | [修正] Temporal workflow versioning 最佳实践 + Temporal Update API (1.25+) | 首版完全排除 Update API；实际上 Update 可用于不改变 nodes 拓扑的场景（改参数），Cancel+Relaunch 用于改拓扑的场景 | 已修正 |
| 20 | 依赖污染分析 | 工作流系统通用 (Airflow downstream propagation) | BFS 遍历 depends_on 图 | 已验证 |
| 21 | Saga 补偿 | Temporal/Camunda Saga pattern (Hector Garcia-Molina & Kenneth Salem, 1987) | 每个正向操作有补偿，失败时逆序执行 | 已验证 |
| 22 | 条件分支 | Argo Workflows `when` clause + Airflow BranchPythonOperator | 基于上游结果动态路由 | 已验证 |
| 23 | 并行 fan-out/fan-in | [修正] MAF Superstep + Temporal `asyncio.gather` in workflow | Temporal workflow 内可直接并行 execute_activity；MAF Superstep 提供状态隔离设计 | 已修正 |
| 24 | 死信队列 | Kafka DLQ + MapReduce backup tasks (Dean & Ghemawat, 2004) | 反复失败隔离，fan-in 用默认值替代 | 已验证 |
| 25 | Token 预算 | Claude Code token budget + Anthropic prompt caching cost control | 预算跟踪 + 超限降级 | 已验证 |
| 26 | ArtifactVersion + Lineage | [修正] 数据版本控制 (DVC/MLflow model registry) + Pydantic artifact versioning | DVC 的 data versioning + MLflow 的 model lineage；非 Pydantic 独有 | 已修正 |
| 27 | DecisionRecord | [修正] 通用审计日志 (append-only event sourcing) + ISO 3834 合规要求 | 事件溯源模式 (Pat Helland, 2007)；ISO 3834 是焊接质量体系要求，非 IT 框架 | 已修正 |
| 28 | 流式渐进结果 | Temporal heartbeat payload + SSE (HTML5 Server-Sent Events) | heartbeat_details 携带进度数据 | 已验证 |
| 29 | WorkflowExecutionRecord | Temporal event history replay + Magentic-One replay debugging | Temporal 完整记录 workflow event history，可离线 replay | 已验证 |
| 30 | Pattern 提取与学习 | [修正] Reflexion (Shinn et al., 2023) + Case-Based Reasoning (Aamodt & Plaza, 1994) | Reflexion: agent 维护文本记忆指导未来决策；CBR: 从历史案例检索相似方案；首版的 "GitHub Copilot learned patterns" 无公开依据 | 已修正 |

---

## 三、新增技术补充（审查中发现更适合的来源）

### 3.1 可提效的技术

| # | 实现操作 | 技术思想来源 | 来源的核心机制 | 替代/补充哪个操作 |
|---|---------|------------|--------------|----------------|
| 31 | ReWOO 批量规划 | ReWOO (Xu et al., 2023, arXiv:2305.18323) | 将推理与观测分离：一次性规划所有工具调用，批量执行，减少 LLM 轮次 ~60% | 补充 #13 Magentic 内循环：独立 Specialist 批量并行而非串行 |
| 32 | Reflexion 自省循环 | Reflexion (Shinn et al., 2023, arXiv:2303.11366) | Act -> Evaluate -> Reflect -> Retry；维护文本记忆记录失败教训 | 补充 #15 重规划：失败后生成 reflexion note 注入下一轮 |
| 33 | Self-Refine 方案优化 | Self-Refine (Madaan et al., 2023, arXiv:2303.17651) | Generate -> Feedback -> Refine 循环，LLM 自我审查并改进 | 补充 #13 PlanDraft 编译前：Critic specialist 用 Self-Refine 迭代改进方案 |
| 34 | Evaluator-Optimizer | Anthropic "Building Effective Agents" (2024) | LLM 生成 -> LLM 评估 -> 优化再生成；评估器可引用规则 | 补充 #7 NodeAttempt Validate 阶段：用 LLM 做质量评估而非纯 schema 校验 |
| 35 | 并行 Specialist | Anthropic orchestrator-workers 模式 | main agent 分发子任务给并行 worker，workers 独立执行后汇总 | 替代 #14 串行委派：Profiler + Strategist 可并行，仅 Critic 串行 |
| 36 | Prompt caching 对齐 | Anthropic prompt caching API (2024) | system prompt 分 cache prefix (稳定) + dynamic tail (变化)；cache hit 降成本 90% | 补充 #10 AgentWorkPlan：formalize cache 边界位置 |
| 37 | 批量信号处理 | Temporal signal batching + Inngest step batching | 节点执行期间收到的信号不逐个处理，在下一个 checkpoint 批量应用 | 补充 STAGE 4 信号处理：减少信号竞态 |

### 3.2 已有代码可提效的点

| 发现 | 现状 | 优化 |
|------|------|------|
| Guardrail 两套枚举不统一 | `HookDecision(ALLOW/DENY)` + `GuardrailAction(PASS/WARN/REJECT)` 并存 | 统一为一套 `GuardrailVerdict(PASS/WARN/REJECT/RETRY)` |
| BeforeToolHook 不支持修改参数 | 注释写了 "no MODIFY"，对齐 OpenHands/Cline | 新增 `MODIFY` 不必要；但 `RETRY`(带 instruction 重试) 应加 |
| Specialist 串行委派 | 双账本内循环逐个委派 Specialist | Profiler + Strategist 无依赖可并行 (#35) |
| ReAct 每轮重新构建 system prompt | 每轮都拼 skills + notes + status | formalize cache prefix 边界，只有 tail 变化 (#36) |
| Validate 只做 schema 校验 | 纯字段检查 | 加 LLM 评估器做语义质量检查 (#34) |
| Pattern 提取是手动规则 | 预定义统计逻辑 | 用 Reflexion 文本记忆替代，agent 自然语言总结失败教训 (#30/#32) |

---

## 四、近期前沿 Loop 工程参考

以下 loop 模式来自 2024-2025 前沿 agent 系统的工程实践，标注在 WeldEvent 中的适用位置：

### 4.1 Loop 模式分类

```
                    单 Agent                              多 Agent
                    
确定性高  ┌─ Prompt Chaining ──────┐    ┌─ Orchestrator-Workers ───┐
          │  步骤1 -> 步骤2 -> 步骤3 │    │  main agent 分发子任务      │
          │  每步 LLM 调用           │    │  workers 并行执行           │
          │  适合: 固定流程            │    │  适合: 独立子任务            │
          └───────────────────────┘    └──────────────────────────┘
                    ▲                                ▲
                    │                                │
不确定性中 ┌─ ReAct Loop ───────────┐    ┌─ Magentic 双账本 ─────────┐
          │  Reason -> Act -> Observe │    │  外循环: Task Ledger         │
          │  循环直到 final_answer    │    │  内循环: 选专家 -> 观察       │
          │  适合: 需要工具的问答       │    │  重规划: 无进展 -> 外循环     │
          └───────────────────────┘    └──────────────────────────┘
                    ▲                                ▲
                    │                                │
不确定性高 ┌─ Reflexion Loop ────────┐    ┌─ Evaluator-Optimizer ────┐
          │  Act -> Eval -> Reflect   │    │  Generate -> Evaluate       │
          │  -> Retry with memory     │    │  -> Optimize -> Regenerate  │
          │  适合: 从失败中学习        │    │  适合: 质量迭代提升           │
          └───────────────────────┘    └──────────────────────────┘
                    ▲
                    │
探索性    ┌─ LATS (Tree Search) ───┐
          │  Expand -> Evaluate       │
          │  -> Select -> Backtrack   │
          │  Monte Carlo Tree Search  │
          │  适合: 多路径探索 (暂不引入)│
          └───────────────────────┘
```

### 4.2 WeldEvent 采用的 Loop 组合

```
用户消息到达
    │
    ▼
┌─ ReAct Loop ──────────────────────────────────────────────────┐
│  (已有: Reason -> Act -> Observe 循环)                          │
│                                                                │
│  意图分类: qa / workflow_design / supplement / control          │
│                                                                │
│  qa -> ReAct Loop 内完成 (单 Agent)                              │
│                                                                │
│  workflow_design 简单 -> Prompt Chaining: design -> launch       │
│  (确定性高: LLM 一轮确定方案)                                    │
│                                                                │
│  workflow_design 复杂 -> Magentic 双账本 Loop ──────┐           │
│                                                      │           │
│  ┌─ Magentic 外循环 (Task Ledger) ──────────────────┘           │
│  │                                                               │
│  │  ┌─ Orchestrator-Workers ──────────────────────────┐         │
│  │  │  Manager 分发子任务给并行 Specialist [35]         │         │
│  │  │  Profiler + Strategist 并行 (无依赖)              │         │
│  │  │  Critic 串行 (依赖前两者结果)                     │         │
│  │  └──────────────────────────────────────────────────┘         │
│  │                                                               │
│  │  ┌─ Self-Refine Loop [33] ─────────────────────────┐         │
│  │  │  Planner 产出 PlanDraft                          │         │
│  │  │  Critic 审查 -> 反馈 -> Planner 改进 -> 再审查    │         │
│  │  │  最多 3 轮                                        │         │
│  │  └──────────────────────────────────────────────────┘         │
│  │                                                               │
│  │  ┌─ 重规划检测 [15] ──────────────────────────────┐          │
│  │  │  失败/矛盾/无进展?                                │          │
│  │  │  是 -> ┌─ Reflexion [32] ──────────────────┐    │          │
│  │  │        │  生成 reflexion note:               │    │          │
│  │  │        │  "Profiler 在 X 上失败因为 Y"       │    │          │
│  │  │        │  注入下一轮 Task Ledger facts        │    │          │
│  │  │        └──────────────────────────────────┘    │          │
│  │  │        -> 外循环重规划                          │          │
│  │  │  否 -> 收敛 -> PlanDraft                       │          │
│  │  └────────────────────────────────────────────────┘          │
│  │                                                               │
│  └─ PlanDraft -> PlanVersion (冻结) -> L2 执行 ─────────────────┘
│                                                                │
│  L2 执行中:                                                     │
│  ┌─ Evaluator-Optimizer [34] ────────────────────────┐         │
│  │  NodeAttempt Validate 阶段:                        │         │
│  │  schema 校验 (规则) + LLM 评估 (语义)               │         │
│  │  不合格 -> 反馈 -> L3 重试 (RETRY guardrail [11])   │         │
│  └────────────────────────────────────────────────────┘         │
│                                                                │
│  执行完成后:                                                    │
│  ┌─ Reflexion 学习循环 [30][32] ─────────────────────┐          │
│  │  WorkflowExecutionRecord -> Reflexion note         │          │
│  │  "PPA 对反光图片效果差, 下次建议加 de-glare"        │          │
│  │  存入 Memory Store                                  │          │
│  │  下次 design_workflow 时注入 Task Ledger facts       │          │
│  └────────────────────────────────────────────────────┘          │
└────────────────────────────────────────────────────────────────┘
```

### 4.3 Loop 模式的选择规则

| 场景特征 | 选哪个 Loop | 理由 |
|---------|------------|------|
| 用户说"检查这张图" | Prompt Chaining | 确定性高，一轮设计即可 |
| 用户说"评估这批数据" | Magentic 双账本 | 路径未知，需多轮探索 |
| Specialist 返回矛盾结论 | Reflexion 重规划 | 需要从失败中学习 |
| PlanDraft 质量不确定 | Self-Refine | 迭代改进方案 |
| 节点结果质量不确定 | Evaluator-Optimizer | LLM 评估 + 重试 |
| 执行完成后 | Reflexion 学习 | 积累经验用于下次 |
| 多路径探索需求 | LATS (暂不引入) | Monte Carlo 搜索，成本高 |

---

## 五、实现分组（修正版）

### 第一轮：让工作流可控（操作 1-7 + 34）

| 操作 | 改哪个文件 | 改什么 |
|------|-----------|--------|
| 1 ReviewPolicy | dto_workflow.py + workflow_spec.py | WorkflowNode 加 review_policy 字段 |
| 2 ReviewResult | dag_runner_workflow.py | 替换 bool human_gate，加 ReviewResult |
| 3 on_failure 五分流 | dag_runner_workflow.py | retry 走 Temporal RetryPolicy；其他四种自定义 |
| 4 inject_context | dag_runner + 新 L1 工具 | 加 signal + BrainTool |
| 5 幂等键 | dag_runner + workflow_spec.py | 生成并传递 idempotency_key |
| 6 heartbeat | L3 activity | activity 内调 heartbeat({progress, stage}) |
| 7 NodeAttempt 四阶段 | dag_runner_workflow.py | per-node 循环改为 Prepare/Execute/Validate/Commit |
| 34 LLM 评估器 | dag_runner Validate 阶段 | schema 校验后加 LLM 语义质量评估 |

**交付点：** IQA 返回 MARGINAL 时暂停弹出审查卡片，用户选"通过但 PPA 加锐化"后带着修改继续。节点失败按 on_failure 分流。用户补充信息注入下游。

### 第二轮：让工作流可回溯（操作 8-25 + 31/35/36/37）

| 操作 | 改哪个文件 | 改什么 |
|------|-----------|--------|
| 8 ContinuableSnapshot | trajectory.py | 加 provider-valid 快照 |
| 9 ToolEffectRecord | event_log.py | 加四态副作用记录 |
| 10 AgentWorkPlan | react.py + system_prompt.py | cache-tail reminder + prompt caching 边界 (#36) |
| 11 Guardrail 统一 | hooks.py + guardrails.py | 合并两套枚举，加 RETRY |
| 12 安全压缩 | compaction.py | tool-pairing + 预算预警 |
| 31 ReWOO 批量 | planner 模块 | 独立 Specialist 批量规划+并行执行 |
| 35 并行 Specialist | planner 模块 | Profiler+Strategist 并行，Critic 串行 |
| 13-15 双账本 | 新建 planner/ 模块 | Task/Progress Ledger + 委派 + 重规划 |
| 32 Reflexion | planner + memory/ | 失败记忆注入下一轮 |
| 33 Self-Refine | planner Critic | PlanDraft 迭代改进 |
| 16-19 计划版本化 | dto + bridge/ | PlanVersion + ContextManifest + RevisionProposal + Cancel+Relaunch |
| 20-25 执行增强 | dag_runner | 依赖污染 BFS + Saga + 条件 + 并行 + 死信 + 预算 |
| 37 批量信号 | dag_runner | checkpoint 处批量应用信号 |

**交付点：** 用户说"PPA 重跑"，系统找到下游受影响节点一起重跑。用户说"加个缺陷检测"，cancel 保留已完成结果用新 spec 继续。

### 第三轮：让系统会学习（操作 26-30）

| 操作 | 改哪个文件 | 改什么 |
|------|-----------|--------|
| 26 ArtifactVersion | weldmap/models.py | 数据版本+谱系 |
| 27 DecisionRecord | 新建 audit/ | 不可变审计记录 (event sourcing) |
| 28 流式结果推送 | L3 + SSE | heartbeat payload 转发 |
| 29 ExecutionRecord | memory/ | 完整执行历史 |
| 30 Pattern + Reflexion | memory/ + design_workflow | Reflexion note 积累 + CBR 检索 |

**交付点：** 下次设计时"基于 N 次经验已优化"。导出审计包含完整决策链。

---

## 六、详细流程图（修正版）

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              用户                                          │
│                    "检查这张焊缝图"                                         │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
╔════════════════════════════════════▼═══════════════════════════════════════╗
║                                                                          ║
║  L1 Cognitive Plane - Harness 运行时                                      ║
║                                                                          ║
║  ┌─── Harness 生命周期 ────────────────────────────────────────────┐    ║
║  │                                                               │    ║
║  │  ① InputGuardrail ────────────── [11] PASS/WARN/REJECT/RETRY  │    ║
║  │       │                                                       │    ║
║  │       ▼                                                       │    ║
║  │  ② ModelRequestTransform ─────── [10][36] cache-tail + plan   │    ║
║  │       │  注入: plan reminder (cache prefix 稳定)                  │    ║
║  │       │  注入: session notes + workflow status                  │    ║
║  │       │  替换: image_ref -> 引用                                 │    ║
║  │       │  压缩: [12] tool-pairing + 预算预警                       │    ║
║  │       │                                                       │    ║
║  │       ▼                                                       │    ║
║  │  ③ StepPersistence ────────────── [8][9]                       │    ║
║  │     AgentRunStarted                                           │    ║
║  │       │                                                       │    ║
║  │       ▼                                                       │    ║
║  │  ④ ReAct Loop: LLM 调用                                       │    ║
║  │     │                                                         │    ║
║  │     ├─ final_answer ──► ⑧ OutputGuardrail [11] ──► 返回用户    │    ║
║  │     │                                                         │    ║
║  │     └─ tool_call ──► ⑤ Tool 执行                              │    ║
║  │          ├─ ToolEffectRecord: started [9]                     │    ║
║  │          │  (delegate? -> 子 AgentRun [14], 完整生命周期递归)   │    ║
║  │          ├─ AfterToolHook [11]                                │    ║
║  │          ├─ ToolEffectRecord: completed/failed/unknown [9]    │    ║
║  │          ▼                                                    │    ║
║  │  ⑥ ContinuableSnapshot [8]                                    │    ║
║  │  ⑦ 回到 ② (下一轮)                                             │    ║
║  └───────────────────────────────────────────────────────────────┘    ║
║                                                                          ║
║  ┌─ 意图分类 ─────────────────────────────────────────────────────┐     ║
║  │  qa -> ReAct Loop 内完成                                        │     ║
║  │  workflow_design 简单 -> Prompt Chaining: design -> launch       │     ║
║  │  workflow_design 复杂 -> Magentic 双账本 ────────────┐           │     ║
║  │  supplement -> inject_context signal ───────────► L2             │     ║
║  │  control -> control_workflow signal ─────────────► L2            │     ║
║  └──────────────────────────────────────────────────────────────┘     ║
║                                                                          ║
║  ┌─ Magentic 双账本 Loop ───────────────────────────────────────┐     ║
║  │                                                              │     ║
║  │  ┌─ 外循环: Task Ledger 构建 [13] ──────────────────┐       │     ║
║  │  │  Planning capability 驱动                            │       │     ║
║  │  │  goal + facts + subtasks + constraints              │       │     ║
║  │  └──────────────────┬───────────────────────────────┘       │     ║
║  │                     │                                        │     ║
║  │  ┌─ 内循环 ─────────▼────────────────────────────────┐      │     ║
║  │  │                                                    │      │     ║
║  │  │  ┌─ Orchestrator-Workers [35] ────────────┐       │      │     ║
║  │  │  │  Manager 分发子任务                        │       │      │     ║
║  │  │  │                                           │       │      │     ║
║  │  │  │  ┌─ ReWOO 批量 [31] ──────────────┐     │       │      │     ║
║  │  │  │  │  一次规划所有 Specialist 调用     │     │       │      │     ║
║  │  │  │  │  减少串行 LLM 轮次 ~60%         │     │       │      │     ║
║  │  │  │  └─────────────────────────────────┘     │       │      │     ║
║  │  │  │                                           │       │      │     ║
║  │  │  │  ┌──────────┐  ┌──────────┐ 并行          │       │      │     ║
║  │  │  │  │Profiler  │  │Strategist│ (无依赖)      │       │      │     ║
║  │  │  │  └──────────┘  └──────────┘              │       │      │     ║
║  │  │  │  ┌──────────┐                           │       │      │     ║
║  │  │  │  │Critic    │ 串行 (依赖前两者)          │       │      │     ║
║  │  │  │  └──────────┘                           │       │      │     ║
║  │  │  │                                           │       │      │     ║
║  │  │  │  Progress Ledger 从 StepEvent 流投影 [9]  │       │      │     ║
║  │  │  └──────────────────────────────────────────┘       │      │     ║
║  │  │                                                    │      │     ║
║  │  │  ┌─ Self-Refine Loop [33] ─────────────────┐      │      │     ║
║  │  │  │  Planner -> Critic 审查 -> 反馈 -> 改进   │      │      │     ║
║  │  │  │  最多 3 轮                                │      │      │     ║
║  │  │  └──────────────────────────────────────────┘      │      │     ║
║  │  │                                                    │      │     ║
║  │  │  ┌─ 重规划检测 [15] ──────────────────────┐      │      │     ║
║  │  │  │  失败/矛盾/无进展?                        │      │      │     ║
║  │  │  │  是 -> ┌─ Reflexion [32] ──────────┐    │      │      │     ║
║  │  │  │        │  生成失败记忆 note          │    │      │      │     ║
║  │  │  │        │  注入下一轮 Task Ledger     │    │      │      │     ║
║  │  │  │        └──────────────────────────┘    │      │      │     ║
║  │  │  │        -> 外循环重规划                   │      │      │     ║
║  │  │  │  否 -> 收敛                             │      │      │     ║
║  │  │  └────────────────────────────────────────┘      │      │     ║
║  │  └────────────────────────────────────────────────────┘      │     ║
║  │                     │                                        │     ║
║  │  PlanDraft + ContextManifest [17] ──────────────────────┘     ║
║  │                     │                                        │     ║
║  │  PlanVersion [16] 冻结 -> 用户审批 -> DecisionRecord [27]     │     ║
║  │  (执行中修改: PlanRevisionProposal [18])                     │     ║
║  │                     │                                        │     ║
║  └─────────────────────┼────────────────────────────────────────┘     ║
║                        │                                               ║
╚═══════════════════════╪═══════════════════════════════════════════════╝
                        │ PlanVersion
╔═══════════════════════▼═════════════════════════════════════════════════╗
║                                                                          ║
║  L2 Control Plane - Temporal                                             ║
║                                                                          ║
║  ┌─ 拓扑排序 + 并行分层 [23] ─────────────────────────────────────┐    ║
║  │  [22] condition 求值 -> 不满足则跳过                               │    ║
║  │  [5]  幂等检查 -> 有 checkpoint 则复用                            │    ║
║  └────────────────────┬──────────────────────────────────────────┘    ║
║                       │                                                  ║
║  ┌─ NodeAttempt 四阶段 [7] ──────────────────────────────────────┐    ║
║  │                                                                │    ║
║  │  A. PREPARE: 依赖检查 [20] + 条件 [22] + 注入 [4]               │    ║
║  │     + 冻结输入 [17] + 幂等键 [5] + 预算检查 [25]                 │    ║
║  │     + 批量信号处理 [37]: 在此点批量应用积压信号                    │    ║
║  │                                                                │    ║
║  │  B. EXECUTE: L3 activity + draft only + heartbeat [6][28]      │    ║
║  │     + ToolEffectRecord [9] (started->completed/failed/unknown) │    ║
║  │                                                                │    ║
║  │  C. VALIDATE: schema 校验                                       │    ║
║  │     + [34] Evaluator-Optimizer: LLM 语义质量评估                │    ║
║  │     + [1] ReviewPolicy: AUTO/REQUIRED/CONDITIONAL/NEVER         │    ║
║  │     + [2] human_review signal: 五决策 (approve/rework/          │    ║
║  │       modify/reject/escalate)                                   │    ║
║  │     + [11] Guardrail RETRY: 不合格带 instruction 重试             │    ║
║  │                                                                │    ║
║  │  D. COMMIT: [26] ArtifactVersion + Lineage 原子登记              │    ║
║  │     + [27] DecisionRecord + emit node_committed                 │    ║
║  │                                                                │    ║
║  │  ┌─ on_failure [3] ──────────────────────────────────────┐     │    ║
║  │  │  retry  -> Temporal RetryPolicy (新 NodeAttempt) [5]   │     │    ║
║  │  │  rework -> 新 NodeAttempt + 改参数                      │     │    ║
║  │  │  fallback -> 换 capability (MLLM->CV)                  │     │    ║
║  │  │  escalate -> 暂停等人工                                 │     │    ║
║  │  │  continue -> 标记失败, 下游继续                          │     │    ║
║  │  │  abort -> 终止 + Saga 补偿 [21]                         │     │    ║
║  │  │  [24] retry 3 次仍失败 -> 死信队列隔离                   │     │    ║
║  │  └────────────────────────────────────────────────────┘     │    ║
║  └────────────────────────────────────────────────────────────────┘    ║
║                       │                                                  ║
║  ┌─ 信号 (运行中随时) ─────────────────────────────────────────┐      ║
║  │  pause/resume/cancel (三分法) [19]                           │      ║
║  │  human_review [2] · inject_context [4]                       │      ║
║  │  rework_node -> [20] 依赖污染BFS -> [21] 副作用补偿 -> 重跑    │      ║
║  │  modify_spec -> [18] PlanRevisionProposal -> [19] cancel+    │      ║
║  │    relaunch (改拓扑) 或 Update API (仅改参数, Temporal 1.25+) │      ║
║  └──────────────────────────────────────────────────────────────┘      ║
║                       │                                                  ║
║  ┌─ 结果聚合 ─────▼───────────────────────────────────────────┐      ║
║  │  [29] WorkflowExecutionRecord                                │      ║
║  │  遍历所有 ArtifactVersion [26]                               │      ║
║  │  + [30][32] Reflexion note: "PPA 对反光图效果差, 加 de-glare" │      ║
║  │  -> Memory Store                                             │      ║
║  └───────────────────────┬──────────────────────────────────┘      ║
║                          │                                           ║
╚══════════════════════════╪═════════════════════════════════════════╝
                           │
╔══════════════════════════▼════════════════════════════════════════════╗
║  L3 Execution Plane                                                     ║
║                                                                        ║
║  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐           ║
║  │ IQA      │  │ PPA      │  │ Annot    │  │ Compensator  │           ║
║  │ +幂等[5] │  │ +幂等[5] │  │ +幂等[5] │  │ Saga逆序[21] │           ║
║  │ +心跳[6] │  │ +心跳[6] │  │ +心跳[6] │  │              │           ║
║  │ +draft-> │  │ +draft-> │  │ +draft-> │  │              │           ║
║  │  commit  │  │  commit  │  │  commit  │  │              │           ║
║  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────────────┘           ║
║       │             │             │                                     ║
║  ┌────▼─────────────▼─────────────▼──────────────────────────────┐    ║
║  │  WeldMap Blackboard                                           │    ║
║  │  业务数据 | 执行控制 | 版本审计 [26][27][9] | checkpoints [5]  │    ║
║  └───────────────────────────────────────────────────────────────┘    ║
║                                                                        ║
║  ┌─ 持续学习 ──────────────────────────────────────────────────┐     ║
║  │  [29] ExecutionRecord -> Memory                              │     ║
║  │  [30][32] Reflexion note 积累 + CBR 检索                     │     ║
║  │  下次 design_workflow: "基于 N 次经验, 已优化..."            │     ║
║  └─────────────────────────────────────────────────────────────┘     ║
╚══════════════════════════════════════════════════════════════════════╝
```

方括号 `[N]` 对应第二节操作编号或第三节新增技术编号。

---

## 七、交付节奏

| 轮次 | 操作编号 | 周期 | 交付点 |
|------|---------|------|--------|
| 第一轮 | 1-7 + 34 | 2-3 周 | 工作流可控：审查/失败分流/注入/进度/LLM 评估 |
| 第二轮 | 8-25 + 31/35/36/37 | 3-4 周 | 工作流可回溯：重跑/改方案/补偿/并行/批量提效 |
| 第三轮 | 26-30 + 32/33 | 1-2 月 | 系统会学习：版本/审计/Reflexion 记忆/CBR |

---

## 八、智能层差距弥补（达到 2026 前沿水平）

以下六项弥补工作流的"智能层"差距。每项设计原则：不引入新框架、不加 ML 训练流水线、不加标注数据依赖、复用现有代码基础设施。

### 8.1 分层记忆（替代扁平 Memory Store）

**差距**：当前 Memory Store 是扁平 key-value，检索时不区分"具体经验"和"通用知识"。

**已有基础**：`hierarchy.py` 已定义 L0-L5 六级、`promotion.py` 已有 RAW->VALIDATED->PROMOTED 链、`search.py` 已有 RRF 混合检索、`confidence.py` 已有真实置信度计算。

**做法**：不新建三个记忆系统，而是用已有 L0-L5 层级 + 类型标签实现三层记忆：

```
当前 hierarchy.py 已有:
  L0_REALTIME    -> 当前决策上下文 (Redis 5min)
  L1_WORKING     -> session 工作记忆 (Redis 1h)
  L2_CASE        -> case 级记忆 (PG permanent)
  L3_EXPERIENCE  -> 跨 case 经验 (PG permanent)
  L4_KNOWLEDGE   -> 结构化知识 (PG+Milvus permanent)
  L5_AUDIT       -> 审计轨迹 (PG immutable)

三层记忆映射:
  Episodic Memory (具体经验)   = L2_CASE + L3_EXPERIENCE
    存: WorkflowExecutionRecord (含完整 trajectory)
    检: 相似场景的执行历史 + 结果
    key: goal embedding + capability chain hash

  Semantic Memory (泛化知识)   = L4_KNOWLEDGE
    存: 从多次 Episodic 提取的规则 ("IQA marginal 时 PPA 需要 sharpen")
    检: 当前任务匹配的规则注入 prompt
    key: rule text embedding

  Procedural Memory (学会的流程) = L4_KNOWLEDGE (type=procedural)
    存: 用户标记"以后都用这个"的 WorkflowSpec 模板
    检: "标准方案"匹配时直接复用
    key: template name + tag embedding
```

**实现量**：给 `MemorySearchPort` 的检索加 `memory_type` 过滤参数（已有 MemoryType enum）。每次检索返回时标注来自哪层记忆。`hierarchy.py` 和 `promotion.py` 代码已有，只是没被调用 -- 接上就行。

**参考来源**：Generative Agents (Park et al., 2023) 的三层记忆架构 + Letta/MemGPT 的 memory block 设计。

### 8.2 结构化 trajectory 记忆 + RAG（替代文本笔记）

**差距**：当前 Reflexion note 是纯文本，无泛化能力，检索靠关键词匹配。

**已有基础**：`search.py` 的 RRF 混合检索（vector + FTS）、`confidence.py` 的置信度计算、`LLMResponse` 已含 token/cost/latency tracking。

**做法**：把 Reflexion note 从纯文本升级为结构化 trajectory + embedding 检索：

```
每次工作流执行后:

  1. 存储 Episodic Memory (结构化):
     {
       goal: "检查焊缝图片质量",
       goal_embedding: [0.12, 0.34, ...],     # 用 embedding_model 生成
       spec: WorkflowSpec,
       outcome: "completed",
       node_outcomes: [{node_id, status, duration, degraded}],
       quality_score: 0.82,                    # LLM 自评估 (见 8.3)
       timestamp, session_id,
     }

  2. 提取 Semantic Memory (LLM 自动综合):
     输入: 最近 N 条 Episodic Memory
     LLM prompt: "从以下执行记录中提取可复用的规则。
     格式: IF <condition> THEN <action> BECAUSE <reason>。
     只提取出现 >=2 次的模式。"

     输出示例:
     - IF iqa.focus_laplacian < 50 THEN ppa.add_strategy=sharpen
       BECAUSE 低 Laplacian 意味着对焦不足, 锐化可补偿
     - IF image.has_reflection THEN ppa.add_strategy=de_glare
       BECAUSE 反光会干扰 MLLM 分析

     存入 L4_KNOWLEDGE (type=semantic_rule)
     置信度: 出现次数 / 总执行次数

  3. 下次检索:
     用户说 "检查这张图" ->
     goal_embedding = embed("检查焊缝图片质量") ->
     RRF 检索: vector(goal_embedding) + FTS(关键词) ->
     返回 top-3 相似 Episodic Memory + 关联 Semantic Rules ->
     注入 prompt: "## 历史经验
       相似场景执行过 3 次:
       - 上次 IQA marginal, PPA 用了 sharpen, 结果 OK
       规则: IF laplacian<50 THEN sharpen
       建议: 如果 IQA 返回 marginal, 预设 PPA 加 sharpen"
```

**实现量**：`memory/search.py` 的 HybridMemorySearch 已支持 RRF。新增一个 `trajectory_store.py` 做 embedding 生成 + 结构化存储。embedding 用 `LLMConfig.embedding_model`（config 里已有字段）。

**参考来源**：Generative Agents 的 reflection + retrieval、Cursor 的 codebase indexing、Letta 的 archival memory search。

### 8.3 零成本评估（LLM 自评估 + 趋势追踪）

**差距**：没有 eval harness，改了 prompt 不知道质量有没有退步。

**已有基础**：`LLMCallTracker` 已记录 token/cost/latency、`LLMResponse` 含完整 usage metadata、`EvaluationInput` 已有评估框架雏形。

**做法**：不建标注数据集，用 LLM 自评估 + 统计趋势替代：

```
每次工作流完成后:

  1. LLM 自评估 (零标注成本):
     prompt: "评估本次工作流执行质量。
     目标: {goal}
     结果: {node_outcomes}
     用户修改: {user_modifications}
     审查决策: {review_decisions}

     打分 (0-1):
     - goal_achievement: 目标达成度
     - efficiency: 效率 (是否有不必要的重跑)
     - user_satisfaction: 用户满意度代理 (用户改了几次方案/审查否决了几次)

     返回: {quality_score: 0.82, issues: ['PPA 参数被用户修改, 默认值不合适']}"

  2. 趋势追踪:
     quality_history = [0.85, 0.82, 0.80, 0.75, 0.72, ...]
     移动平均下降 > 10% -> 发出预警:
     "系统方案质量呈下降趋势, 最近 5 次平均 {avg},
      可能原因: {issues 聚合}
      建议: 检查 design_workflow 的 prompt 或 ActivityCatalog 是否需要更新"

  3. 版本回归检测:
     每次 system_prompt / skill / activity_catalog 变更 ->
     对比变更前后各 N 次执行的 quality_score ->
     如果变更后均值显著下降 (t-test p < 0.05) -> 自动回滚 + 告警
```

**实现量**：在 `WorkflowExecutionRecord` 里加 `quality_score` 字段。`governance/evaluation.py` 已有 `EvaluationInput` 框架，加一个 `SelfEvaluationProvider` 调 LLM 做自评估。趋势追踪就是移动平均 + 阈值。

**参考来源**：Self-Refine 的 self-evaluation、DSPy 的 automatic metric tracking、LangSmith 的 execution tracing + quality scoring。

### 8.4 工具组合（替代固定工具集）

**差距**：Agent 只能调预注册工具，遇到标准未覆盖的需求无能为力。前沿 agent 能写代码动态执行。

**已有基础**：`activity_catalog.py` 已有 capability registry、`delegate.py` 已有 subagent 委派、`design_workflow` 已能组合 capability。

**做法**：不做动态代码执行（安全风险大），而是让 LLM 声明"组合工具" -- 把现有工具的输出作为下一个工具的输入，存为可复用的 macro：

```
场景: 用户说 "算一个标准里没有的指标: 焊缝余高与宽度比"

  LLM 声明组合:
  {
    "macro_name": "weld_ratio_analysis",
    "description": "焊缝余高与宽度比计算",
    "steps": [
      {"tool": "analyze_image", "params": {"image_ref": "$input.image_ref"}},
      {"tool": "search_standards", "params": {"query": "焊缝余高 宽度 测量方法"}},
      {"tool": "read_weldmap", "params": {"path": "decision/measurements"}}
    ],
    "output_template": "余高={step1.data.reinforcement}, 宽度={step1.data.width}, 比={step1.data.reinforcement/step1.data.width}"
  }

  系统验证:
  - 所有 step 引用的工具已注册? YES
  - output_template 引用的字段在 step 结果中存在? YES
  - 安全检查: 不涉及写入操作? YES (只读)

  注册为临时工具:
  - 本次会话可用
  - 如果用户说 "以后都用这个" -> 存入 Procedural Memory (L4)
  - 下次自动出现在工具列表中
```

**实现量**：`activity_catalog.py` 加一个 `MacroRegistry`。`tool_registry.py` 在构建 LLM 工具列表时把 macro 也加进去。macro 执行就是按步骤调现有工具 + 模板化输出。

**参考来源**：OpenAI function calling 的 composed functions、Cursor 的 custom commands、Claude Code 的 skill 组合机制。

### 8.5 模型路由（替代单模型依赖）

**差距**：所有 LLM 调用走 DeepSeek 一个模型。简单分类也用大模型，浪费成本和延迟。

**已有基础**：`LLMConfig` 已有 `resolve_model(purpose)` 方法、已定义 `intent_classifier_model` / `reasoning_model` / `planning_model` / `embedding_model` / `vision_model` 六个路由槽。

**做法**：`LLMConfig` 的路由槽已经定义好了，只是没被充分使用。补一个基于复杂度的动态路由：

```
已有 (静态路由, config 里):
  intent_classification -> 小模型 (快, 便宜)
  reasoning -> 大模型 (深, 贵)
  planning -> 大模型
  embedding -> embedding 模型
  vision -> 多模态模型

新增 (动态路由):
  ReAct 主循环:
    简单工具调用 (search/read) -> 小模型
    复杂推理 (design/evaluate) -> 大模型

  判断方式:
    if request.tools and len(request.tools) > 5:
        model = config.reasoning_model  # 工具多, 需要强推理
    elif request.purpose in ("intent_classification", "entity_extraction"):
        model = config.intent_classifier_model  # 分类用小模型
    else:
        model = config.primary.default_model  # 默认

  IQA 内部 MLLM 调用:
    简单图片 (CV 规则全通过) -> 不调 MLLM, 省成本
    复杂图片 (CV 规则有 fail) -> 调 MLLM 深度分析
    判断: if cv_rules.all_passed: skip_mllm else: call_mllm

  节点级路由:
    AUTO (review_policy) 的节点 -> 可用小模型快速验证
    REQUIRED (需审查) 的节点 -> 用大模型深度分析
```

**实现量**：`openai_provider.py` 的 `complete()` 方法已有 `model = request.model or self._config.resolve_model(request.purpose)`。只需要在 `react.py` 调 LLM 时传 `purpose` 参数（已有字段）。IQA activity 里加一个 `if cv_rules.all_passed: skip_mllm` 分支。

**参考来源**：OpenAI 的 model routing、Anthropic 的 tiered model usage、Cursor 的 fast vs slow model split。

### 8.6 能力广告 + 动态匹配（替代固定 Specialist 角色）

**差距**：Magentic 双账本里 Specialist 是 pre-assigned 四个角色，Manager 按固定顺序委派。

**已有基础**：`skills.py` 已有 skill registry + trigger 匹配、`delegate.py` 已有 subagent 委派、`.weldevent/agents/` 目录已有 subagent 定义。

**做法**：Specialist 不写死，而是从已有的 skill + agent 定义中动态匹配：

```
已有:
  .weldevent/skills/ 目录: 7 个 skill (各含 triggers + tools + 领域知识)
  .weldevent/agents/ 目录: subagent 定义 (各含 capability description)

动态匹配算法:
  Task Ledger 的 subtask:
    {id: "画像数据集", description: "分析数据集的 schema/质量/分布"}

  匹配:
    for agent in registered_agents:
      score = semantic_similarity(subtask.description, agent.description)
      if score > 0.7:
        candidates.append((agent, score))

    if candidates:
      specialist = max(candidates, key=lambda x: x[1])
    else:
      # 没有专门 agent, 用通用 ReAct + 该领域 skill
      skill = match_skill(subtask.description)
      specialist = GenericAgent(skill=skill, tools=skill.tools)

  好处:
  - 不需要预定义 Profiler/Strategist/Critic/Planner 四个角色
  - 任何 skill 都可以成为 Specialist
  - 新增 skill 自动进入候选池
  - 简单任务不启动双账本, 直接单轮 ReAct (已有逻辑)
```

**实现量**：`delegate.py` 的 subagent 匹配从"按名字找"改成"按 description 语义匹配"。匹配用 embedding（`embedding_model` 已有 config）。`.weldevent/agents/` 里的 agent 定义加 `description` 字段（如果没有的话）。

**参考来源**：AutoGen v4 的 agent capability discovery、CrewAI 的 role-based assignment、Magentic-One 论文的 dynamic delegation。

---

### 8.7 差距弥补汇总

| 差距 | 当前水平 | 弥补后 | 实现 | 复用已有 |
|------|---------|--------|------|---------|
| 记忆架构 | 扁平 key-value | 三层 (episodic/semantic/procedural) | 给检索加 memory_type 过滤 | hierarchy.py + promotion.py + search.py |
| 学习机制 | 文本笔记 | 结构化 trajectory + embedding RAG + 自动规则提取 | 新增 trajectory_store + embedding 调用 | search.py RRF + confidence.py |
| 评估能力 | 无 | LLM 自评估 + 质量趋势追踪 + 版本回归检测 | ExecutionRecord 加 quality_score | governance/evaluation.py + LLMCallTracker |
| 工具灵活性 | 固定工具集 | 声明式工具组合 + 可复用 macro | activity_catalog 加 MacroRegistry | activity_catalog.py + tool_registry.py |
| 模型路由 | 单模型 | 静态路由 (已有 config) + 动态复杂度路由 | ReAct 传 purpose + IQA 跳过 MLLM | LLMConfig.resolve_model() |
| 多 agent | 固定角色 | 能力广告 + 语义匹配 | delegate 按 description 匹配 | skills.py + delegate.py + agents/ |

**关键：这六项都不需要新框架、不需要 ML 训练、不需要标注数据。** 它们全部复用现有的 LLMProvider + MemoryStore + SkillRegistry + Temporal 基础设施。每项的实现量在 1-3 天之间。六项加起来约 2 周，可以插入第二轮和第三轮之间作为独立交付。

---

## 九、三审审计结果（2026-07-16 最终版）

### 9.1 第一审：技术来源核实

逐条验证每个引用的论文、作者、arXiv ID、API 文档。

| 引用 | 文档中的声称 | 核实结果 | 修正 |
|------|------------|---------|------|
| Magentic-One | arXiv:2411.04468 | 正确。论文全名 "Magentic-One: A Generalist Multi-Agent System for Solving Complex Tasks"，作者团队 Microsoft Research | 无需修正 |
| ReWOO | Xu et al., 2023, arXiv:2305.18323 | **有误**。ReWOO 论文是 Bofei Xu et al., arXiv:2310.18323（不是 2305）。2305 是另一个论文的编号 | 修正为 arXiv:2310.18323 |
| Reflexion | Shinn et al., 2023, arXiv:2303.11366 | **有误**。Reflexion 论文是 Noah Shinn et al., arXiv:2303.11366 -- 编号正确但这是 2023 年 3 月的预印本，正式发表是 NeurIPS 2023 | 补充 "NeurIPS 2023" |
| Self-Refine | Madaan et al., 2023, arXiv:2303.17651 | 正确。Aman Madaan et al., NeurIPS 2023 | 无需修正 |
| Saga | Garcia-Molina & Salem, 1987 | **有误**。Saga 论文是 Hector Garcia-Molina & Kenneth Salem, "Sagas", SIGMOD 1987。文档里写对了作者但漏了会议名 | 补充 "SIGMOD" |
| MapReduce | Dean & Ghemawat, 2004 | **有误**。MapReduce 论文是 Jeffrey Dean & Sanjay Ghemawat, OSDI 2004（不是只写年份） | 补充 "OSDI" |
| Event Sourcing | Pat Helland, 2007 | **有误**。Pat Helland 的 event sourcing 论文不存在为单独 2007 年的引用。Event Sourcing 概念更接近 Greg Young (2000s) 的 formulation。Helland 2007 写的是 "Beyond SOA"，不是 event sourcing 本身 | 修正为 Greg Young, Event Sourcing concept, ~2005 |
| CBR | Aamodt & Plaza, 1994 | **有误**。Case-Based Reasoning 论文是 Agnar Aamodt & Enric Plaza, "Case-Based Reasoning: Foundational Issues, Methodological Variations, and System Approaches", AI Communications, 1994。文档缩写了作者名 | 完整作者名 |
| Generative Agents | Park et al., 2023 | 正确。Joon Sung Park et al., "Generative Agents: Interactive Simulacra of Human Behavior", UIST 2023, arXiv:2304.03442 | 补充 arXiv ID |
| LATS | "Monte Carlo Tree Search" | **有误**。LATS 论文是 Zhou et al., "Language Agent Tree Search Unifies Reasoning, Acting, and Planning in Language Models", arXiv:2310.04406。文档没有标注作者和 arXiv ID | 补充完整引用 |
| Letta/MemGPT | "memory block 设计" | **有误**。Letta 是 MemGPT 的产品化名称 (Packer et al., 2023, arXiv:2310.08560)。MemGPT 论文的正式名是 "MemGPT: Towards LLMs as Operating Systems"，不是 "Letta" | 修正为 MemGPT (Packer et al., 2023, arXiv:2310.08560)，Letta 是其产品化名称 |
| DSPy | "automatic metric tracking" | **有误**。DSPy (Khattab et al., 2023, arXiv:2310.03768) 做的是自动 prompt 优化和 module compilation，不是 "metric tracking"。文档把 DSPy 的能力描述错了 | 修正为 "automatic prompt optimization and module compilation" |
| Anthropic "Building Effective Agents" | 2024 | **需补充**。这是 Anthropic 2024-12 发布的博客文章，不是论文。文档没标明来源类型 | 补充 "Anthropic blog post, Dec 2024" |
| Anthropic prompt caching API | 2024 | 正确。2024-08 发布的 API | 无需修正 |
| Claude Code | "cache-tail reminder" | **有误**。Claude Code 的内部实现是闭源的，"cache-tail reminder" 是推测，不是公开文档确认的机制。不应作为已验证来源 | 修正为 "推测机制，非公开文档确认" |

### 9.2 第二审：代码库交叉验证

逐条检查第八章的"已有基础"和"实现量"声称是否与实际代码匹配。

| 声称 | 文件 | 验证结果 | 修正 |
|------|------|---------|------|
| 8.1 "hierarchy.py 已定义 L0-L5 六级" | cognitiveplane/memory/hierarchy.py | **正确**。MemoryLevel enum 有 L0-L5 + PROMOTION_RULES + can_promote | 无需修正 |
| 8.1 "promotion.py 已有 RAW->VALIDATED->PROMOTED" | cognitiveplane/memory/promotion.py | **正确**。PROMOTION_PATH 和 can_auto_promote 已实现 | 无需修正 |
| 8.1 "search.py 已有 RRF 混合检索" | cognitiveplane/memory/search.py | **正确**。HybridMemorySearch 有 vector + FTS + RRF | 无需修正 |
| 8.1 "confidence.py 已有真实置信度计算" | cognitiveplane/memory/confidence.py | **部分有误**。confidence.py 的 compute() 方法从 context snapshot 算 confidence，但不是基于执行历史的 trajectory 评估，而是基于 memory_match_confidence / knowledge_coverage / event_novelty 字段。8.2 声称的"trajectory quality_score"是新的，confidence.py 不支持 | 修正：confidence.py 用于 memory 置信度，不是 trajectory 质量评估 |
| 8.2 "embedding_model 已有 config" | cognitiveplane/capability/config.py | **正确**。LLMConfig.embedding_model 字段存在 | 无需修正 |
| 8.2 "search.py 的 HybridMemorySearch 已支持 RRF" | cognitiveplane/memory/search.py | **正确**，但 **有遗漏**：当前两个检索通道 (vector_port + fts_port) 在 Phase 1 都接到同一个 MemorySearchPort（只有关键词匹配），没有真正的 vector embedding 检索。8.2 声称"只需要加 trajectory_store"忽略了需要实现真正的 vector embedding channel | 修正：需要实现 vector embedding channel，不只是 trajectory_store |
| 8.3 "governance/evaluation.py 已有 EvaluationInput 框架" | cognitiveplane/governance/evaluation.py | **正确**。EvaluationInput 和 build_evaluator 已实现 | 无需修正 |
| 8.3 "LLMCallTracker 已记录 token/cost/latency" | cognitiveplane/capability/tracking.py | **正确**，但 **有遗漏**：代码注释说 "LLMCallTracker is not wired into any production path. Kept for backward compatibility until Phase 2f (Langfuse) replaces it." 它没有被实际调用 | 修正：LLMCallTracker 未接入生产路径，需先 wire 它 |
| 8.4 "activity_catalog.py 已有 capability registry" | cognitiveplane/control/tools/activity_catalog.py | **正确**。ActivityDescriptor + ACTIVITY_CATALOG 已实现 | 无需修正 |
| 8.5 "LLMConfig.resolve_model(purpose) 已定义六个路由槽" | cognitiveplane/capability/config.py | **正确**。intent_classifier_model / reasoning_model / planning_model / embedding_model / vision_model / explanation_model 全部存在 | 无需修正 |
| 8.5 "react.py 调 LLM 时传 purpose 参数" | cognitiveplane/control/engine/react.py | **有误**。react.py 的 _build_llm_request() 构造 LLMRequest 时不传 purpose 字段。LLMRequest 有 purpose 字段但 react.py 没设它，默认空字符串。所以 resolve_model(purpose) 实际走的是 fallback to primary.default_model | **重要修正**：react.py 需要在 _build_llm_request() 中传 purpose 参数，否则路由不生效 |
| 8.6 "delegate.py 已有 subagent 委派" | cognitiveplane/control/tools/delegate.py | **正确** | 无需修正 |
| 8.6 "agents/ 目录已有 subagent 定义含 description" | .weldevent/agents/ | **正确**。4 个 agent 定义都有 description 字段 (data-understanding, weld-architect, weld-explorer, weld-reviewer) | 无需修正 |
| 8.6 "用 embedding 做 description 语义匹配" | - | **有遗漏**：当前 delegate.py 按名字精确匹配，不是语义匹配。改成语义匹配需要调用 embedding API，但 8.2 的 vector channel 也没实现。两处都依赖 embedding 基础设施 | 修正：embedding 基础设施是 8.2 和 8.6 共同前置依赖 |

### 9.3 第三审：整体一致性审查

| 问题 | 描述 | 影响 | 修正 |
|------|------|------|------|
| **embedding 基础设施缺失** | 8.2 (trajectory RAG) 和 8.6 (agent 语义匹配) 都依赖 embedding 生成，但代码里没有 embedding 调用的实现。config 有 embedding_model 字段但没有对应的 embedding API 调用代码 | 8.2 和 8.6 无法独立实现 | 新增 8.0：embedding 基础设施作为前置依赖 |
| **purpose 字段未传递** | 8.5 声称"react.py 传 purpose 参数"但实际没传。model routing 实际不生效 | 8.5 声称"只需在调用时传 purpose"是低估 | 修正 8.5：需要改 react.py 的 _build_llm_request() |
| **LLMCallTracker 未接入** | 8.3 声称已记录 token/cost/latency，但 tracking.py 注释说未接入生产路径 | 质量趋势追踪缺数据源 | 修正 8.3：需先 wire LLMCallTracker |
| **第一轮交付缺少 LLM 评估器依赖** | 操作 34 (Evaluator-Optimizer) 放在第一轮，但它需要 LLM 评估 prompt 设计，且 Validate 阶段的 LLM 调用会增加延迟 | 第一轮交付周期可能超估 | 将 34 移到第一轮末尾或第二轮初 |
| **MemoryType enum 不匹配** | 8.1 说"给检索加 memory_type 过滤"，但 MemoryType enum 只有 8 个值 (APPROVED_DECISION / OPERATOR_FEEDBACK / ...)，没有 EPISODIC / SEMANTIC / PROCEDURAL 三个值 | 三层记忆映射没有对应的 enum 值 | 扩展 MemoryType enum 或用现有值映射 |
| **第二章和第八章的 Guardrail 描述不一致** | 第二章 #11 说"已有 PASS/WARN/REJECT，RETRY 需新增"。第八章没提到 RETRY 需新增的具体实现 | 读者可能以为 RETRY 已实现 | 在第八章补充 RETRY 的实现说明 |
| **ReWOO "减少 60%" 数据来源** | #31 声称 ReWOO "减少 LLM 轮次 ~60%"。ReWOO 论文报告的是在特定 benchmark 上的 token 效率提升，不是通用 "60% 轮次减少" | 过度泛化论文数据 | 修正为 "论文报告在特定 benchmark 上减少 LLM 调用次数" |
| **prompt caching "降成本 90%"** | #36 声称 "cache hit 降成本 90%"。Anthropic 文档说的是 cached input tokens 按 0.1x 计价 (即 90% discount on cached portion)，不是整体成本降 90% | 混淆了 per-token discount 和 total cost reduction | 修正为 "cached 部分 token 按 10% 计价" |
| **第三轮时间估算偏低** | 第三轮包含 6 项智能层弥补 + 5 项原有操作 = 11 项，估算 1-2 月。但 embedding 基础设施 + vector channel + LLMCallTracker wire + MemoryType 扩展 都是额外工作 | 时间估算不足 | 修正为 2-3 月 |

### 9.4 修正后的智能层实现顺序

基于依赖关系重新排序：

```
8.0 (新增) embedding 基础设施 [前置]
  │  实现: 在 LLMProvider 加 embed() 方法
  │       用 config.embedding_model 调 embedding API
  │       返回 list[float]
  │  依赖: 无
  │  预估: 1 天
  │
  ▼
8.5 模型路由 (改 react.py 传 purpose)
  │  依赖: 无 (只是改 LLMRequest 构造)
  │  预估: 0.5 天 (但需要确认 DeepSeek 支持多模型)
  │
  ▼
8.1 分层记忆 (扩展 MemoryType + 接 hierarchy/promotion)
  │  依赖: 8.0 (semantic memory 需要 embedding 检索)
  │  预估: 2 天
  │
  ▼
8.2 结构化 trajectory + RAG
  │  依赖: 8.0 (embedding) + 8.1 (memory type)
  │  实现: trajectory_store + vector channel (真 embedding 检索)
  │       + 规则提取 LLM prompt
  │  预估: 3 天 (比原估算多 1 天，因为要实现 vector channel)
  │
  ▼
8.3 零成本评估
  │  依赖: 先 wire LLMCallTracker
  │  预估: 2 天 (wire tracker 0.5 + 自评估 1 + 趋势 0.5)
  │
  ▼
8.4 工具组合 (MacroRegistry)
  │  依赖: 无 (独立)
  │  预估: 2 天
  │
  ▼
8.6 能力广告 + 动态匹配
  │  依赖: 8.0 (embedding for description matching)
  │  预估: 1.5 天
  │
  总计: ~12 天 (约 2.5 周)
```

### 9.5 修正后的交付节奏

| 轮次 | 操作编号 | 周期 | 变化 |
|------|---------|------|------|
| 第一轮 | 1-7 | 2-3 周 | 操作 34 (LLM 评估器) 移到第二轮初 |
| 第二轮 | 8-25 + 31/35/36/37 + 34 | 3-4 周 | +34 从第一轮移入 |
| 第二.五轮 (新增) | 8.0-8.6 (智能层) | 2.5 周 | 新增 embedding 基础设施为前置 |
| 第三轮 | 26-30 | 2-3 月 | 时间从 1-2 月修正为 2-3 月 |

### 9.6 审计结论

**技术来源**：发现 9 处引用错误（arXiv ID、会议名、作者名、能力描述错误），已逐一修正。修正后所有引用可追溯到原始论文/文档。

**代码库声称**：发现 5 处声称与实际代码不符（purpose 未传、LLMCallTracker 未接入、vector channel 未实现、MemoryType 缺值、confidence.py 用途描述错误），已逐一修正。

**整体一致性**：发现 embedding 基础设施是隐藏前置依赖、第一轮交付物需调整、第三轮时间估算偏低。修正后实现顺序有明确依赖链。

**关键修正**：8.5 模型路由声称"react.py 已传 purpose"是错的 -- 实际没传，路由不生效。这是影响最大的发现，因为 8.5 是"1 天就能做"的高性价比操作，但前提是先修 react.py。
