# 03 — L1 极简 Agent 设计

## 物理结构

```
l1_agent/                              # 与 executionplane/、controlplane/ 平级
  __init__.py
  main.py                              # CLI entry, ≤ 30 行
  config.py                            # DeepSeek key、Temporal host、MCP server path
  design/
    workflow_designer.py               # design 类 LLM 调用: 产出 WorkflowTemplate dict, ≤ 60 行
    template_validator.py              # 三重验证, ≤ 80 行
    human_reviewer.py                  # 渲染 template 给人看 + 循环接收反馈, ≤ 60 行
  execution/
    workflow_runner.py                 # Temporal Client + 等 result, ≤ 60 行
    # 注：FileTemplateRepository 不在这里，放 shared/template_repository/（worker 和 L1 共享，见 04 第 3 步）
  tests/
    test_designer.py
    test_validator.py
    test_reviewer.py
    test_runner.py
    test_e2e.py
```

> **注**：MCP 调用代码不在 l1_agent 下。`annotate_tool` 放在仓库根的中性目录 `shared/mcp_tools/`，L1 和 L3 都向它 import，避免下层反向依赖上层。见 [02-architecture.md 接口 2](02-architecture.md)。

整个 l1_agent ≤ 400 行，**有意为之**。任何超过的代码都该问一句"是不是把 cognitiveplane 的活儿往里塞了"。

## 四个最小组件

| 组件 | 职责 | 是否需要 LLM | 第一版怎么做 |
|---|---|---|---|
| WorkflowDesigner | requirement + dataset → WorkflowTemplate dict | 是 | DeepSeek function calling，工具只有 1 个：`emit_workflow_template` |
| HumanReviewer | 渲染 template 给人看 + 循环接收反馈 | 否 | CLI 打印 + input() |
| WorkflowRunner | 启动 Temporal workflow + 拿 handle + 等 result | 否 | 10 行 SDK 调用 |
| ResultRenderer | workflow 完成后的 dict 结果 → 给 user 的话 | 是 | summarize 类 LLM 调用（流程固定 1 次），prompt 就一行 |

整个 L1 = 这四个东西串成一根直线，零循环零分支（设计期的 review 循环除外）。**这是"线性"在 L1 的物理体现**。

## 关键设计决策（一次性定下来）

| 决策 | 推荐 | 理由 |
|---|---|---|
| L1 形态 | CLI 单次执行，不是常驻服务 | 测试阶段，agent 跑完即退；常驻进程会引入 session 状态、并发问题 |
| LLM 调用次数 | 2 类调用（design / summarize），不是 2 次 | design 类按 review 轮数 ×N（最多 MAX_DESIGN_ROUNDS 轮）；summarize 类固定 1 次。"线性"指运行期无 LLM 循环，不是 design 期只调一次 |
| Template 怎么找 | L1 内硬编码 activity 白名单 `["annotation"]` | 第一版只做一个 activity（见 05-Q7）；LLM 自由命名 activity 会幻觉出 `clean_image` 等 |
| Gate 策略 | CLI 阻塞 + input() | LLM 决策 gate 是 cognitiveplane 的事；第一版老老实实让人按回车 |
| 失败时 | 打印 stack trace 退出，不重试 | 重试是 L2 的事（retry_policy 已在 template 里） |
| 数据集多张图 | L1 在 workflow 外面循环：每张图启动一个 workflow | 简单、隔离好；`TransitionType` 没有 FOR_EACH |

## 设计期循环 vs 运行期线性

"不符合则改"是 LLM ↔ 人的多轮对话，发生在 **workflow 启动前**。workflow 一旦启动就是线性的，不再回头改 template。两者在代码里物理隔离：

```
l1_agent/
  design/          # 设计期：可以 loop、可以多轮 LLM
  execution/       # 运行期：线性，无 loop
```

## main.py 骨架

```python
async def main(requirement: str, dataset_path: str):
    dataset_summary = summarize_dataset(dataset_path)

    # 1. 设计期：LLM 生成 → 验证 → 人审 → 循环
    template = await design_phase(requirement, dataset_summary)

    # 2. 运行期：启动 workflow → 等结果
    handle = await workflow_runner.start(template)
    result = await handle.result()

    # 3. 给 user 讲结果（summarize 类 LLM 调用，整个流程固定 1 次）
    return await result_renderer.render(requirement, result)

if __name__ == "__main__":
    print(asyncio.run(main(sys.argv[1], sys.argv[2])))
```

## design_phase 骨架

