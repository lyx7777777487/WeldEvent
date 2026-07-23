# WeldEvent 评估模块设计 - 基于读码的现状与方案

**日期**: 2026-07-20
**源**: docs/superpowers/specs/2026-07-20-execution-situation-catalog.md (315 条情况清单) + 实读代码核实

## 一、关键判断: 评估设施 80% 已存在, 缺组织层

读码确认评估的"原料"已齐, 散在三个平面但都已实现:

| 评估能力 | 已有代码 | 位置 |
|---|---|---|
| L0 状态机迁移 | NodeStateMachine (9态+20事件+迁移表+7不变量+append-only history) | controlplane/runtime/node_state_machine.py |
| 决策审计链 | DecisionRecord (immutable+content_hash) + AuditTrail + verify_integrity | cognitiveplane/audit/decision_record.py |
| 执行记录 | WorkflowExecutionRecord + NodeExecutionRecord (含 quality_score) | cognitiveplane/audit/execution_record.py |
| 模式/案例 | PatternStore + CBRStore (bigram 检索) | cognitiveplane/audit/pattern_learning.py |
| 事件重放 | DecisionPipelineView (从事件流重建状态) | cognitiveplane/control/event_log.py |
| LLM 轨迹 | TrajectoryRecorder + ContinuableSnapshot | cognitiveplane/control/trajectory.py |
| 指标 | InMemoryCounter/Histogram (OTel stub) | cognitiveplane/adapters/observability/metrics.py |
| 追踪 | Langfuse v4 (已接入, 无 key 降级 NoOp) | cognitiveplane/adapters/observability/tracing.py |
| LLM-as-Judge | WeldingLLMJudge (4维度) + LangfuseScoreRecorder + NoOp 降级 | cognitiveplane/governance/evaluation.py |
| Golden set | GOLDEN_SET (4条) + run_golden_set + __main__ 可跑 | cognitiveplane/governance/eval_dataset.py |

**结论: 不要重建, 要组织.** 评估模块核心价值是把散件组织成"能回答系统可不可信"的判断层.

## 二、四层可信度金字塔

| 层 | 评什么 | 确定性 | 速度 | 成本 | 现状 |
|---|---|---|---|---|---|
| L0 状态机 | 迁移 + 7 不变量 + 非法迁移拒绝 | 100% | ms | 0 | ✅ 22测试绿, 已接主路径(shadow) |
| L1 回放确定性 | 给定 trajectory 重放结果一致 | 高 | s | 低 | ⚠️ DecisionPipelineView 有能力无驱动器 |
| L2 情况覆盖 | 315 条情况是否触发且正确 | 中 | s~min | 中 | ✅ 覆盖矩阵已建 (316条, 13 tested) |
| L3 语义质量 | LLM 输出真值 (golden + judge) | 低 | min | 高(真LLM) | ✅ 框架在, golden set 仅4条 |

CI 分级: L0+L1 每次提交(s); L2 nightly(min); L3 周度真实 LLM(贵).

## 三、已完成 (2026-07-20)

### 1. Langfuse 底座打通
- 修 `.env`: 填 jp 区 key, 修 host 错值 (`clogfuse.comud.lan` -> `jp.cloud.langfuse.com`)
- 修 `tracing.py:64` bug: TracingConfig.host 硬编码 `cloud.langfuse.com` 导致 env 的 LANGFUSE_HOST 读不到, 改 None 让 env 生效
- 验证: trace 上报 -> API 可查, 端到端通 (jp 区域)

### 2. L3 评估设施验证 + 修复
- 跑通 golden set: DeepSeek judge + Langfuse trace 联动
- 发现并修复 judge prompt 缺陷: 拦截/拒绝危险请求被判 safety=0 (应 1.0)
- 修 evaluation.py:126 prompt, 重验 4/4 passed

### 3. L2 情况覆盖矩阵 (评估尺子)
- 情况清单纳入仓库: docs/superpowers/specs/2026-07-20-execution-situation-catalog.md (315条)
- 三分类文档纳入: docs/superpowers/specs/2026-07-20-status-vs-frontier-three-category.md
- 建机器可读映射: controlplane/tests/situation_coverage/situations.yaml (316条, 每条含 id/desc/tier/coverage/test)
- 建覆盖追踪器: controlplane/tests/situation_coverage/coverage_tracker.py
  - 自动扫描测试文件发现 test_<ID>_* 命名测试 (支持中文函数名)
  - 认 declared_test 字段 (非 ID 命名测试, 如 test_standard_happy_path)
  - 输出覆盖度报告 + CI 门禁 (--check)
- 首次覆盖度: 316 条, tested=13 (4.1%), covered=106, pending=197
  - L0: 12/66 tested | L1: 0/80 | L2: 1/132 | L3: 0/38
- 建上报器: controlplane/tests/situation_coverage/report_to_langfuse.py
  - 覆盖度作为 score 挂到 Langfuse `situation-coverage` trace
  - 趋势线在 Langfuse 看板可视化
  - 已验证 8 个 score 落库

### 4. 安全
- 补建 .gitignore (之前没有), 确认 .env 被忽略
- git 历史搜无 key 泄露

## 四、下一步 (按性价比)

### Phase A: dag_runner 全链路 @observe (1-2天)
现在 @observe 只在 openai_provider LLM 调用点. 给 dag_runner 关键阶段加:
- PREPARE/EXECUTE/VALIDATE/COMMIT 各一个 span
- 7 类决策 (review/rework/saga/revoke/signal/budget/plan_revision) 各一个 span
- node_state_machine 的 illegal transition 上报为 ERROR level
收益: Langfuse 看板看到完整三平面执行, 是后续评估基础.

### Phase B: 评估分挂 Langfuse (2天)
- run_golden_set 用 evaluation.py 已有的 make_evaluator/LangfuseScoreRecorder 把分挂到 trace
- evaluate_node_quality activity 的分也挂上 (现在只评 L1 回复, 节点级没接)
- Langfuse 看板出评分趋势图

### Phase C: 扩 golden set 4->50 (3-4天)
- 从 audit/pattern_learning.py 的历史成功案例回流
- 覆盖焊接质检场景多样性 (正常咨询/危险拦截/违规参数/国标/工艺推荐/缺陷判定)
- 回归基线加厚

### Phase D: L1 回放确定性 (3-4天)
- wrap DecisionPipelineView 成 replay_runner
- 喂事件脚本断言终态 + 不变量
- I2/I4 (replay 不一致/上下文漂移) 补测试

### Phase E: 状态机 shadow->硬断言 (1-2天)
现在 _assert_sm_consistency 只 warn. 评估 harness 侧捕获 warning 转测试失败.
dag_runner 零改动, 纯测试侧断言.

## 五、模块定位

评估不嵌进任一执行平面 (audit 在 cognitiveplane, 状态机在 controlplane), 是横切.
当前结构:
- 情况清单 + 映射表 + 追踪器: controlplane/tests/situation_coverage/ (已有)
- L3 judge + golden set: cognitiveplane/governance/ (已有)
- Langfuse 底座: cognitiveplane/adapters/observability/ (已有)
不新建独立 evalplane 目录, 复用现有位置, 避免重复造轮子.

评估尺子 = situations.yaml + coverage_tracker + report_to_langfuse 三件套.
贯穿后续所有开发: 做完一条情况, 在 YAML 标 tested + 加测试, 跑覆盖矩阵看进度.
