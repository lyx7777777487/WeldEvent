# 02 — 三层架构与接口契约

## 三层职责切割

| 层 | 第一版只做这件事 | 严格禁止 |
|---|---|---|
| L1 Agent | 根据需求 + 数据集生成 `WorkflowTemplate` dict → 人工 review → 启动 Temporal workflow → 等结果 | 引入 cognitiveplane 任何 import |
| L2 Workflow | 复用 `TemplateWorkflow`，按 template 中的 control_points 顺序调度 | 写新 Workflow class、加分支、加并发 |
| L3 Activity | `AnnotationActivity`：建 MCP 会话 → 调 server 工具 → 把结果塞进 `ActivityOutput.data` | 在 Activity 里做 LLM 调用、做决策 |

复杂度全压在层与层之间，每层内部刻意保持愚蠢。

## 接口契约（最小子集，对应 redesign §6.7 接口 0/1/2）

### 接口 0：L1 内部 — workflow_designer.design

```python
async def design(
    requirement: str,
    dataset_summary: dict,   # {"count": 120, "format": "PNG", "sample_paths": [...]}
) -> dict:                    # WorkflowTemplate 的 dict 表示
    ...
```

LLM 调用 DeepSeek function calling，工具只暴露 1 个：`emit_workflow_template`。
返回值必须能通过 `template_validator` 三重验证。

### 接口 1：L1 → L2 — workflow_runner.start

repository 直接存 dict，**不**重建 `WorkflowTemplate` dataclass。原因：`WorkflowTemplate` 是 frozen dataclass 且字段是嵌套 list，`**dict` 不会递归构造（`control_points` 会变成 list[dict] 而非 list[ControlPointDefinition]），手动重建逻辑见 `template_workflow.py:234-298` 的 `_dict_to_template`，复杂且与 repository 无关。`load_template` activity 返回的本来就是 `_make_json_safe(asdict(...))` 的 dict，repository 一路保持 dict 即可，重建由 `TemplateWorkflow._dict_to_template` 在 workflow 内部完成。

```python
async def start(template: dict) -> WorkflowHandle:
    # 1. save 到 template_repository（直接存 dict，不重建 dataclass）
    template_ref = {"template_id": template["id"], "template_version": template["version"]}
    await repository.save(template_ref["template_id"], template_ref["template_version"], template)
    # 2. 启动 Temporal workflow
    handle = await client.start_workflow(
        TemplateWorkflow.run,
        template_ref,
        id=f"annot-{template['id']}-{uuid4().hex[:8]}",
        task_queue=config.task_queue,
    )
    return handle
```

### 接口 2：L3 → L4 — annotate_tool.annotate

共享工具放中性位置，**不**放 `l1_agent/` 下（否则 L3 反向依赖 L1，破分层）：

```python
# shared/mcp_tools/annotate_tool.py（L1 和 L3 都从这里 import — DRY，无层间依赖）
async def annotate(image_path: str, **kwargs) -> dict:
    """调外部标注 MCP server 的 annotate 工具"""
    async with _mcp_session() as session:
        result = await session.call_tool("annotate", {"image_path": image_path, **kwargs})
        return _parse_result(result)
```

`shared/` 与 `executionplane/` / `controlplane/` / `l1_agent/` 平级，谁都不依赖谁。L1 和 L3 都向它依赖。

**关键约束**：L3 `AnnotationActivity` 和未来 L1 LLM 都 import 同一个 `annotate_tool`，工具实现只有一份。MCP 协议无状态，调用方不感知。

## redesign §6.7 五条接口的取舍

| §6.7 接口 | 第一版 | 理由 |
|---|---|---|
| 接口 0（LLM 决定触发 L2） | ✅ 保留（简化为 design → confirm 两步直线，不是 ReAct） | 测试核心 |
| 接口 1（L1→L2 触发） | ✅ 保留 | 启动 L2 |
| 接口 2（L3→L4 + 共享工具） | ✅ 保留 | 调 MCP |
| 接口 3（L2→WeldMap→L1 感知） | ❌ 砍 | 用 `await handle.result()` 同步拿结果 |
| 接口 4（前端反馈穿越） | ❌ 砍 | 留后续 |

## MCP × Temporal Activity 的关键坑

MCP stdio client 是有状态 session，Activity 是无状态、可重试、可能换 worker 跑。两者天然冲突。

| 方案 | 复杂度 | 推荐度 |
|---|---|---|
| A. 每次 Activity 调用都 open/close MCP session | 低 | 测试阶段 ✅ |
| B. Worker 启动时建一个 MCP session，所有 Activity 共享 | 中 | 生产再考虑 |
| C. 单独 MCP gateway 进程（HTTP）+ Activity 走 HTTP | 高 | 多 worker 时必须 |

第一版用 A。如果标注 server 支持 HTTP/SSE transport 而不是 stdio，直接选 C 跳过这个坑（HTTP MCP 是无状态的，最适合 Activity）。见 [05-open-questions.md](05-open-questions.md)。

## Activity heartbeat + 幂等

MCP 调用超过几秒（标注操作很容易超），Temporal 会判 Activity timeout 然后重试 → MCP server 端可能产生重复任务。两个对策同时上。

下面是 `@activity.defn(name="annotation")` 函数的核心逻辑（由 `executionplane/activities/annotation/adapter.py` 把 `AnnotationActivity(BaseActivity)` 实例适配而来，见 04 第 2 步；这里只展示 activity 函数体）：

```python
@activity.defn(name="annotation")
async def annotation(input: ActivityInput) -> dict:
    params = input.params or {}   # template 没填 params 时兜底空 dict
    async with mcp_session() as s:
        task = asyncio.create_task(s.call_tool("annotate", params))
        while not task.done():
            activity.heartbeat()              # 每秒心跳
            await asyncio.sleep(1)
        try:
            mcp_result = task.result()
        except Exception as e:
            # MCP 业务失败（非超时）→ 转成 ActivityOutput(ERROR)，不抛出避免 Temporal 无谓重试
            # 真正需要重试的（超时/网络）会以 ApplicationError 形式被 task.result() 抛出，
            # 那种走 retry_policy；这里只兜业务失败
            return _to_error_output(input, e)
        return _to_activity_output(mcp_result)
```

区分两类失败：
- **业务失败**（MCP 返回 status=failed / 标注 server 拒绝）：转 ERROR output，不重试
- **基础设施失败**（网络/超时）：让异常冒泡，Temporal 按 `retry_policy` 重试

并且 MCP server 那边的工具必须幂等或带 `idempotency_key`（用 `activity.info().workflow_id` 当 key）。需向标注系统设计师确认支持。

## 测试金字塔

| 层 | 工具 | 跑什么 |
|---|---|---|
| Unit | pytest + asyncio | Activity 单跑，MCP 全 mock |
| Integration | `temporalio.testing.WorkflowEnvironment.start_local()` | 进程内拉起真 Temporal，验证 Workflow + Activity 串起来对。L2 真用的关键证据 |
| E2E | docker-compose Temporal + 真 MCP server | 跑 1-2 个 happy path |

第一版"L2 必须用到"在 integration 层就达成 — `start_local()` 是真 Temporal SDK 在跑，不是 mock，启动 ~2 秒。