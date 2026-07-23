# 系统现状对照前沿 + ❓ 三分类

> 基于**实际读代码**（`/Users/liuyixuan/WeldEvent`，约 5.7 万行）的评估，不是只看文档。
> 读码发现：基础执行层扎实，但 v2（4阶段）写了没接入，8 个新模块全部未实现。

---

## 一、对照前沿：已实现 / 半实现 / 未实现

> 用代码 grep + 读 `_execute_node_v2` 核实。✅真实现并生效 · 🟡写了未接入 · ❌未实现。

### 1.1 第一轮（让工作流可控 1-7+34）-- 基础最扎实

| Op | 机制 | 代码核实 | 状态 | 前沿对齐 |
|---|---|---|---|---|
| 1 | ReviewPolicy 四策略 | `workflow_spec.py:29` `Literal["auto","required","conditional","never"]` | ✅ 生效 | 对齐 LangGraph interrupt 思想 |
| 2 | ReviewResult 五决策 | dag_runner `Op 2` 注释+`_review_results` | ✅ 生效 | 对齐 Command(resume) |
| 3 | on_failure 分流 | `_handle_on_failure`，但"continue/retry 当前等同 escalate" | 🟡 部分（2/5 未真分流） | retry 对齐 Temporal RetryPolicy ✓ |
| 4 | inject_context | `_injected_context` + signal | ✅ 生效 | 对齐 Magentic Task Ledger |
| 5 | 幂等键 | `idempotency_key`（**只在 v2 路径**，主循环未用） | 🟡 未接入 | 对齐 Temporal 最佳实践 |
| 6 | heartbeat | activity 层**未实现**（仅 design doc 提及） | ❌ 缺失 | 前沿必需（流式+保活） |
| 7 | NodeAttempt 四阶段 | `_execute_node_v2` 完整实现，但注释"主循环仍用原逻辑" | 🟡 **写了没切换** | 对齐 draft->commit |
| 34 | Evaluator-Optimizer | `_validate_node_result` 有 quality_score，MARGINAL 降级 | 🟡 在 v2 路径 | 对齐 Anthropic |

**结论**：基础可控层 8 项里 3 个生效、3 个写了没接入、1 个部分、1 个缺失。**最该先做的是把 v2 切成主循环**（Op7+Op5+Op34 一起激活），这是零成本就能拿到的最大收益。

### 1.2 第二轮（可回溯 8-25+31/35/36/37）

| Op | 机制 | 状态 | 备注 |
|---|---|---|---|
| 8/9 | ContinuableSnapshot/ToolEffectRecord | 🟡 | trajectory.py/event_log.py 存在，接入度待查 |
| 11 | Guardrail 统一 | ✅ | `guardrails.py` PASS/WARN/REJECT/RETRY 全有 |
| 12 | 安全压缩 | ✅ | `memory/compaction.py` 实现+有测试 |
| 19 | pause/resume/cancel | ✅ | 生效，30 分钟超时自动取消 |
| 20 | rework_nodes | ✅ | 生效，下游闭包 |
| 21 | Saga 补偿 | ✅ | `_saga_compensations` 生效 |
| 37 | 批量信号 | ✅ | `_apply_pending_signals` 生效 |
| 10/36 | cache-tail/prompt caching | ❌ | react.py 未传 purpose，cache 边界未做 |
| 31/35 | ReWOO/并行委派 | ❌ | 仍是串行委派 |

### 1.3 第三轮（学习 26-30）-- memory 模块最完整

| Op | 机制 | 状态 |
|---|---|---|
| 26 | ArtifactVersion | ✅ `weldmap/models.py` |
| 27 | DecisionRecord | ✅ `audit/decision_record.py` |
| 29 | ExecutionRecord | ✅ `audit/execution_record.py` |
| 30 | Pattern learning | ✅ `audit/pattern_learning.py` |
| 8.0 | embedding 基础设施 | ❌ 未实现（trajectory_store 在但 vector channel 缺） |
| 8.1 | 分层记忆 | ✅ `memory/hierarchy.py` L0-L5 全实现 |
| 8.2 | trajectory+RAG | 🟡 trajectory_store 存在，vector 检索缺 |

### 1.4 八个新模块 -- **全部未实现**

