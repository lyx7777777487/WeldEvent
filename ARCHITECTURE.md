# WeldEvent 三层架构流程图

> L1 Cognitive Plane · L2 Control Plane · L3 Execution Plane
> 算法严谨对照源码梳理，黑白文本图，无颜色依赖。

---

## 一、全局视图（三层 + Bridge）

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ L1 · COGNITIVE PLANE（认知平面）                                                     │
│ LLM ReAct 推理 + 工具编排 + 用户交互                                                 │
│                                                                                     │
│  用户/前端 ──► Chat API ──► AgentLoop ──► ReActEngine ──► ToolRegistry ──► MCP 工具  │
│  (chat.html)   (双路径)    (双 task)     (核心循环)      (14 L1 工具)    (10 标注)    │
└───────────────────────────────────┬─────────────────────────────────────────────────┘
                                    │
                          Bridge L1↔L2
                          WorkflowSpec ↓
                          WorkflowEventBus ↑
                          NotificationStore ↑
                                    │
┌───────────────────────────────────┴─────────────────────────────────────────────────┐
│ L2 · CONTROL PLANE（控制平面）                                                       │
│ Temporal workflow DAG runner + HumanGate 双重校验                                    │
│                                                                                     │
│  WorkflowLauncher ──► RunWorkflowSpec ──► HumanGate ──► Worker ──► WorkflowEventBus  │
│  (LaunchPort)        (拓扑排序)        (双重校验)     (activity)   (→L1 注入 prompt)  │
└───────────────────────────────────┬─────────────────────────────────────────────────┘
                                    │
                          Bridge L2↔L3
                          NodeExecute (capability) ↓
                          ActivityOutput (result) ↑
                          WeldMap 黑板 (CAS + Event Sourcing) ↔
                                    │
┌───────────────────────────────────┴─────────────────────────────────────────────────┐
│ L3 · EXECUTION PLANE（执行平面）                                                     │
│ 工业质检 Activity 执行 + WeldMap 黑板 + Label Studio MCP                             │
│                                                                                     │
│  ActivityPool ──► IQA ──► PPA ──► Annotation ──► WeldMap 黑板                        │
│  (9 capability)  (质量)  (预处理) (标注 10 工具)   (6 域 CAS)                        │
│                  ↓       ↓                            ↑                              │
│                  Label Studio MCP (远程 HTTP, 10 工具)┘                              │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 二、L1 认知平面 — 算法与流程

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ L1 · COGNITIVE PLANE                                                                 │
├──────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                      │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐   │
│  │ 用户/前端    │───►│ Chat API    │───►│ AgentLoop   │───►│ ReActEngine 核心    │   │
│  │ chat.html   │    │ chat.py     │    │ agent_loop  │    │ engine/react.py     │   │
│  │             │    │ (router装配)│    │ .py         │    │                     │   │
│  │ • WS /ws    │    │ +handlers/  │    │ • receive_  │    │ • 3-tier LLM        │   │
│  │ • HTTP      │    │  chat_      │    │   task      │    │   (FC/DSML/Keyword) │   │
│  │   /stream   │    │  handlers   │    │ • react_task│    │ • ApprovalGate      │   │
│  │ • 流式渲染   │    │ +handlers/  │    │   (仅 1 个) │    │   (6 工具阻塞)      │   │
│  │ • 中断按钮   │    │  stream_    │    │ • feedback  │    │ • Context Compaction│   │
│  │ • 反馈机制   │    │  handlers   │    │   queue     │    │   (4 策略)          │   │
│  │ • Approval  │    │ +handlers/  │    │ • interrupt │    │ • Plan-and-Execute  │   │
│  │   弹窗      │    │  approval/  │    │   (cancel)  │    │ • Skill 锁定+切换    │   │
│  │ • 通知弹窗   │    │  workflow/  │    │ • session   │    │ • Session Notes     │   │
│  │             │    │  session_   │    │   持久化     │    │   (19 工具)         │   │
│  │             │    │  handlers   │    │ (委托helper)│    │ • 失败反思           │   │
│  └─────────────┘    └─────────────┘    └─────────────┘    │ • 同轮工具上限=3    │   │
│                                                            │ • 三层知识 fallback │   │
│                                                            └──────────┬──────────┘   │
│                                                                       │              │
│                                                                       ▼              │
│                                                            ┌─────────────────────┐   │
│                                                            │ ToolRegistry        │   │
│                                                            │ (14 L1 工具)        │   │
│                                                            │                     │   │
│                                                            │ • analyze_image     │   │
│                                                            │ • upload_image_to_  │   │
│                                                            │   dataset (包装)    │   │
│                                                            │ • design/launch/    │   │
│                                                            │   control_workflow  │   │
│                                                            │ • request_          │   │
│                                                            │   confirmation      │   │
│                                                            │ • search_standards  │   │
│                                                            │ • search_cases      │   │
│                                                            │ • web_search        │   │
│                                                            │ • escalate          │   │
│                                                            │ • read_weldmap      │   │
│                                                            │ • explain_decision  │   │
│                                                            │ • archive_memory    │   │
│                                                            │                     │   │
│                                                            │ phase=3 过滤        │   │
│                                                            │ Draft7 schema 校验  │   │
│                                                            └──────────┬──────────┘   │
│                                                                       │              │
│                                          ┌────────────────────────────┼────────┐     │
│                                          │                            │        │     │
│                                          ▼                            ▼        │     │
│                            ┌─────────────────────┐      ┌─────────────────────┐  │     │
│                            │ MCP 标注工具 (10)    │      │ 横切组件             │  │     │
│                            │ Label Studio        │      │                     │  │     │
│                            │                     │      │ • ImageStore (双轨)  │  │     │
│                            │ • list_datasets     │      │ • NotificationStore  │  │     │
│                            │ • get_dataset       │      │   (5 类通知 pub/sub) │  │     │
│                            │ • create_job        │      │ • ApprovalStore      │  │     │
│                            │ • list_jobs         │      │ • EventLog (13 类)   │  │     │
│                            │ • get_job           │      │ • Memory 系统        │  │     │
│                            │ • create_task       │      │ • Langfuse 追踪      │  │     │
│                            │ • list_tasks        │      │ • ContextCompactor   │  │     │
│                            │ • upload_images     │      │ • SkillRegistry      │  │     │
│                            │ • assign_task       │      │ • ToolPolicy         │  │     │
│                            │ • trigger_ai        │      │ • Hooks (Safety/Pol) │  │     │
│                            │                     │      │ • LLMResponseCache   │  │     │
│                            │ ToolPolicy A/B/C    │      │   (LRU+TTL temp==0) │  │     │
│                            │ phase_override=3    │      │ • Guardrails (3 层)  │  │     │
│                            └─────────────────────┘      │   AfterTool+Output  │  │     │
│                                                         │ • Evaluator          │  │     │
│                                                         │   (LLM-Judge 4 维)  │  │     │
│                                                         │ • ToolFailureRefl    │  │     │
│                                                         │   (schema 反射)     │  │     │
│                                                         │ • WorkflowObserver   │  │     │
│                                                         │   (长路径观察者)     │  │     │
│                                                         │ • WorkflowEventBus   │  │     │
│                                                         │   (hooks+历史缓存)  │  │     │
│                                                         │                     │  │     │
│                                                         │ 进程内状态           │  │     │
│                                                         └─────────────────────┘  │     │
│                                                                                  │     │
│                                          ┌─────────────────────┐                  │     │
│                                          │ Bridge L1→L2        │                  │     │
│                                          │ cognitiveplane/     │                  │     │
│                                          │ bridge/             │                  │     │
│                                          │                     │                  │     │
│                                          │ • EventConnector    │                  │     │
│                                          │ • WorkflowLauncher  │                  │     │
│                                          │ • query_status      │                  │     │
│                                          │ • send_signal       │                  │     │
│                                          │ • send_human_gate_  │                  │     │
│                                          │   signal            │                  │     │
│                                          └─────────────────────┘                  │     │
│                                                                                   │     │
└───────────────────────────────────────────────────────────────────────────────────┘     │
                                                                                            │
