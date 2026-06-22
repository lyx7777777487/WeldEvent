# 01 — 项目现状与误判修正

记录实际读代码后看到的事实，以及前期建议中被推翻的部分。

## 关键事实

### L2 是 template-driven 解释器，不是 code-driven workflow

`controlplane/runtime/template_workflow.py` 的 `TemplateWorkflow`：

- 入参 `template_ref: dict`，通过 `load_template` activity 把 `WorkflowTemplate` 拉回来
- 按 template 中的 `control_points` 顺序执行
- 每个 control point 可以：绑 Activity（按 `activity_name` 派发）/ 带 human gate / 走条件 transition
- Retry / timeout / heartbeat / search attributes / human gate（update + query）全做了
- ~300 行写完了一个相当完整的 control plane runtime

**含义**：本次测试根本不需要写新 Workflow。L2 的活儿在架构里被一次性写完了。

### `WorkflowTemplate` schema 已经定死

`controlplane/domain/template.py`：

```python
@dataclass(frozen=True)
class WorkflowTemplate:
    id: str
    version: str
    control_points: list[ControlPointDefinition]
    transitions: list[TransitionDefinition]
    entry_point: str
    migration_strategy: MigrationStrategy | None = None

@dataclass(frozen=True)
class ControlPointDefinition:
    id: str
    name: str
    activity_binding: ActivityBinding | None
    execution_policy: ExecutionPolicy
    human_gate: HumanGateDefinition | None = None

@dataclass(frozen=True)
class ActivityBinding:
    activity_name: str
    activity_version: str = "latest"
    task_queue: str | None = None
    execution_target: str | None = None
```

**含义**：L1 与 L2 之间的契约 = 这个 dataclass 的 dict 表示。L1 不需要发明新 DTO。

### `load_template` activity 支持注入 repository

`controlplane/adapter/template_loader.py`：

```python
def create_load_template_activity(repository=None):
    @activity.defn(name="load_template")
    async def load_template(template_ref: dict) -> dict:
        if repository:
            template = await repository.load(
                template_ref["template_id"],
                template_ref["template_version"],
            )
        else:
            template = None
        if template is None:
            raise ValueError(...)
        return _make_json_safe(asdict(template))
    return load_template
```

**含义**：第一版写一个 `FileTemplateRepository`（文件 repo，见 [05-Q4](05-open-questions.md)），worker 启动时传进去，L1 design 阶段把 LLM 产出的 template save 成 `templates/{id}_{version}.json`。worker 和 L1 跨进程通过文件共享，无需 in-memory 实例共享。

### `ActivityInput` / `ActivityOutput` 是无 schema 的 dict 容器

`controlplane/domain/activity.py`：

```python
@dataclass
class ActivityInput:
    control_point_id: str
    workflow_context: dict
    params: dict | None = None

@dataclass
class ActivityOutput:
    status: ActivityStatus   # OK / MARGINAL / NG / ERROR
    data: dict | None = None
    error: str | None = None
```

**含义**：`params` 和 `data` 都是 `dict | None`，完全无 schema。每个 Activity 必须自己定义内部 contract，否则 L2 不知道塞什么、Workflow 拿到的 data 不知道怎么用。建议每个 Activity 文件头用 dataclass 写一份 params/data schema 作为文档。

### `controlplane/worker.py` 现在传的是 mock

```python
activities = list(ALL_MOCK_ACTIVITIES) + [create_load_template_activity()]
worker = Worker(
    client,
    task_queue=config.task_queue,
    workflows=[TemplateWorkflow],
    activities=activities,
)
```

**含义**：需要新增一个文件把 `AnnotationActivity`（BaseActivity 实例）适配成 `@activity.defn(name="annotation")` 函数，加进 activities 列表。胶水代码建议放 `executionplane/activities/annotation/__init__.py`，跟 `iqa/` 同级。

### L1 已有的标注系统接口

`executionplane/interface/iqa_interface.py`：

- `run_iqa(image_path, standard_id, workflow_id, mllm_enabled) → IqaResult`
- `run_iqa_batch(image_paths, ...) → list[IqaResult]`
- `run_iqa_folder(folder_path, ...) → list[IqaResult]`
- `IqaResult` 字段：status / route_decision / confidence / 4 项 check 明细 / deep_vision 异常 / error

**含义**：Python API 是给程序看的，MCP 工具是给 LLM 看的，两者不应该是 1:1。MCP 工具粒度按"决策颗粒"切，不照搬 Python API 字段。

## 推翻的前期建议

| 前期建议 | 推翻理由 |
|---|---|
| 新写 `AnnotationWorkflow` class | L2 是 template-driven，不该再写 code-driven workflow |
| 新写 `AnnotationRequest` DTO | L1↔L2 契约是 `WorkflowTemplate` dict，不需要新 DTO |
| 在 `controlplane/contracts/` 集中跨层 DTO | 模板抽象一刀切完了，不存在"层间 DTO 难"的问题 |
| L2 用 `WorkflowEnvironment.start_local()` 做 integration test | 依然成立 ✅ |
| MCP 会话每次 Activity 调用 open/close | 依然成立 ✅，但取决于 MCP transport（见 05-open-questions.md） |