| 模块 | 代码文件数 | 状态 |
|---|---|---|
| pause_scope | 0 | ❌ |
| batch_hold | 0 | ❌ |
| relabel_request | 0 | ❌ |
| ground_truth_override | 0 | ❌ |
| delegate_review | 0 | ❌ |
| revoke_approval | 1（弱） | ❌ |
| standard_update | 0 | ❌ |
| case_library_correction | 0（case_library 本体5，非纠错） | ❌ |

**对照前沿的总判断**：

- **骨架与前沿对齐良好**。三平面分层（L1认知/L2控制/L3执行）、memory L0-L5、guardrail 四态、Saga 补偿、signal 批量、Temporal 耐久执行--这些是 2024-2025 主流 agent 工作流的正确选型，方向没错。比我研究的前沿库里很多项目还规整。
- **但停在"第一轮已实现，v2 未切换"**。最大的工程债不是缺模块，是 `_execute_node_v2` 写好了没接进主循环--相当于买了新引擎没装上车。
- **真正落后前沿的三块**：① 8 模块（干预治理层，前沿里 Magentic-UI 的 check-in、Temporal Reset 都是这类，你一个没做）；② 智能层 8.0/8.2 的 embedding+vector（让记忆真正可检索）；③ heartbeat（流式与保活，前沿标配）。
- **一个好消息**：你担心的"技术和思想能否对齐前沿"--已实现的部分基本对齐，**不需要重写**，缺的是补齐和接入。

---

## 二、❓ 三分类（状态机能答 / 需结构决定 / 需领域判断）

> 从清单 ~180 个 ❓ 里归类。**A 类（状态机能答）= 现在就能动，不需等任何人。**

### A 类：状态机直接回答（现在就能动，约 40%）

> 这些 ❓ 本质是"某态下来某事件怎么办"，查迁移表即得。不依赖你拍板，我可以直接定。

| ❓ | 状态机答案 |
|---|---|
| B6 COMMIT 前暂停恢复后重验还是直接提交 | PAUSED 记住挂起前态：原 EXECUTING→resume 回 EXECUTING 重跑；原 REVIEW→resume 回 REVIEW 可直接 approve |
| B12 暂停超时放弃 | PAUSED 加 timer 事件→cancel→FAILED（你已实现 30m 超时，对齐） |
| B13 暂停时间是否计超时 | 建议 PAUSED 期间 activity 计时暂停（状态机不管，交给 activity） |
| C11 上游变了用新/旧输入 | 上游 rework→本节点 PENDING→重跑取当下输入；旧结果保留可对比 |
| C8 连锁触发新问题 | 下游闭包递归，状态机自动传播 |
| C12 回溯与暂停叠加 | 看 PAUSED 时收 rework 事件→迁 PREPARING |
| O17 审查超时无人 | AWAITING_REVIEW 加 timer→escalate(=pause) 或 reject(=fail)，配默认 |
| M9 中断丢弃未提交 draft | draft 未 COMMIT→直接弃，合法迁移 |
| L6 已执行不回滚 | inject 不改态，已 COMMITTED 前用旧版，你已实现 |
| L7 未执行用新上下文 | PENDING/PREPARING 取最新版，自动 |
| L8 进行中节点处理 | 等下一 checkpoint 批量应用，你已实现 |
| I3 幂等键冲突 | 去重，状态机保证幂等 |
| B7 信号堆积 | checkpoint 批量，你已实现 |
| N11 已完成节点保留改时 | PENDING 重新调度，COMMITTED 保留 |
| 多数"某态下某事件"❓ | 查迁移表 |

**动作**：这批我可以直接写进状态机迁移表 + 测试，不用等你。建议**第一步就把 A 类全部消掉**。

### B 类：需结构决定（要先定结构 v1，约 35%）

> 这些 ❓ 依赖"你打算怎么搭结构"，结构定了就有答案。建议先定结构 v1（主要是：是否切 v2 为主循环、是否引入 8 模块的状态、是否做 embedding）。