```

### L1 核心算法清单

| 算法 | 实现要点 | 关键常量 |
|------|---------|---------|
| **3-tier LLM fallback** | Tier-1 Function Calling / Tier-2 Structured 意图分类 / Tier-3 关键词规则；启动时选定，非运行时回退 | — |
| **ApprovalGate 架构阻塞** | 6 个写入类工具（launch_workflow / create_job / create_task / trigger_ai / upload_images / assign_task）执行前 `await event.wait(timeout=300s)`，LLM 无法绕过 | `APPROVAL_TIMEOUT=300s` |
| **Context Compaction 4 策略** | SLIDING_WINDOW / FULL_SUMMARY / SELF_COMPACT_SLIDING（默认）/ SELF_COMPACT_ALL；fallback 链 expensive→cheap；MUST PRESERVE / CAN DROP 边界 | `max_tokens=16000` |
| **Plan-and-Execute** | `design_workflow` 成功后写 `session["current_plan"]`，下轮注入 system prompt；`launch_workflow` 成功后清理并解锁 skill | — |
| **Skill 选择 + 锁定** | 子串计数 match_score + session 锁定 + 工具驱动自动切换 + 3 级漂移检测（提示→强提示→解锁） | — |
| **Session Notes** | `_derive_note` 覆盖 19 工具：L1 工具 9 个（analyze_image/design_workflow/launch_workflow/read_weldmap/escalate/explain_decision/web_search/request_confirmation/upload_image_to_dataset）+ MCP 标注 10 个（list_datasets/get_dataset/list_jobs/get_job/list_tasks/create_job/create_task/upload_images/assign_task/trigger_ai）；查询类记结果摘要 + first_job_id/first_task_id；写入类记产出 ID（job_id/task_id/version_id）；request_confirmation 记 user_selection | FIFO 上限 8 条 |
| **同轮同名工具上限** | `SAME_TOOL_LIMIT=3`，超限静默 reject（不 yield 给前端），回填 LLM 提示走 web_search | `SAME_TOOL_LIMIT=3` |
| **三层知识获取 fallback** | search_standards（≤2 次）→ web_search → request_confirmation 弹窗；system prompt 约束 | — |
| **DSML fallback 解析** | LLM 未返回原生 tool_calls 但 content 含 `<｜｜DSML｜｜>` 时正则解析；stream 最终轮 `re.sub` 清理残留 | — |
| **失败反思** | `fail_counts[tool:args_fingerprint]` 连续 ≥2 次失败触发 verification gate，按工具+错误类型生成针对性提示 | — |
| **ToolFailureReflector（schema 反射）** | 独立模块 `tool_failure_reflector.py`，jsonschema ValidationError 时尝试自动修复：design_workflow 专用策略（6 类错误：missing objective/node_id、unknown capability、duplicate node_id、depends_on 模糊匹配 auto-fix、cycle）；generic 策略（missing query/reason 从 user_input 推断、wrong type、enum）；返回 ReflectionResult(severity=auto_fixed/actionable/fatal) | — |
| **LLMResponseCache** | `capability/response_cache.py`，LRU+TTL，仅缓存 temperature==0 的确定性请求；key=messages+model+temp+max_tokens+top_p+response_format+tools 的 SHA256；排除 caller/case_id；不缓存 tool_calls 响应与 stream；maxsize=256 | `maxsize=256` |
| **三层 Guardrails** | `governance/guardrails.py`：AfterToolHook（工具结果护栏，2 条焊接规则：电流>500A/预热<100°C REJECT）+ OutputGuardrail（LLM 输出护栏，3 条规则：绕过国标/跳过检测/伪造参数 REJECT）；默认 WARN 不阻断 | — |
| **LLM-as-Judge 评估** | `governance/evaluation.py`：4 维度评分（helpfulness/safety/tool_efficiency/grounding）+ `_repair_truncated_json` 修复；`eval_dataset.py` GOLDEN_SET 4 用例（正常咨询/危险建议/违规参数/国标充分）；LangfuseScoreRecorder 挂载 trace，无 Langfuse 时 NoOp 降级 | — |
| **WorkflowObserver** | `control/workflow_observer.py`：订阅 WorkflowEventBus publish hooks，仅 3 类 KEY_EVENTS（workflow_completed/failed/paused）推 NotificationStore；app.py lifespan 启动/停止 | — |
| **ID 类型区分** | 4 种 ID（dataset_id/version_id/job_id/task_id）严格区分，session notes 记录写入工具产出的 ID，避免 LLM 混用导致 404 | — |

**关键常量**：`max_iterations=25`、`SAME_TOOL_LIMIT=3`、`TOOL_TIMEOUT_SECONDS=180`、`APPROVAL_TIMEOUT_SECONDS=300`。

**system prompt 引导段（5 段）**：标注能力两条路径 / 三层知识获取 fallback / 主动提问何时弹窗 / 标注流程工具使用引导 / ID 类型严格区分。

**前端动态文案**：弹窗按钮按 tool_name 动态生成（8 工具映射：launch_workflow/create_job/upload_images/create_task/trigger_ai/assign_task/request_confirmation/escalate），各有独立 approve/reject/title/resolvedOk 文案。

**调试端点**：`GET /sessions` 列出所有 session；`GET /sessions/{id}/export` 导出完整对话历史。

**Chat API 端点（11 个）**：`POST /`（基础 chat）、`POST /upload`（图片上传）、`POST /stream`（SSE）、`WS /ws`（AgentLoop）、`POST /cancel`、`POST /workflow/events`、`GET /sessions`、`GET /sessions/{id}/export`、`GET /workflow/stream/{session_id}`、`POST /workflow/status`、`POST /approve`。

**shared/ 跨层共享层**：`shared/mcp/`（MCPClient ABC + StdioMCPClient + HTTPMCPClient + protocol 请求/响应/ToolDescriptor）+ `shared/labelstudio/`（auth login + config），供 L1 adapters/mcp 与 L3 mcp/ 共享传输层，避免 L1 依赖 L3 业务封装。

**技术债**：当前 L1 仅 ReActEngine 单路径，无子 agent 能力；节点级回溯不存在（无 rollback/replay）；L3 HCA Activity mock；feedback 信号链延迟到下一轮；ToolFailureReflector 仅 design_workflow 有专用策略；Guardrails 默认 WARN 不阻断；Evaluator 无 Langfuse 时 NoOp 降级。

**L1 目录结构（boundary-pinning 拆解后）**：
```
cognitiveplane/
├── app.py                    # FastAPI composition root (240行, 从989行瘦身)
├── bootstrap/                # 装配分离 (从 app.py 拆出)
│   ├── __init__.py          # re-export bootstrap_llm/build_dependencies/build_tool_registry_for_mcp
│   ├── llm_setup.py         # probe_chat/bootstrap_llm/build_llm_cache
│   ├── dependencies.py      # build_dependencies (调 build_seed_knowledge)
│   └── seed_knowledge.py    # build_seed_knowledge (6类种子数据收进函数)
├── control/
│   ├── react.py             # shim (78行, re-export ReActEngine 等公开符号)
│   ├── agent_loop.py        # AgentLoop 双 task
│   ├── engine/              # ReActEngine 拆解 (从 react.py 2728行拆出)
│   │   ├── __init__.py      # re-export 23 个公开符号
│   │   ├── react.py         # ReActEngine 核心类 + Tier fallback (1294行)
│   │   ├── approval.py      # ApprovalStore + ApprovalRequest + APPROVAL_REQUIRED_TOOLS
│   │   ├── session_notes.py # derive_note (19工具) + TOOL_TIMEOUT_SECONDS
│   │   ├── system_prompt.py # WorldviewBuilder + SystemPromptBuilder
│   │   └── tool_execution.py # ToolExecutor + ToolCallValidator + HookRunner
│   ├── registry/            # 工具/MCP 注册表 (从 control/ 重组)
│   │   ├── __init__.py      # re-export ToolRegistry/MCPRegistry/reflect_schema_failure
│   │   ├── tool_registry.py
│   │   ├── mcp_registry.py
│   │   ├── mcp_server.py
│   │   └── tool_failure_reflector.py
│   └── tools/               # 14 个 L1 工具实现
├── interaction/api/         # Chat API (从 chat.py 852行拆解)
│   ├── chat.py              # create_chat_router 装配 + 11个 @router 薄注册 (293行)
│   ├── _helpers.py          # 共享函数: prepare_session_context/persist_session
│   ├── chat_handlers.py     # handle_chat + handle_chat_with_files
│   ├── stream_handlers.py   # handle_chat_stream (SSE) + handle_websocket_chat (WS)
│   ├── approval_handlers.py # handle_approve_tool + handle_cancel_workflow
│   ├── workflow_handlers.py # handle_receive_workflow_event 等
│   └── session_handlers.py  # handle_list_sessions + handle_export_session
└── ...
```

- 旧 Brain Orchestration 10+5 测试（FSM 状态机/Planner/Reflector/SubAgent/Supervisor，被 ReActEngine 取代）
- 旧 TemplateWorkflow FSM 10+7 测试（WorkflowTemplate/ControlPoint/Transition，被 DAG Runner 取代）
- 旧 DDD 分层 10 文件（controlplane/port/repo/infrastructure，纯 ABC 无 .pyc，被 Temporal+WeldMap 取代）
- 旧异常+prompt 7+1 测试（control/exceptions.py FSM 异常 + capability/prompts/ 6 文件旧 ModeRegistry）
- 其他 6 文件（governance/exceptions.py 副本 + executionplane/mcp/ shim + bridge/decision_translator + AGENTS.md + docker-compose 重复 + check_phase_discipline）

**未来预留扩展点（保留不删）**：cognitiveplane/memory/（分层记忆算法）、knowledge/（知识服务层）、control/ports.py（6 Port ABC）、adapters/（生产部署 stub）、governance/permissions+ports（OPA）、shared/dto/{persona,reasoning_mode,weldmap_events}、copilot/。

---

## 三、Bridge L1↔L2

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ Bridge L1↔L2                                                                          │
│ boundary-pinning §6.2 · WorkflowSpec 透传（无 legacy 翻译）                            │
├──────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                      │
│  下行（L1 → L2）：                                                                    │
│  ┌──────────────────────────────────────────────────────────────────────────┐        │
│  │ design_workflow 产出 WorkflowSpec (DAG)                                  │        │
│  │   ↓                                                                       │        │
│  │ EventConnector.on_workflow_spec(spec)                                    │        │
│  │   ↓                                                                       │        │
│  │ WorkflowLauncher.launch(spec)                                            │        │
│  │   ↓                                                                       │        │
│  │ TemporalWorkflowLaunchPort.submit                                        │        │
│  │   ↓                                                                       │        │
│  │ temporal_client.start_workflow(RunWorkflowSpec)                          │        │
│  │   ↓                                                                       │        │
│  │ [L2 Temporal workflow 启动]                                              │        │
│  └──────────────────────────────────────────────────────────────────────────┘        │
│                                                                                      │
│  上行（L2 → L1）：                                                                    │
│  ┌──────────────────────────────────────────────────────────────────────────┐        │
│  │ [L2 节点事件]                                                            │        │
│  │   ↓                                                                       │        │
│  │ POST /api/v1/chat/workflow/events                                        │        │
│  │   ↓                                                                       │        │
│  │ WorkflowEventBus.publish(event)                                          │        │
│  │   • 存 _history (limit 50，新订阅者回看)                                  │        │
│  │   • 更新 _workflow_status (workflow_id → 最新状态)                        │        │
│  │   • 广播给 _subscribers[session_id] (多 Queue 支持多标签页)                │        │
│  │   • 通知 _publish_hooks (WorkflowObserver 订阅)                           │        │
│  │   ↓                                                                       │        │
│  │ WorkflowObserver (boundary-pinning §3 长路径观察者)                       │        │
│  │   • 仅 3 类 KEY_EVENTS 推 NotificationStore:                              │        │
│  │     workflow_completed / workflow_failed / workflow_paused                │        │
│  │   ↓                                                                       │        │
│  │ NotificationStore.add(5 类: CONFIRMATION_REQUEST / ESCALATION /           │        │
│  │   ALERT / INSTRUCTION / WORKFLOW_UPDATE)                                 │        │
│  │   ↓                                                                       │        │
│  │ [前端 WebSocket 推送]                                                     │        │
│  │                                                                           │        │
│  │ 另一路（注入 system prompt）:                                             │        │
│  │ ReActEngine._build_system_prompt                                         │        │
│  │   → workflow_event_bus.get_session_workflow_summary(session_id)           │        │
│  │   → 读取 _workflow_status + _history → 注入下一轮 prompt                  │        │
│  └──────────────────────────────────────────────────────────────────────────┘        │
│                                                                                      │
│  控制信号（L1 → L2）：                                                                │
│  ┌──────────────────────────────────────────────────────────────────────────┐        │
│  │ • query_status(workflow_id)        — 查询工作流状态                       │        │
│  │ • send_signal(pause/resume/cancel) — 协作式暂停/恢复/取消                 │        │
│  │ • send_human_gate_signal(decision) — L1 feedback 驱动 L2 节点继续/终止    │        │
│  │ • cancel_workflow(workflow_id)     — 取消工作流                           │        │
│  └──────────────────────────────────────────────────────────────────────────┘        │
│                                                                                      │
│  适配器：                                                                             │
│  • 默认 NullWorkflowLaunchPort（仅记录不提交，开发/测试）                              │
│  • 生产 TemporalWorkflowLaunchPort（app.py 装配）                                     │
│                                                                                      │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 四、L2 控制平面 — 算法与流程

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ L2 · CONTROL PLANE                                                                   │
├──────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                      │
│  ┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐       │
│  │ WorkflowLauncher    │───►│ RunWorkflowSpec     │───►│ HumanGate Signal    │       │
│  │ (LaunchPort)        │    │ (唯一生产 workflow)  │    │ (双重校验)          │       │
│  │                     │    │                     │    │                     │       │
│  │ • temporal_client   │    │ • 拓扑排序 (Kahn)    │    │ • L1 ApprovalGate   │       │
│  │ • Null adapter      │    │ • execute_node      │    │   (同步阻塞 5min)   │       │
│  │   (默认)            │    │   activity          │    │ • L2 HumanGateSignal│       │
│  │ • Temporal adapter  │    │ • dependency_       │    │   (Temporal signal) │       │
│  │   (生产)            │    │   results 注入      │    │ • pause/resume/     │       │
│  │                     │    │ • mocked_nodes      │    │   cancel            │       │
│  │ is_healthy()        │    │   透明化            │    │ • 协作式暂停        │       │
│  │ 探测                 │    │ • on_failure 决策    │    │   (signal handler) │       │
│  └─────────────────────┘    └──────────┬──────────┘    └──────────┬──────────┘       │
│                                        │                          │                  │
│                                        ▼                          ▼                  │
│                              ┌─────────────────────┐    ┌─────────────────────┐       │
│                              │ Retry & Failure     │    │ Temporal Worker     │       │
│                              │                     │    │ (Composition Root)  │       │
│                              │ • retry_policy      │    │                     │       │
│                              │   (max_attempts)    │    │ • RunWorkflowSpec   │       │
│                              │ • initial_interval  │    │ • execute_node      │       │
│                              │ • backoff           │    │   activity 注册     │       │
│                              │ • 异常分类:         │    │                     │       │
│                              │   transient (retry) │    │                     │       │
│                              │   fatal (fail)      │    │                     │       │
│                              │   signal (wait)     │    │                     │       │
│                              │ • failure_strategy: │    │                     │       │
│                              │   retry / skip /    │    │                     │       │
│                              │   abort             │    │                     │       │
│                              │ • EventLog 记录     │    │                     │       │
│                              └─────────────────────┘    └─────────────────────┘       │
│                                                                                      │
│                              ┌─────────────────────┐                                │
│                              │ WorkflowEventBus    │                                │
│                              │ (boundary-pinning   │                                │
│                              │  §3 长路径)         │                                │
│                              │                     │                                │
│                              │ • L2 节点事件 → L1  │                                │
│                              │ • WorkflowObserver  │                                │
│                              │ • → 注入 system     │                                │
│                              │   prompt            │                                │
│                              │ • → NotificationStore│                               │
│                              │   (前端推送)         │                                │
│                              └─────────────────────┘                                │
│                                                                                      │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

### L2 核心算法清单

| 算法 | 实现要点 |
|------|---------|
| **拓扑排序（Kahn 算法）** | 入度 0 的节点入队，循环剥入度；检测环 + 孤立节点 |
| **execute_node activity** | 按 `retry_policy`（max_attempts / initial_interval / backoff）重试，异常分类（transient retry / fatal fail / signal wait） |
| **dependency_results 注入** | 上游节点输出通过 `dependency_results` dict 注入下游节点 input_data，作为 WeldMap 数据传递的回退机制 |
| **HumanGate 双重校验** | L1 ApprovalGate（同步阻塞，5 分钟超时）+ L2 HumanGateSignal（Temporal signal，长流程节点暂停） |
| **协作式暂停** | Temporal signal handler，`pause_event` set 时 `await wait()`，`resume_event` set 时恢复 |
| **mocked_nodes 透明化** | 未注册 capability 走 fallback mock 路径，结果标注 `mocked=True` |
| **on_failure 决策** | 节点失败时按 `failure_strategy`（retry / skip / abort）处理，记 EventLog |
| **cancel_by_user** | 走 Temporal cancellation + cleanup activity |

**技术债**：feedback 信号链未端到端验证（CTO Briefing P0）；默认 NullWorkflowLaunchPort。

---

## 五、Bridge L2↔L3

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ Bridge L2↔L3                                                                          │
│ execute_node activity → ActivityPool.resolve(capability) · WeldMap 黑板共享状态        │
├──────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                      │
│  下行（L2 → L3）：                                                                    │
│  ┌──────────────────────────────────────────────────────────────────────────┐        │
│  │ RunWorkflowSpec.execute_node(node)                                       │        │
│  │   ↓                                                                       │        │
│  │ ActivityPool.resolve(node.capability)                                    │        │
│  │   ↓                                                                       │        │
│  │ Activity.execute(input_data, context)                                    │        │
│  │   ↓                                                                       │        │
│  │ [L3 activity 执行]                                                        │        │
│  └──────────────────────────────────────────────────────────────────────────┘        │
│                                                                                      │
│  上行（L3 → L2）：                                                                    │
│  ┌──────────────────────────────────────────────────────────────────────────┐        │
│  │ ActivityOutput (executionplane/activities/contracts.py)                  │        │
│  │   • status: ActivityStatus (OK / MARGINAL / NG / ERROR)                   │        │
│  │   • data: dict | None (结果数据)                                          │        │
│  │   • error: str | None                                                     │        │
│  │   ↓                                                                       │        │
│  │ [回传 L2 → dependency_results 注入下游节点]                                │        │
│  │   (mocked 标记在 data.mocked 字段，非顶层字段)                             │        │
│  └──────────────────────────────────────────────────────────────────────────┘        │
│                                                                                      │
│  横切（L3 内部跨 Activity 共享）：                                                     │
│  ┌──────────────────────────────────────────────────────────────────────────┐        │
│  │ WeldMap 黑板                                                              │        │
│  │   • CAS 乐观锁 (write_path 带版本号)                                      │        │
│  │   • Event Sourcing (WeldMapEvent 不可变写事件)                             │        │
│  │   • 层级路径 weldmap:///{workflow_id}/{domain}/                           │        │
│  │   • 6 个域: image_quality / image_preprocess /                            │        │
│  │             mask / annotations / validation / decision                    │        │
│  │   • InMemoryWeldMapClient (开发/测试)                                     │        │
│  │   • HTTP client / watcher (订阅)                                          │        │
│  └──────────────────────────────────────────────────────────────────────────┘        │
│                                                                                      │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 六、L3 执行平面 — 算法与流程

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ L3 · EXECUTION PLANE                                                                 │
├──────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                      │
│  ┌─────────────────────┐                                                             │
│  │ ActivityPool        │                                                             │
│  │ executionplane/     │                                                             │
│  │ pool.py             │                                                             │
│  │                     │    ┌──────────────────────────────────────────────────┐      │
│  │ 9 capability:       │    │ capability 别名映射:                            │      │
│  │ • IQA    (真实) ✅  │    │   defect_detection → iqa                        │      │
│  │ • PPA    (真实) ✅  │    │   preprocess → ppa                              │      │
│  │ • Annotation(真实)✅│    │   label_studio → annotation                     │      │
│  │ • MEA    (mock) ❌  │    │ 共享同一 WeldMapClient                          │      │
│  │ • RDA    (mock) ❌  │    │ (IQA write → PPA read 跨 activity 状态传递)     │      │
│  │ • VDA    (mock) ❌  │    └──────────────────────────────────────────────────┘      │
│  │ • RVA    (mock) ❌  │                                                             │
│  │ • MTA    (mock) ❌  │                                                             │
│  │ • HCA    (mock) ❌  │                                                             │
│  └──────────┬──────────┘                                                             │
│             │                                                                        │
│             ├──────────────────┬──────────────────┬──────────────────┐                 │
│             ▼                  ▼                  ▼                  ▼                 │
│  ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐ │
│  │ IQA 质量评估      │ │ PPA 预处理        │ │ Annotation 标注   │ │ 其他 6 个 mock   │ │
│  │ (complete)       │ │ (complete)       │ │ (complete)       │ │                  │ │
│  │                  │ │                  │ │                  │ │ • MEA 几何测量   │ │
│  │ 检查项:          │ │ 根据 IQA 报告:    │ │ 10 单一 action:  │ │ • RDA 缺陷识别   │ │
│  │ • 分辨率 ≥ 512   │ │ • 降噪 (高斯/    │ │ • list_datasets  │ │ • VDA 视觉决策   │ │
│  │ • 曝光 [45,90]   │ │   双边)          │ │ • get_dataset    │ │ • RVA 风险评估   │ │
│  │ • Laplacian ≥ 50 │ │ • 锐化 (Unsharp) │ │ • create_job     │ │ • MTA 测量任务   │ │
│  │ • 焊缝区域 ≥ 30% │ │ • 亮度 (Gamma)   │ │ • list_jobs      │ │ • HCA 人工审核   │ │
│  │                  │ │ • 对比度 (CLAHE) │ │ • get_job        │ │                  │ │
│  │ 路由决策:        │ │                  │ │ • create_task    │ │ fallback mock    │ │
│  │ • PASS           │ │ 策略映射写死     │ │ • list_tasks     │ │ 路径             │ │
│  │ • SUGGEST_REVIEW │ │ 但可配置         │ │ • upload_images  │ │                  │ │
│  │ • REJECT         │ │                  │ │ • assign_task    │ │ mocked=True      │ │
│  │                  │ │                  │ │ • trigger_ai     │ │                  │ │
│  │ 置信度加权       │ │                  │ │                  │ │                  │ │
│  │ 4 项指标         │ │                  │ │ 1 复合 action:   │ │                  │ │
│  │                  │ │                  │ │ • auto_annotate  │ │                  │ │
│  │                  │ │                  │ │   (6 步链路)     │ │                  │ │
│  │                  │ │                  │ │                  │ │                  │ │
│  │                  │ │                  │ │ version_id       │ │                  │ │
│  │                  │ │                  │ │ 4 级 fallback:   │ │                  │ │
│  │                  │ │                  │ │ params.version_id│ │                  │ │
│  │                  │ │                  │ │ → dataset_id +   │ │                  │ │
│  │                  │ │                  │ │   get_dataset    │ │                  │ │
│  │                  │ │                  │ │ → dependency_    │ │                  │ │
│  │                  │ │                  │ │   results 上游   │ │                  │ │
│  │                  │ │                  │ │ → list_datasets  │ │                  │ │
│  │                  │ │                  │ │   取第一个       │ │                  │ │
│  │                  │ │                  │ │                  │ │                  │ │
│  │                  │ │                  │ │ 每次新建短生命   │ │                  │ │
│  │                  │ │                  │ │ 周期 MCP client  │ │                  │ │
│  │                  │ │                  │ │ (Temporal 无状态)│ │                  │ │
│  └──────────────────┘ └──────────────────┘ └────────┬─────────┘ └──────────────────┘ │
│                                                      │                              │
│                                                      ▼                              │
│                                           ┌─────────────────────┐                   │
│                                           │ Label Studio MCP    │                   │
│                                           │ (远程 HTTP server)  │                   │
│                                           │                     │                   │
│                                           │ • HTTPMCPClient     │                   │
│                                           │   (Streamable HTTP) │                   │
│                                           │ • JWT token 注入    │                   │
│                                           │ • tools/list 拉取   │                   │
│                                           │   inputSchema       │                   │
│                                           │ • call_tool 透传    │                   │
│                                           │ • _parse_tool_      │                   │
│                                           │   response 统一解析 │                   │
│                                           │ • health_check      │                   │
│                                           │   (list_tools 探活) │                   │
│                                           └─────────────────────┘                   │
│                                                                                      │
│  ┌──────────────────────────────────────────────────────────────────────────────┐    │
│  │ WeldMap 黑板层                                                                │    │
│  │ executionplane/weldmap/                                                       │    │
│  │                                                                               │    │
│  │ 6 个域:                                                                       │    │
│  │ ┌─────────────────┬─────────────────┬─────────────────┐                       │    │
│  │ │ image_quality   │ image_preprocess│ mask            │                       │    │
│  │ │ (IQA 写入)      │ (PPA 写入)      │ (分割结果)      │                       │    │
│  │ │ resolution/     │ denoise/sharpen/│ ConsensusReport │                       │    │
│  │ │ exposure/focus/ │ brightness/     │ (U-Net/SAM 分歧)│                       │    │
│  │ │ completeness +  │ contrast        │                 │                       │    │
│  │ │ route_decision  │                 │                 │                       │    │
│  │ ├─────────────────┼─────────────────┼─────────────────┤                       │    │
│  │ │ annotations     │ validation      │ decision        │                       │    │
│  │ │ (标注结果)      │ (规则校验)      │ (最终判定)      │                       │    │
│  │ │ dict[element_id,│ RuleViolation   │ verdict         │                       │    │
│  │ │  AnnotationElem]│ CONS-01~15      │ OK/NG/MARGINAL/ │                       │    │
│  │ │                 │                 │ PENDING +       │                       │    │
│  │ │                 │                 │ RiskScore       │                       │    │
│  │ └─────────────────┴─────────────────┴─────────────────┘                       │    │
│  │                                                                               │    │
│  │ WeldMapClient ABC:                                                            │    │
│  │ • initialize                                                                  │    │
│  │ • read_snapshot                                                               │    │
│  │ • read_path (层级路径)                                                        │    │
│  │ • write_path (CAS 乐观锁版本号)                                               │    │
│  │ • subscribe (watcher 订阅)                                                    │    │
│  │                                                                               │    │
│  │ 实现:                                                                         │    │
│  │ • InMemoryWeldMapClient (开发/测试完整)                                       │    │
│  │ • HTTP client                                                                 │    │
│  │ • watcher (订阅机制)                                                          │    │
│  │ • Event Sourcing (WeldMapEvent 不可变写事件)                                  │    │
│  └──────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                      │
│  ┌──────────────────────────────────────────────────────────────────────────────┐    │
│  │ QualityStandard + Registry                                                    │    │
│  │                                                                               │    │
│  │ • GB/T 3323-2005 (金属熔化焊焊接接头射线照相)                                 │    │
│  │ • NB/T 47014-2011 (承压设备焊接工艺评定)                                      │    │
│  │ • ISO 5817 (焊缝缺陷分级)                                                    │    │
│  │ • GB 50661 (钢结构焊接规范)                                                   │    │
│  │                                                                               │    │
│  │ search_standards 工具查询                                                     │    │
│  │ capabilities/ 独立算法实现 (与 Activity 解耦)                                 │    │
│  └──────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                      │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

### L3 核心算法清单

| Activity | 算法 | 成熟度 |
|----------|------|--------|
| **IQA** | 分辨率 min(w,h)≥512 / 曝光灰度均值∈[45,90] / 对焦 Laplacian 方差≥50 / 完整性焊缝区域比≥30% / 路由决策 PASS/SUGGEST_REVIEW/REJECT / 置信度加权综合 | complete |
| **PPA** | 根据 IQA 报告自适应调优：降噪（高斯/双边）/ 锐化（Unsharp Mask）/ 亮度校正（Gamma）/ 对比度增强（CLAHE）；策略映射写死但可配置 | complete |
| **Annotation** | 10 单一 action + 1 复合 auto_annotate（6 步 plan-and-execute）；version_id 4 级 fallback；每次新建短生命周期 MCP client | complete |
| **MEA/RDA/VDA/RVA/MTA/HCA** | 未实现，走 fallback mock 路径，结果标注 `mocked=True` | stub |
| **WeldMap 黑板** | CAS 乐观锁 + Event Sourcing + 6 域层级路径；InMemory 完整，生产 Redis/MinIO+DB 未实现 | partial |
| **Label Studio MCP** | L3 `integrations/label_studio/client.py` 9 业务方法（类型安全 API）；L1 `adapters/mcp/label_studio_server.py` LabelStudioMCPClientAdapter 适配 shared HTTPMCPClient 到 cognitiveplane MCPClient ABC；共享 `shared/mcp/` 传输层（HTTPMCPClient + protocol），不共享业务封装（避免 L1 依赖 L3）；JWT token 注入；tools/list 拉取 inputSchema 透传 | complete |
| **auto_annotate 复合链路** | list_datasets → get_dataset → create_job → upload_images → create_task → trigger_ai（每步记 `steps[]`，失败 abort；trigger_ai 失败 non-fatal） | complete |
| **Activity 契约** | `executionplane/activities/contracts.py` 独立副本（ActivityStatus: OK/MARGINAL/NG/ERROR + ActivityInput + ActivityOutput(status/data/error)），打破 L3→L2 直接 import（端口/适配器隔离） | complete |

**技术债**：auto_annotate 是复合节点（CTO Briefing 要求拆分为 6 独立 Activities 走 ToolPool 隔离）；trigger_ai 失败 silent skip 不回滚；WeldMap 生产实现缺失；6/9 Activity mock（MEA/RDA/VDA/RVA/MTA/HCA）；节点级回溯不存在；contracts.py 是 controlplane/domain/activity.py 的副本（双份维护）。

---

## 七、完整数据流（端到端）

```
用户输入
  │
  ▼
