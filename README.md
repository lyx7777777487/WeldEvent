# WeldEvent

**工业焊缝质检智能决策引擎** — 基于 LLM ReAct 的三层 Agent 架构

> *"LLM decides what to do; the architecture decides what cannot be done."*

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com/)
[![Temporal](https://img.shields.io/badge/Temporal-1.27+-purple.svg)](https://temporal.io/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

---

## 架构全景

```
┌─────────────────────────────────────────────────────────────────┐
│  L1 · Cognitive Plane  认知平面                                  │
│  ┌──────────┐    ┌────────┐    ┌───────────────┐                │
│  │ chat.html │───►│ Chat   │───►│ ReActEngine   │───► 14 tools  │
│  │ (Web UI)  │    │ API    │    │ 3-tier LLM    │    + 10 MCP   │
│  │ WS + SSE  │    │ 11端点 │    │ ApprovalGate  │    (Label     │
│  └──────────┘    └────────┘    │ Stream 输出   │     Studio)   │
│                                └───────────────┘                │
│  技术栈: FastAPI · DeepSeek-Chat · Volc Doubao Vision · SSE/WS  │
└─────────────────────────────┬───────────────────────────────────┘
                              │ WorkflowSpec (DAG)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  L2 · Control Plane   控制平面                                    │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  RunWorkflowSpec (DAG Runner)                               │ │
│  │  ├── topological_sort(nodes)    拓扑排序                     │ │
│  │  ├── execute_node → L3 Activity                              │ │
│  │  ├── HumanGate (双重校验)       审批门禁                     │ │
│  │  └── RetryPolicy(max=3, 2s)     自动重试                     │ │
│  └─────────────────────────────────────────────────────────────┘ │
│  技术栈: Temporal (gRPC) · workflow-as-code                      │
└─────────────────────────────┬───────────────────────────────────┘
                              │ execute_node
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  L3 · Execution Plane  执行平面                                   │
│  ┌──────────┐   ┌──────┐   ┌──────┐   ┌──────────────┐         │
│  │ Activity │   │ IQA  │   │ PPA  │   │  Annotation  │         │
│  │  Pool    │──►│ 品质 │──►│ 预处 │──►│  Label Studio│         │
│  │ (9 cap)  │   │ 检控 │   │ 理   │   │  MCP 10 工具 │         │
│  └──────────┘   └──────┘   └──────┘   └──────────────┘         │
│  WeldMap 黑板 (CAS + Event Sourcing)                             │
│  技术栈: OpenCV · NumPy · SciPy · MCP · Label Studio             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 核心特性

### L1 — 认知平面

| 特性 | 说明 |
|------|------|
| **3-Tier LLM 回退** | FC → DSML → Keyword，确保 LLM 不可用时系统仍可用 |
| **ApprovalGate** | 6 个写入类工具强制弹窗审批，`await event.wait()` 架构层阻塞 |
| **Context Compaction** | 4 种策略 (SELF_COMPACT_SLIDING / TRUNCATE / HYBRID / NONE)，长对话自动压缩 |
| **Session Notes** | 19 工具自定义 note 格式，每轮 ReAct 自动注入当前状态 |
| **Skill 系统** | 多 Skill 注册 + 锁定 + 切换机制，按意图路由 |
| **双路径双模式** | HTTP /stream (SSE) + WS /ws (AgentLoop)，`run()` + `run_stream()` 双模式 |
| **三层 Guardrails** | SafetyHook + PolicyHook + AfterToolHook + OutputGuardrail |
| **ToolFailureReflector** | schema 校验失败自动修复，design_workflow 专用策略 |
| **LLMResponseCache** | 同 session 同 prompt 缓存，避免重复 LLM 调用 |
| **WorkflowObserver** | 长路径观察者，监听 workflow 事件推送通知 |

### L2 — 控制平面

| 特性 | 说明 |
|------|------|
| **DAG Runner** | 拓扑排序节点执行，支持 depends_on 依赖声明 |
| **HumanGate 双重校验** | L1 ApprovalGate (工具层) + L2 HumanGate (执行层) |
| **自动重试** | RetryPolicy(max_attempts=3, backoff=2.0s) |
| **WorkflowEventBus** | 实时事件广播，注入 L1 system prompt |

### L3 — 执行平面

| 特性 | 说明 |
|------|------|
| **IQA 图像质量评估** | 分辨率/曝光/对焦/完整性四维检查 + MLLM 深度视觉 |
| **PPA 自适应预处理** | 亮度/对比度/去噪/锐化/去反光 |
| **Label Studio 标注** | MCP 协议集成 10 个标注工具 (list_datasets/get_dataset/create_job/upload_images/create_task/trigger_ai/assign_task/list_jobs/get_job/list_tasks) |
| **WeldMap 黑板** | 6 域 CAS (Compare-And-Swap) + Event Sourcing |

---

## 快速开始

### 前置条件

- Python 3.12+
- Temporal Server (localhost:7233) — 可选，仅 L2 需要
- Label Studio 实例 — 可选，仅标注功能需要

### 安装

```bash
git clone https://github.com/lyx7777777487/WeldEvent.git
cd WeldEvent

# 安装 L1 认知平面
pip install -e cognitiveplane/

# 安装 L2 控制平面 (含 Temporal)
pip install -e cognitiveplane/[temporal]
pip install -r controlplane/requirements.txt

# 安装 L3 执行平面 (含 OpenCV 可选)
pip install -e executionplane/[cv,vision]
```

### 配置

```bash
# 创建 .env 文件配置 LLM
cat > .env << 'EOF'
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
VISION_API_KEY=your-vision-api-key
VISION_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
VISION_MODEL=doubao-vision-pro-32k
EOF
```

### 启动

```bash
# 启动 L1 认知平面 (FastAPI)
python cognitiveplane/app.py
# 默认 http://localhost:8000
# API 文档 http://localhost:8000/docs
# 聊天界面 http://localhost:8000/chat.html

# 启动 L2 控制平面 (Temporal Worker)
python -m controlplane worker
```

### 运行测试

```bash
pytest cognitiveplane/tests/ controlplane/tests/ executionplane/tests/ -q
```

---

## 项目结构

```
WeldEvent/
├── cognitiveplane/            # L1 认知平面 — LLM ReAct 引擎
│   ├── app.py                 # FastAPI 入口 (240行)
│   ├── bootstrap/             # 装配层 (LLM 初始化 / 依赖注入 / 种子数据)
│   │   ├── llm_setup.py       # LLM 配置探测
│   │   ├── dependencies.py    # 构建依赖图
│   │   └── seed_knowledge.py  # 6 类种子知识
│   ├── control/
│   │   ├── react.py           # 公共 shim (78行)
│   │   ├── agent_loop.py      # AgentLoop 双 task (WebSocket)
│   │   ├── engine/            # ReActEngine 核心拆解
│   │   │   ├── react.py       # 核心循环 + 3-tier 回退 (1294行)
│   │   │   ├── approval.py    # ApprovalGate 审批
│   │   │   ├── session_notes.py # Session Notes (19工具)
│   │   │   ├── system_prompt.py # 四源世界观构建
│   │   │   └── tool_execution.py # 工具执行 + 钩子链
│   │   ├── registry/          # 工具/MCP 注册表
│   │   │   ├── tool_registry.py
│   │   │   ├── mcp_registry.py
│   │   │   ├── mcp_server.py
│   │   │   └── tool_failure_reflector.py
│   │   ├── tools/             # 14 个 L1 工具
│   │   ├── skills/            # Skill 注册 + 路由
│   │   └── ports.py           # 6 Port ABC (未来扩展点)
│   ├── governance/            # 治理层
│   │   ├── guardrails/        # 三层 Guardrails
│   │   ├── evaluation.py      # 在线评估
│   │   └── permissions.py     # 权限 (未来扩展)
│   ├── interaction/           # 交互层
│   │   ├── api/               # Chat API (11端点)
│   │   │   ├── chat.py        # Router 装配 (293行)
│   │   │   ├── chat_handlers.py
│   │   │   ├── stream_handlers.py
│   │   │   ├── approval_handlers.py
│   │   │   ├── workflow_handlers.py
│   │   │   ├── session_handlers.py
│   │   │   └── _helpers.py    # 共享函数
│   │   ├── image_store.py     # 图片存储
│   │   └── notifications/     # 通知系统
│   ├── memory/                # 分层记忆 (Phase 2e/2g 扩展)
│   ├── knowledge/             # 知识服务层 (Phase 5)
│   ├── adapters/              # 生产部署 stub (Phase 5+)
│   └── shared/                # 跨层共享 DTO / 枚举 / 类型
│
├── controlplane/              # L2 控制平面 — Temporal DAG Runner
│   ├── runtime/
│   │   ├── dag_runner_workflow.py # RunWorkflowSpec DAG 执行
│   │   └── worker.py              # Temporal Worker
│   ├── domain/
│   │   ├── workflow_spec.py   # WorkflowSpec DAG 模型
│   │   └── activity.py        # Activity 定义
│   └── adapter/
│       └── dag_activities.py  # execute_node 实现
│
├── executionplane/            # L3 执行平面 — 工业视觉 Activity
│   ├── interface/             # IQA 公共接口 (拆解为 6 文件)
│   │   ├── config.py          # 全局单例 + 标准注册
│   │   ├── async_api.py       # 异步接口
│   │   ├── sync_api.py        # 同步包装
│   │   ├── result.py          # IqaResult 数据结构
│   │   └── io.py              # 结果输出
│   ├── activities/            # Activity 实现
│   │   ├── iqa/               # 图像质量评估
│   │   ├── ppa/               # 自适应预处理
│   │   └── annotation/        # Label Studio 标注
│   ├── capabilities/          # 视觉能力
│   │   └── vision/            # 预处理算法
│   ├── config/                # 质检标准
│   └── pool.py                # ActivityPool 分发
│
├── shared/                    # 跨层共享层
│   ├── mcp/                   # MCP 协议实现
│   └── contracts/             # 跨层契约 (ActivityOutput 等)
│
├── docs/
│   ├── CODEBASE_ANALYSIS.md   # 代码盘点 — 算法与结构
│   └── superpowers/           # 设计文档 specs
│
├── ARCHITECTURE.md            # 三层架构流程图
├── AGENT_COMPARISON_REPORT.md # trae-agent / Codex / Claude Code 对比报告
└── WeldEvent_Briefing.md      # CTO 技术汇报
```

---

## 设计哲学

WeldEvent 不是通用 Agent 框架，而是**工业场景 specialization**。核心理念：

> 通过大量"架构替 LLM 做看不见的事"的工程化设计，补强通用 ReAct 的脆弱点。

**固定（架构决定）：** 可用工具集、安全边界（什么需要审批）、记忆存取方式。

**动态（LLM 决定）：** 调用哪个工具、何时调用、如何分析推理、何时停止。

### 独有工程优势

| 优势 | 说明 |
|------|------|
| **架构层强制 Approval** | `await event.wait()` 阻塞，LLM 无法绕过 |
| **L2 控制平面** | Temporal DAG + HumanGate 双重校验，工业级可靠性 |
| **WeldMap 黑板** | CAS + Event Sourcing，跨 Activity 状态共享 |
| **DSML 回退** | LLM 不可用时仍可工作 |
| **三层 Guardrails** | 调用前/后/输出三方拦截 |
| **Session Notes 19 工具** | 每轮自动注入结构化状态，LLM 上下文不丢失 |
| **SAME_TOOL_LIMIT=3** | 防止 LLM 循环调用同一工具 |
| **双路径统一治理** | /stream 和 /ws 共享 hooks/guardrails/session 持久化 |

---

## 技术栈

| 平面 | 核心依赖 |
|------|---------|
| L1 | FastAPI, uvicorn, Pydantic v2, DeepSeek-Chat, Volc Doubao Vision, SSE, WebSocket |
| L2 | temporalio ≥ 1.27, gRPC |
| L3 | NumPy, SciPy, Pillow, OpenCV (可选), httpx, MCP |

---

## 文档

- [ARCHITECTURE.md](ARCHITECTURE.md) — 三层架构流程图
- [docs/CODEBASE_ANALYSIS.md](docs/CODEBASE_ANALYSIS.md) — 代码盘点：算法与结构先进性
- [AGENT_COMPARISON_REPORT.md](AGENT_COMPARISON_REPORT.md) — trae-agent / Codex / Claude Code 对比报告
- [WeldEvent_Briefing.md](WeldEvent_Briefing.md) — CTO 技术汇报

---

## License

MIT