| ❓ | 依赖的结构决定 |
|---|---|
| C4/C5 已转正式/离系统的回退路径 | 是否做 revoke_approval 模块 + Saga 补偿到外部 |
| B14 分级暂停组合 scope 交集/并集 | pause_scope 的 scope 模型设计 |
| B9 暂停期间上游回溯下游是否重跑 | 依赖失效传播规则（结构决定） |
| N6 改 review_policy 运行时生效 | PlanVersion 不可变 vs 运行时可改的边界 |
| N12 改时进行中节点处理 | modify_spec 与活跃节点的抢占规则 |
| F7 并行回溯冲突 | 是否引入悲观锁/版本号 |
| Q8 经验置信度阈值 | 记忆 promotion 规则（你 promotion.py 已有，定阈值即可） |
| Q9 新旧经验冲突按时间还是置信度 | 记忆冲突解决策略 |
| C15 批量回溯排序 | 调度器是否支持批量 rework 调度 |
| S13 版本升级时旧 Run 用旧还是新 spec | PlanVersion 版本迁移策略 |

**动作**：先做两个结构决定就能解锁大半--①**切 v2 为主循环**；②**定 PlanVersion 不可变边界**（改参数 Update / 改拓扑 Cancel+Relaunch，你 docs 已写但代码未实现）。

### C 类：需领域/业务判断（只能你拍板，约 25%）

> 这些涉及业务容忍度、成本、合规，Agent 给候选+权衡，你定。

| ❓ | 需你判断的 |
|---|---|
| C7 回退回退（重做更差）保留新版还是旧版 | 业务上"更差"是否可接受 |
| V9 终态成功但质量存疑交付还是扣留 | 交付 SLA vs 质量门槛 |
| O18 多审查人意见冲突裁决 | 资深优先/多数/升级，组织规则 |
| O16 多人会签 | 是否需要会签机制 |
| E9 结果与历史不一致判异常还是改进 | 焊接质检领域知识 |
| P8 外部环境变更重评追溯边界 | 合规追溯年限 |
| T1 敏感数据进 LLM 脱敏统一还是各节点 | 合规要求 |
| J1 预算耗尽 abort 还是降级 | 成本 vs 可用性 |
| G9 deadline 临近加速还是降级 | 业务实时性要求 |
| Q20-22 经验影响模型路由/Specialist/工具 | 自动调整还是人工审核 |

**动作**：这批不阻塞开发，可以先走 A/B 类。每条你拍板一条，我就能落一条。

---

## 三、给你 0 经验的下一步（基于真实代码，不是空谈）

你的系统不是"从 0 到 1"，是"**从 0.6 到 1**"--骨架已搭好，缺接入和补齐。所以路径和纯新手不同：

**第一步（最高性价比，1-2 天）：把 `_execute_node_v2` 切成主循环。**
它已经写好了，4 阶段+幂等+LLM 评估都在里面，只是没接。接进去你就免费拿到 Op5/Op7/Op34，且不引入新代码。这是你现在能动的最大收益。

**第二步（A 类❓，2-3 天）：把状态机迁移表写成显式代码+测试。**
现在状态逻辑散在 dag_runner 的 if/while 里，没显式状态机。把 9 状态+迁移表落成代码，A 类 40% 的 ❓ 自动消解（变成测试用例）。非法迁移加报警。

**第三步（B 类结构决定，1 周）：定两件事。** PlanVersion 不可变边界（Update vs Cancel+Relaunch）+ 是否引入 revoke_approval（已提交回退）。这俩定了，C 类回退的 B 类 ❓ 大半解锁。

**第四步（8 模块，按需）：** 不用全做。建议先做 revoke_approval（是其它模块误判的出口，最关键）+ batch_hold（批次隔离，焊接场景高频）。pause_scope 其实你的 pause/resume/cancel 已有，加个 scope 字段即可，最便宜。

**第五步（前沿补齐，2-4 周）：** heartbeat（流式）+ embedding 基础设施（让 8.1 分层记忆真正可检索）+ cache 边界（react.py 传 purpose）。这三块让你从"对齐前沿"到"达到前沿"。

> 一个判断：你现在的焦虑是"不知道够不够前沿"，但读了代码我认为**你的骨架比多数团队扎实**，问题不是方向错，是"已写的没接、该补的没补"。把第一步做了，你的体感会立刻从"一堆散件"变成"能跑的 4 阶段管线"。