[前端 chat.html]
  │ WebSocket /ws
  ▼
[Chat API] ──► [AgentLoop]
                  │
                  ▼
              [ReActEngine 核心循环]
                  │
                  ├─► 3-tier LLM (FC → DSML → Keyword)
                  ├─► ApprovalGate (写入工具阻塞)
                  ├─► Context Compaction (4 策略)
                  ├─► Plan-and-Execute (current_plan)
                  ├─► Skill 选择 + 锁定
                  ├─► Session Notes (19 工具)
                  ├─► 失败反思 (连续 ≥2 次)
                  └─► 同轮工具上限 (=3)
                  │
                  ▼
              [ToolRegistry] ──► 工具调用
                  │
                  ├─► 查询类 (search_standards/cases, web_search, read_weldmap)
                  │     ↓
                  │   直接返回结果
                  │
                  ├─► 标注类 (list_datasets, get_dataset, list_jobs, get_job, list_tasks)
                  │     ↓
                  │   MCP 标注工具 (10 个) ──► Label Studio 远程 HTTP
                  │     ↓
                  │   返回结果 + 写入 Session Notes
                  │
                  ├─► 写入类 (create_job, create_task, upload_image_to_dataset,
                  │          assign_task, trigger_ai)
                  │     ↓
                  │   ApprovalGate 阻塞 ──► 前端弹窗 ──► 用户确认
                  │     ↓
                  │   执行 ──► 返回 job_id/task_id ──► 写入 Session Notes
                  │
                  └─► 工作流类 (design_workflow, launch_workflow, control_workflow)
                        │
                        ▼
              [Bridge L1→L2]
                        │
                        │ WorkflowSpec (DAG)
                        ▼
              [L2 Temporal Workflow]
                        │
                        ├─► 拓扑排序 (Kahn)
                        ├─► execute_node activity
                        ├─► HumanGate 双重校验
                        ├─► retry_policy + 异常分类
                        │
                        │ NodeExecute (capability)
                        ▼
              [Bridge L2↔L3]
                        │
                        │ ActivityPool.resolve(capability)
                        ▼
              [L3 Activity 执行]
                        │
                        ├─► IQA (质量评估) ──► WeldMap.image_quality
                        ├─► PPA (预处理)   ──► WeldMap.image_preprocess
                        ├─► Annotation     ──► Label Studio MCP (10 工具)
                        ├─► MEA/RDA/VDA/RVA/MTA/HCA (mock fallback)
                        │
                        │ ActivityOutput
                        ▼
              [回传 L2]
                        │
                        │ dependency_results 注入下游节点
                        ▼
              [工作流完成]
                        │
                        │ WorkflowEventBus
                        ▼
              [Bridge L2→L1]
                        │
                        │ 节点事件 → WorkflowObserver
                        ▼
              [L1 注入下一轮 system prompt]
                        │
                        │ NotificationStore
                        ▼
              [前端 WebSocket 推送]
                        │
                        ▼
              [用户看到结果]
