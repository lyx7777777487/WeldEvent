# 06 — L2/L3 已有基础设施（controlplane / executionplane）

记录 Test/src/controlplane 和 Test/src/executionplane 里拷过来的、本次测试**直接复用不重写**的代码。

## L2 — controlplane（已提供，本次测试不写新 Workflow）

| 文件 | 作用 |
|---|---|
| `runtime/template_workflow.py` | `TemplateWorkflow` — 模板驱动解释器。入参 template_ref dict → load_template → 按 control_points 顺序执行 → 按 transitions 跳转。L1 的产出（一个 dict）就是它的输入 |
| `runtime/control_point.py` | `ControlPointExecutor` — CP 状态机（enter/wait_gate/complete/fail/transition） |
| `runtime/human_gate.py` | `HumanGateRuntime` — gate 动作管理（CONTINUE/REDIRECT/TERMINATE） |
| `runtime/transition.py` | `TransitionResolver` — 按 condition 解析下一个 CP |
| `domain/activity.py` | `ActivityInput` / `ActivityOutput` / `ActivityStatus` — Activity 契约 |
| `domain/template.py` | `WorkflowTemplate` / `ControlPointDefinition` / `ActivityBinding` / `TransitionDefinition` — 模板 schema |
| `domain/execution.py` | `WorkflowState` / `WorkflowContext` / `StateTransition` — 运行态 |
| `domain/human_gate.py` | `GateAction` / `HumanGateDefinition` |
| `domain/policy.py` | `ExecutionPolicy` / `RetryPolicy` / `TimeoutPolicy` |
| `adapter/template_loader.py` | `create_load_template_activity(repository=)` — 注入 repository 钩子 |
| `adapter/mocks.py` | 8 个 mock activity（iqa/ppa/mea/rda/vda/rva/mta/hca）— 其他 CP 的占位 |
| `adapter/base.py` | `ActivityAdapter` ABC（第一版未用，留作接法 B 的预留） |
| `worker.py` | Worker 注册入口 — **已改**：注入 `FileTemplateRepository` + 注册真实 `annotation_activity` |
| `config.py` | `ControlPlaneConfig` — temporal_host / namespace / task_queue |

## L3 — executionplane（已提供 + 本次新增）

| 文件 | 作用 |
|---|---|
| `activities/base.py` | `BaseActivity` ABC — 生命周期 hook（on_start/on_complete/on_error/run）。**第一版 annotation activity 不继承它**（接法 A 裸函数），保留作参考 |
| `activities/annotation/activity.py` | **本次新增** — `@activity.defn(name="annotation")` 裸函数。调 `shared.mcp_tools.annotate_tool`，含 heartbeat + 业务/基础设施失败分流 |
| `activities/annotation/__init__.py` | 导出 `annotation_activity` 和 `ANNOTATION_ACTIVITY_NAME` |

### 接法 A 的决定（不接 BaseActivity）

原 `controlplane/adapter/mocks.py` 里 8 个 mock **都是裸 `@activity.defn` 函数**，不是从 BaseActivity 实例适配来的。原 `executionplane/activities/iqa/activity.py` 里的 `IqaActivity(BaseActivity)` **根本没注册成 Temporal activity** — worker.py 跑的是 mocks.py 的裸函数。

所以本次 annotation activity 跟随现有代码风格，写裸函数。理由：
- MVP 阶段跟现有代码风格一致最重要
- BaseActivity 的 on_start/on_complete/on_error 生命周期 hook 第一版用不上
- 接法 B（adapter 桥接 BaseActivity）要给 executionplane 引入新架构模式且逼 IqaActivity 跟着改，超出本次测试范围

## iqa 样板

`executionplane/activities/iqa/activity.py` 是真实的 BaseActivity 实现，但依赖 numpy/cv2/weldmap/capabilities 等一堆重依赖，**没拷进 Test/**。需要时回主仓库 `executionplane/activities/iqa/` 看样板。

## L4 — MCP server（外部提供）

本次测试不写 MCP server，依赖标注系统设计师给的 server。
- 测试用 `shared/mcp_tools/mock_mcp_server.py`（in-process 假 server）
- 接真 server 时设环境变量 `MCP_TRANSPORT=http` + `MCP_SERVER_URL=...`（见 [05-open-questions.md](05-open-questions.md) Q1）