两个独立计数器，不要混：
- `schema_retries`：连续 schema 验证失败计数，上限 3，超了直接抛错（不让 LLM 无限重试）
- `review_rounds`：人审驳回轮数，上限 MAX_DESIGN_ROUNDS=5，超了抛 `DesignTimeoutError`

```python
async def design_phase(requirement, dataset_summary):
    history = []
    schema_retries = 0
    for review_round in range(MAX_DESIGN_ROUNDS):   # 5
        tpl = await workflow_designer.design(requirement, dataset_summary, history)

        validation = template_validator.validate(tpl)
        if not validation.ok:
            schema_retries += 1
            if schema_retries > 3:
                raise SchemaRetryExhausted(f"LLM 连续 3 次 schema 失败: {validation.error}")
            history.append({"role": "system", "content": f"schema 错误（第 {schema_retries} 次）: {validation.error}"})
            continue
        schema_retries = 0   # 一次成功就清零

        print(human_reviewer.render(tpl))
        feedback = await asyncio.to_thread(input, "满意? (y / 改进意见): ")   # 不阻塞 event loop
        if feedback.strip().lower() == "y":
            return tpl
        history.append({"role": "user", "content": feedback})

    raise DesignTimeoutError("超过最大人审轮数仍未达成共识")
```

## WorkflowDesigner 的 prompt 战略

不要让 LLM 凭空写 JSON。给填空式骨架：

```
你可用的 activity:
  - annotation(image_path) → 调外部标注 MCP，输出标注结果

请按以下 JSON 结构填写 workflow template（不要改 schema，只填值）:
{
  "id": "<给一个有意义的名字>",
  "version": "1",
  "entry_point": "<必须是下面 control_points 里某个 id>",
  "control_points": [
    {"id": "...", "name": "...", "activity_binding": {"activity_name": "annotation"}, "execution_policy": {...默认}}
  ],
  "transitions": []
}

用户需求: {requirement}
数据集: {dataset_summary}

参考例子（焊缝宏观标注的最小 template）:
{few_shot_example}
```

第一版只有一个 activity，transitions 通常为空（单 CP）。单 CP 跑通就足以验证 L1→L2→L3 链路；transition / 多 CP / human gate 机制留到下一轮（见 05-Q7）。

## 三重验证

```
LLM 输出 JSON
  ↓
1. pydantic / dataclass schema 验证（对照 WorkflowTemplate）
  ↓
2. 语义验证：
   - activity_name ∈ ["annotation"]（第一版白名单；Q7 已决定只做 annotation）
   - entry_point 存在于 control_points
   - transitions 引用的 cp_id 都存在
   - 没有孤岛 CP（除非是 entry 或终态）
  ↓
3. 给 user 看，等审批
```

第 1、2 步机械验证，第 3 步交给人。**连续 schema 失败上限 3 次**（`schema_retries`，见 design_phase 骨架），超了抛 `SchemaRetryExhausted` 给 user 重新描述需求；**人审驳回上限 MAX_DESIGN_ROUNDS=5 轮**，超了抛 `DesignTimeoutError`。两个计数器独立。

## 给人看的 template 渲染

不要把 JSON 糊脸给 user，画成树。第一版单 CP 的例子：

```
工作流: weld_macro_annotation_v1
  [1] annotation  → 完成
```

未来加 preprocess 后变成：

```
工作流: weld_macro_annotation_v1
  [1] preprocess  → OK 时转 [2]，NG 时终止
  [2] annotation  → 完成
```

人看这种比 JSON 易懂十倍。

## 主动砍掉（防 scope creep）

- ❌ 不引入任何 cognitiveplane 的 import
- ❌ 不做对话历史 / multi-turn
- ❌ 不做工具选择（LLM 只暴露 1 个工具 `emit_workflow_template`）
- ❌ 不做 gate 的 LLM 决策
- ❌ 不做重试（L2 的事）
- ❌ 不做并发（一次只跑一个 workflow）
- ❌ 不做模板热加载（template JSON 跟代码一起 ship）
- ❌ 不做 Memory（template 入库只到 FileTemplateRepository，不入 Memory）
- ❌ 不做 NATS / WeldMap 事件回写
- ❌ 不做 Signal 等待人工标注（MCP 同步返回）
- ❌ 不做 ReAct 多轮（design → confirm 两步直线）
- ❌ 不做 ToolPolicy / SafetyHook
- ❌ 不做 LLMTier / Persona / AgentRole

每条砍掉的东西，都对应 cognitiveplane 里一个复杂功能。把它们留给后面的真 L1，这次只验证骨架。