```

---

## 八、架构哲学总结

1. **架构替 LLM 做看不见的事** — ApprovalGate、Context Compaction、Session Notes、DSML fallback、同轮工具上限、ID 类型区分、ToolFailureReflector（schema 反射），全部架构层固化，不依赖 LLM 自觉。
2. **三平面 specialization** — L1 推理 / L2 编排 / L3 执行各司其职，跨层通过 WeldMap + Temporal signal 通信。
3. **boundary-pinning 边界钉死** — L1 不直接执行工业 verb，L2 不直接调 LLM，L3 不直接暴露给用户；每层边界清晰；L1 与 L3 共享 `shared/mcp/` 传输层但不共享业务封装。
4. **MCP 协议化工具层** — Label Studio 通过 MCP 协议接入，L1 通过 ToolPolicy 分级控制可见性；L1 adapters/mcp 适配 shared HTTPMCPClient。
5. **双路径统一治理** — HTTP /stream 与 WS /ws 共享 hooks/skill_registry/approval_store/compactor/guardrails；session 持久化通过 `_helpers.py` 的 `prepare_session_context`/`persist_session` 共享函数消除重复，治理一致。
6. **WeldMap 黑板解耦** — 跨 Activity 状态传递通过 WeldMap 黑板，CAS 乐观锁 + Event Sourcing 支持回放。
7. **三层 Guardrails 防护** — AfterToolHook（工具结果）+ OutputGuardrail（LLM 输出）+ 焊接领域规则（电流/预热/绕过国标），默认 WARN 不阻断。
8. **LLM-as-Judge 闭环** — Evaluator 4 维度评分 + GOLDEN_SET 4 用例回归 + Langfuse 分数挂载，无 Langfuse 时 NoOp 降级。
9. **确定性缓存** — LLMResponseCache 仅缓存 temperature==0 请求，降低重复推理成本，不缓存 tool_calls/stream。

---

*基于 2026-07-08 代码版本对照源码梳理（含 boundary-pinning 重构 + 三层拆解：react.py 2728行→engine/ 6文件、app.py 989行→bootstrap/ 4文件、chat.py 852行→7文件、interface/__init__.py 698行→6文件、registry/ 子目录重组、/stream和/ws重复逻辑消除 + 死代码清理 56 文件）。*
