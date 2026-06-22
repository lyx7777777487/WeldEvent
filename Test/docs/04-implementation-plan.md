# 04 — 落地顺序

每步独立可测。每步跑通再做下一步。中途暴露接口设计问题回头改代价小。

## 第 1 步：MCP 工具层 + mock MCP server

**产出文件**：
- `shared/mcp_tools/annotate_tool.py` — MCP client 包装，L1 和 L3 都 import（中性位置，见 02-architecture.md 接口 2）
- `shared/mcp_tools/mcp_session.py` — MCP 会话管理（每次 open/close）
- `shared/mcp_tools/tests/mock_mcp_server.py` — 测试用 in-process mock

**测什么**：
- `test_annotate_tool.py`：调 mock MCP，验证入参出参正确
- 不依赖 L2、不依赖 L1

**完成判据**：`python -m shared.mcp_tools.annotate_tool /path/to/image.png` 能调通 MCP server（先 mock，后真）。

## 第 2 步：AnnotationActivity 接进 worker

**产出文件**：
- `executionplane/activities/annotation/__init__.py`
- `executionplane/activities/annotation/activity.py` — `AnnotationActivity(BaseActivity)`
- `executionplane/activities/annotation/adapter.py` — 把 BaseActivity 实例适配成 `@activity.defn(name="annotation")` 函数
- `controlplane/worker.py` 改：把 mock annotation 换成真 adapter，注入 `FileTemplateRepository`（见 05-Q4）

**测什么**：
- `test_annotation_activity.py`：Activity 单跑，MCP 全 mock，验证 `ActivityOutput` 字段对
- `test_activity_adapter.py`：BaseActivity 实例 → Temporal activity 函数的桥接对

**完成判据**：单测全过。

## 第 3 步：WorkflowRunner + Integration Test

**产出文件**：
- `shared/template_repository/file_repository.py` — `FileTemplateRepository`（中性位置，worker 和 L1 都 import，避免反向依赖）
- `l1_agent/execution/workflow_runner.py` — Temporal Client 包装
- `l1_agent/tests/test_workflow_runner.py`

**测什么**：
- 用 `temporalio.testing.WorkflowEnvironment.start_local()` 拉起真 Temporal（不用 docker）
- 构造一个最小 template（只有 `annotation` 一个 CP）
- mock MCP，跑通完整一次 `TemplateWorkflow`
- 验证：worker 派发到 `annotation` activity → activity 调 mock MCP → workflow 返回 result

**完成判据**：integration test 通过。这是"L2 真用到"的关键证据 — `start_local()` 是真 Temporal SDK 在跑。

## 第 4 步：WorkflowDesigner + HumanReviewer + main

**产出文件**：
- `l1_agent/design/workflow_designer.py`
- `l1_agent/design/template_validator.py`
- `l1_agent/design/human_reviewer.py`
- `l1_agent/main.py`
- `l1_agent/config.py`

**测什么**：
- `test_designer.py`：mock LLM，验证产出合法 template
- `test_validator.py`：各种非法 template 被拒
- `test_e2e.py`：mock LLM + mock MCP + `start_local()`，从 requirement 到 ResultRenderer 输出走通，**断言整条链路 0 报错**
- `test_correctness.py`（独立于 e2e）：同一张图，agent 链路里 `annotate_tool` 的原始返回 vs 直连 mock MCP，逐字段比对，**断言 100% 一致**。这是北极星"正确性"指标，与 e2e 的"0 报错"分开

**完成判据**：`python -m l1_agent.main "对这批焊缝图做宏观标注" /path/to/dataset` 跑通端到端。

## 第 5 步（可选）：接真 MCP server

把第 1 步的 mock MCP 换成真 server。需先确认 [05-open-questions.md](05-open-questions.md) 里的 MCP transport。

## 时间估算

| 步骤 | 预计 |
|---|---|
| 1 | 半天 |
| 2 | 半天 |
| 3 | 1 天 |
| 4 | 1 天 |
| 5 | 半天 |

合计约 3.5 天能跑通端到端。

## 北极星指标

| 维度 | 怎么测 | 通过线 |
|---|---|---|
| 正确性 | 同一张图，agent 链路里 `annotate_tool` 的原始返回 dict vs 直连 MCP server 调用同一工具的返回 dict，**逐字段比对** | 100% 一致 |
| 稳定性 | 同一 requirement 跑 30 次，看 LLM 产出的 template 是否一致 | ≥ 28/30 |
| 退化点 | 故意让 MCP 工具返回业务失败，看 Activity 是否转成 ERROR output 且不重试 | 不重试，返回 ERROR |

**重要**：正确性比对的是 `annotate_tool` 的原始返回（结构化 dict），**不是** ResultRenderer 渲染后的自然语言。后者是 LLM 自由发挥，无法 100% 一致。

`test_e2e` 是这次测试的真正北极星 — 通过即代表 L1+L2+L3 三层接口都对了。