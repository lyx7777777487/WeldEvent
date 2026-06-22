# Test — L1 线性 Agent over IQA 标注系统

自包含测试项目：基于现有标注系统 MCP 接口、跑通 L1 agent → L2 Temporal → L3 AnnotationActivity → MCP。

不引入 cognitiveplane。第一版 L1 与 L2 之间的契约 = 一个 `WorkflowTemplate` dict。

## 快速开始

```bash
cd Test
pip install -e ".[test]"

# 跑测试（mock LLM + mock MCP + in-process Temporal，无需外部依赖）
pytest

# 接真 MCP server（先确认 docs/05-open-questions.md Q1）
export MCP_TRANSPORT=http
export MCP_SERVER_URL=http://localhost:8000/mcp
export DEEPSEEK_API_KEY=sk-...
python -m controlplane.worker &          # 启 worker
python -m l1_agent.main "标注焊缝图" /path/to/dataset
```

## 目录结构

```
Test/
  README.md
  pyproject.toml                  — 包根配置，import 路径 from controlplane / from l1_agent
  conftest.py                     — 把 src/ 加进 sys.path
  docs/
    01-context.md                 — 项目现状与误判修正
    02-architecture.md            — 三层职责切割、接口契约、MCP × Temporal 关键坑
    03-l1-agent-design.md         — L1 极简 agent 物理结构、design_phase 骨架、prompt
    04-implementation-plan.md     — 落地顺序 + 北极星指标
    05-open-questions.md          — 7 个待确认问题
    06-existing-infrastructure.md — L2/L3 已有代码清单（controlplane/executionplane 拷过来的）
  src/
    controlplane/                 — 从原 controlplane 拷（L2 必需子集）
      domain/                     — activity / template / execution / human_gate / policy
      runtime/                    — template_workflow / control_point / human_gate / transition
      adapter/                    — template_loader / mocks / base
      worker.py                   — 已改：注入 FileTemplateRepository + 注册 annotation_activity
      config.py
    executionplane/
      activities/
        base.py                   — BaseActivity ABC（第一版未用，留作接法 B 预留）
        annotation/
          activity.py             — @activity.defn(name="annotation") 裸函数（接法 A）
    shared/                       — 中性层，L1 和 L3 都 import，无反向依赖
      mcp_tools/
        annotate_tool.py          — annotate() 入口
        mcp_session.py            — 会话管理（stdio/http/sse/mock）
        mock_mcp_server.py        — 测试用 in-process 假 server
      template_repository/
        file_repository.py        — 跨进程共享的 template 文件仓库
    l1_agent/
      config.py                   — 环境变量配置
      dataset.py                  — 数据集元数据摘要
      design_phase.py             — 设计期主循环（schema_retries + review_rounds 两个计数器）
      result_renderer.py          — summarize 类 LLM 调用（固定 1 次）
      main.py                     — CLI 入口
      design/
        workflow_designer.py      — DeepSeek function calling 产出 template
        template_validator.py     — 三重验证（结构 + 语义）
        human_reviewer.py         — 渲染 + 人审 input
      execution/
        workflow_runner.py        — save template + start workflow + 等结果
  tests/
    test_validator.py             — 非法 template 被拒
    test_annotate_tool.py         — mock MCP 下工具入参出参
    test_designer.py              — mock LLM 产出合法 template
    test_annotation_activity.py   — Activity 单跑（happy / 业务失败 / 基础设施失败）
    test_e2e.py                   — 北极星：mock LLM + mock MCP + start_local() 端到端
```

## 核心结论（TL;DR）

1. **L2 不需要新 Workflow**。`controlplane/runtime/template_workflow.py` 的 `TemplateWorkflow` 是模板驱动解释器，agent 的产出 = 一个符合 `WorkflowTemplate` schema 的 dict。
2. **L1 的全部职责**：根据需求 + 数据集生成 `WorkflowTemplate` → 人工 review → 启动 Temporal workflow → 等结果。
3. **L3 新增 `annotation_activity`**：裸函数（接法 A，跟 `mocks.py` 风格一致），调 `shared.mcp_tools.annotate_tool`，含 heartbeat + 业务/基础设施失败分流。
4. **L1 与 L3 共享同一份 MCP 调用代码**（`shared/mcp_tools/annotate_tool.py`，DRY，§6.7 接口 2 硬约束）。
5. **第一版主动砍掉**：Memory、NATS、WeldMap 事件、Hook、Signal、ReAct 多轮、cognitiveplane 全部 import。

## 文档索引

- [01-context.md](docs/01-context.md) — 项目现状与误判修正
- [02-architecture.md](docs/02-architecture.md) — 三层架构与接口契约
- [03-l1-agent-design.md](docs/03-l1-agent-design.md) — L1 极简 agent 设计
- [04-implementation-plan.md](docs/04-implementation-plan.md) — 落地顺序与北极星指标
- [05-open-questions.md](docs/05-open-questions.md) — 开放问题
- [06-existing-infrastructure.md](docs/06-existing-infrastructure.md) — L2/L3 已有代码清单