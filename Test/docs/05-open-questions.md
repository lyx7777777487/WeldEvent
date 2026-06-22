# 05 — 开放问题

需在动手前与标注系统设计师 / 自己确认。

## Q1：MCP server transport 是什么？

**为什么重要**：决定 `annotate_tool.py` 怎么建会话，决定 worker 设计。

| Transport | 含义 | 对 worker 的影响 |
|---|---|---|
| stdio | 启子进程，stdin/stdout 通信 | 有状态 session，每次 Activity 必须 open/close；多 worker 时每个 worker 各启一个 |
| HTTP / SSE | URL 通信 | 无状态，最适合 Activity；多 worker 共享一个 server 实例 |

**推荐**：HTTP/SSE。如果标注 server 是 stdio，要么第一版接受"每次 open/close"开销，要么写一个 stdio→HTTP gateway。

**待标注系统设计师确认**：
- transport 类型
- stdio 启动命令 + 参数，或 HTTP URL
- `list_tools()` 的实际输出（name / description / inputSchema）

## Q2：MCP server 是否支持幂等 key？

**为什么重要**：Temporal Activity timeout 会重试，重试时如果 MCP server 重复执行标注任务，会产生重复数据。

**待确认**：
- `annotate` 工具是否接受 `idempotency_key` 参数？
- 如果不支持，第一版用 `activity.info().workflow_id + activity.info().activity_id` 当 key 透传，server 端忽略也行（至少审计有据）

**注意**：业务失败已被 Activity 转成 ERROR output 不抛异常（见 [02-architecture.md Activity heartbeat](02-architecture.md)），不会触发 Temporal 重试。幂等 key 只针对**基础设施失败**（网络/超时）触发的重试 — 这类重试合理，但 server 端若不幂等会产生重复任务。如果 server 完全不支持幂等且重复代价高，把 `retry_policy.max_attempts` 设 1，靠 workflow 层（人工 gate / 重新启动）兜底。

## Q3：DeepSeek 怎么接？

**选项 A**：直接用 `openai` SDK 兼容接口打 `https://api.deepseek.com/v1`，L1 内部独立维护。

**选项 B**：复用 `cognitiveplane/interaction/llm/openai_provider.py`。

**推荐 A**。第一版 L1 必须与 cognitiveplane 物理隔离，否则会忍不住拖 cognitiveplane 的东西进来。代码量很小（≤ 30 行），不值得复用。

**待确认**：
- API key 从环境变量 `DEEPSEEK_API_KEY` 读取
- model 名（`deepseek-chat` / `deepseek-reasoner`）
- 是否支持 function calling（DeepSeek-chat 支持）

**function calling 工具 schema**（第一版只 1 个工具）：

```json
{
  "type": "function",
  "function": {
    "name": "emit_workflow_template",
    "description": "提交你设计的 workflow template。schema 见 system prompt，不要改字段名。",
    "parameters": {
      "type": "object",
      "properties": {
        "id": {"type": "string"},
        "version": {"type": "string"},
        "entry_point": {"type": "string"},
        "control_points": {"type": "array"},
        "transitions": {"type": "array"}
      },
      "required": ["id", "version", "entry_point", "control_points"]
    }
  }
}
```

DeepSeek 兼容 OpenAI tools 字段，直接传 `tools=[...]` + `tool_choice={"type": "function", "function": {"name": "emit_workflow_template"}}` 强制调用。

## Q4：TemplateRepository 怎么让 worker 和 L1 共享？

**问题**：worker 是独立进程，L1 也是独立进程。in-memory 实例不共享。

**选项 A**：L1 和 worker 跑在同一个进程里（asyncio 协同），共享 repo 实例。简单但耦合。

**选项 B**：用文件 repo — template 写盘成 `templates/{id}_{version}.json`，repo 从盘读。worker 启动时 `create_load_template_activity(repository=FileTemplateRepository("templates/"))`。

**选项 C**：用 PG / Redis。第一版过度设计。

**推荐 B**。文件 repo 5 行代码，跨进程天然共享，且与 `template_loader.py` 现有 `repository` 钩子无缝衔接。L1 写盘 → worker 读盘 → 启动 workflow。

**实现位置**：`shared/template_repository/file_repository.py`（中性目录，worker 和 L1 都 import，避免任一层反向依赖另一层）。`shared/` 与 `executionplane/` / `controlplane/` / `l1_agent/` 平级。

## Q5：`AnnotationActivity` 的 params / data schema 怎么定？

`ActivityInput.params` 和 `ActivityOutput.data` 都是 `dict | None`，无 schema。每个 Activity 必须自己定义内部 contract。

**建议**：`executionplane/activities/annotation/activity.py` 文件头用 dataclass 写：

```python
@dataclass
class AnnotationParams:
    """L1 通过 template 塞进 ActivityInput.params 的字段"""
    image_path: str
    label_schema: list[str] | None = None    # 可选，标注 server 用的标签集

@dataclass
class AnnotationResult:
    """ActivityOutput.data 里的字段"""
    task_id: str
    labels: list[dict]                        # [{"label": "气孔", "bbox": [...], "confidence": 0.9}]
    status: str                               # "completed" / "failed"
    raw: dict | None = None                   # MCP 原始返回，调试用
```

不一定要运行时校验（第一版可选），但作为文件级 contract 必须存在。L1 写 template 时知道 params 塞什么，L2 拿到 data 知道怎么用。

## Q6：dataset_summary 怎么生成？

`WorkflowDesigner.design()` 需要 `dataset_summary` 作为 LLM 上下文。建议：

```python
def summarize_dataset(path: str) -> dict:
    files = list(Path(path).glob("*.[pj][np]g")) + list(Path(path).glob("*.tif*"))
    return {
        "count": len(files),
        "format": files[0].suffix if files else "unknown",
        "sample_paths": [str(f) for f in files[:3]],
        "size_hint": "small" if len(files) < 10 else "medium" if len(files) < 100 else "large",
    }
```

不让 LLM 看图本身（那是 cognitiveplane 视觉模式的事），只看元数据。

## Q7：第一版要不要做 preprocess activity？

需求里说"图像预处理和标注"。两个 activity 都做会让 L1 复杂度翻倍。

**推荐**：第一版只做 `annotation`。preprocess 留到第二轮。理由：
- 验证"agent 设计 workflow → L2 跑 → activity 调 MCP"这条链路，只需要一个 activity
- 加 preprocess 会引入"preprocess 输出 → annotation 输入"的数据传递问题，是另一个独立坑
- 单 activity 也能验证 TemplateWorkflow 的 transition / 多 CP 机制（用 human gate 替代第二个 activity）

如果一定要两个 activity，preprocess 可以做成"空 activity"（不调 MCP，只 echo 路径），先把链路验证了再填业务。