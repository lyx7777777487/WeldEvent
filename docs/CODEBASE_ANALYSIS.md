# WeldEvent 代码盘点 — 算法与结构先进性

> **代码版本**：L1 cognitiveplane + L2 controlplane + L3 executionplane 三平面 monorepo

---

## 一、项目定位

WeldEvent 是一个**焊接质检智能决策引擎**，采用 **L1 认知 + L2 控制 + L3 执行** 三平面架构：

| 平面 | 职责 | 技术栈 |
|------|------|--------|
| **L1 cognitiveplane** | LLM ReAct 推理 + 工具编排 + 用户交互 | FastAPI + asyncio + 自研 ReActEngine + WebSocket |
| **L2 controlplane** | Temporal workflow DAG runner | temporalio 1.27 + Python |
| **L3 executionplane** | 工业质检 Activity 执行 | numpy + scipy + cv2 + Pillow + Label Studio MCP |

**核心理念**：不是通用 Agent 框架，而是**工业场景 specialization** — 通过大量"架构替 LLM 做看不见的事"的工程化设计，补强通用 ReAct 的脆弱点。

---

## 二、L1 认知平面 — 算法与结构

### 2.1 ReActEngine 核心循环（`cognitiveplane/control/engine/`）

> **拆解后结构**：原 `react.py` 2728 行单文件已拆解为 `engine/` 子目录 6 文件，`control/react.py` 保留为 78 行 shim（re-export 全部公开符号，保持对外接口零改动）。
>
> | 文件 | 职责 |
> |------|------|
> | `engine/react.py` (1294行) | ReActEngine 核心类 + Tier fallback + 常量 + InteractionTier/InteractionResponse |
> | `engine/approval.py` (203行) | ApprovalStore + ApprovalRequest + APPROVAL_REQUIRED_TOOLS + summarize_for_approval |
> | `engine/session_notes.py` (249行) | derive_note (19工具) + derive_failure_reflection + build_progress_note + TOOL_TIMEOUT_SECONDS |
> | `engine/system_prompt.py` (588行) | WorldviewBuilder + SystemPromptBuilder + activity catalog + memory 注入 |
> | `engine/tool_execution.py` (647行) | ToolExecutor + ToolCallValidator + HookRunner + RatchetRecorder |
> | `engine/__init__.py` (80行) | re-export 23 个公开符号 |

| 算法 | 实现要点 | 成熟度 |
|------|---------|--------|
| **3-tier LLM fallback** | Tier-1 Function Calling / Tier-2 Structured 意图分类 / Tier-3 关键词规则；启动时选定，非运行时回退 | complete |
| **ApprovalGate 架构阻塞** | 6 个写入类工具（launch_workflow / create_job / create_task / trigger_ai / upload_images / assign_task）执行前 `await event.wait(timeout=300s)`，架构层强制阻塞，LLM 无法绕过 | complete |
| **Context Compaction 4 策略** | SLIDING_WINDOW / FULL_SUMMARY / SELF_COMPACT_SLIDING（默认）/ SELF_COMPACT_ALL；fallback 链 expensive→cheap；MUST PRESERVE / CAN DROP 边界明确 | complete |
| **Plan-and-Execute** | `design_workflow` 成功后写 `session["current_plan"]`，下轮注入 system prompt；`launch_workflow` 成功后清理并解锁 skill | complete |
| **Skill 选择 + 锁定** | 子串计数 match_score + session 锁定 + 工具驱动自动切换 + 3 级漂移检测（提示→强提示→解锁） | complete |
| **Session Notes** | `_derive_note` 覆盖 19 工具：L1 工具 9 个（analyze_image/design_workflow/launch_workflow/read_weldmap/escalate/explain_decision/web_search/request_confirmation/upload_image_to_dataset）+ MCP 标注 10 个（list_datasets/get_dataset/list_jobs/get_job/list_tasks/create_job/create_task/upload_images/assign_task/trigger_ai）；查询类记结果摘要 + first_job_id/first_task_id；写入类记产出 ID（job_id/task_id/version_id）；request_confirmation 记 user_selection；FIFO 上限 8 条 | complete |
| **同轮同名工具上限** | `SAME_TOOL_LIMIT=3`，超限静默 reject（不 yield 给前端），回填 LLM 提示走 web_search | complete |
| **三层知识获取 fallback** | system prompt 约束：search_standards（≤2 次）→ web_search → request_confirmation 弹窗 | complete |
| **DSML fallback 解析** | LLM 未返回原生 tool_calls 但 content 含 `<｜｜DSML｜｜>` 时正则解析；stream 最终轮 `re.sub` 清理残留 | complete |
| **失败反思** | `fail_counts[tool:args_fingerprint]` 连续 ≥2 次失败触发 verification gate，按工具+错误类型生成针对性提示 | complete |
| **ToolFailureReflector（schema 反射）** | 独立模块 `tool_failure_reflector.py`（与 fail_counts 不同概念）：jsonschema ValidationError 时尝试自动修复；design_workflow 专用策略（6 类错误含 depends_on 模糊匹配 auto-fix）；generic 策略（missing query/reason 从 user_input 推断）；返回 ReflectionResult(severity=auto_fixed/actionable/fatal) | complete |

**关键常量**：`max_iterations=25`、`SAME_TOOL_LIMIT=3`、`TOOL_TIMEOUT_SECONDS=180`、`APPROVAL_TIMEOUT_SECONDS=300`。

**技术债**：3-tier 知识 fallback 仅靠 prompt 约束（非架构强制）；Context Compaction token 估算 4 chars/token（中文偏差大）；Tier-A/B 工具集硬编码；ToolFailureReflector 仅 design_workflow 有专用策略（其他工具走 generic）；Guardrails 默认 WARN 不阻断；Evaluator 无 Langfuse key 时 NoOp 降级；LLMResponseCache 单进程不跨进程共享。

### 2.2 AgentLoop — WebSocket 生命周期管理（`cognitiveplane/control/agent_loop.py`）

| 算法 | 实现要点 | 成熟度 |
|------|---------|--------|
| **双 task 模型** | `receive_task`（消费 `_incoming` queue 分发 chat/feedback/interrupt）+ `react_task`（按需启动跑 `run_stream()`，同一时刻仅一个） | complete |
| **tool_registry 透传** | 构造时接收外部 `tool_registry`，复用 `engine._tools`，确保标注 MCP 工具可见 | complete |
| **session 持久化** | `_prepare_session_context`/`_persist_session` 委托 `interaction/api/_helpers.py` 的共享函数 `prepare_session_context`/`persist_session`，与 HTTP /stream 路径行为一致，消除重复实现；构造时接收 session_manager/image_sessions/router 三参数；新增 `after_tool_hooks` + `output_guardrail` 参数（Phase 4 Guardrails 透传） | complete |
| **feedback** | `_handle_feedback` → MemoryWritePort + `_feedback_queue`（maxsize=10）；`_drain_feedback_queue` 在 react_task 启动前排空记 EventLog | complete |
| **interrupt** | `_handle_interrupt` → `react_task.cancel()` + 发 `interrupted` 事件 | complete |

**技术债**：feedback 实际注入下一轮 ReAct 靠 §5.1 `_fetch_memory_hits` 读 Memory，异步信号未直接插入当前 messages。

### 2.3 ToolRegistry（`cognitiveplane/control/registry/tool_registry.py`）

> **目录重组**：原 `control/` 下的 tool_registry/mcp_registry/mcp_server/tool_failure_reflector 已移入 `control/registry/` 子目录，`control/registry/__init__.py` re-export 全部公开符号。

- **14 个 L1 工具**：search_standards/cases/process、analyze_image、**upload_image_to_dataset（L1 包装）**、read_weldmap、design_workflow、launch_workflow、**control_workflow**（文件名 workflow_control.py，tool.name 返回 "control_workflow"）、request_confirmation、escalate、explain_decision、archive_memory、web_search。
- **phase 过滤**：`current_phase=3`，`phase > current_phase` 的工具仍注册但不暴露给 LLM。
- **Schema 校验**：`jsonschema.Draft7Validator`，allow extra properties（LLM-friendly）。
- **upload_image_to_dataset 包装工具**：接受 `image_ref` + `version_id`，内部 ImageStore 取图→压缩（1024px JPEG 80%）→base64→调 MCP `upload_images`。解决 LLM 有 image_ref 但 MCP 要 base64 的断层。
- **control_workflow 工具**：action=query/pause/resume/cancel；query 优先读 L1 WorkflowEventBus 缓存（快），fallback 到 L2 Temporal query_status（权威）；pause/resume/cancel 调 EventConnector.send_signal。

### 2.4 MCP 三层 ToolPolicy（`cognitiveplane/control/registry/mcp_server.py` + `adapters/mcp/`）

- **三层判定**：MCPAnnotations（server 自报）→ mcp_policy.yaml（人工分级 A/B/C + auto_approve）→ MCPToolPolicyClassifier（默认 tier C）。
- **Label Studio MCP**：远程 HTTP server，运行时 `tools/list` 拉取 10 个工具的 inputSchema 透传给 LLM。
- **phase_override 机制**：标注工具 phase=3 可见，其他工业 MCP 默认 phase=4 隐藏。
- **L1 适配器**：`adapters/mcp/label_studio_server.py` 的 `LabelStudioMCPClientAdapter` 适配 `shared/mcp/client.py` HTTPMCPClient 到 cognitiveplane MCPClient ABC；`create_label_studio_mcp_server(config)` 工厂自动登录+connect+initialize+包装，返回 `MCPServer(phase_override=3)`。共享传输层但不共享业务封装（避免 L1 依赖 L3）。

### 2.5 双路径 Chat API（`cognitiveplane/interaction/api/`）

> **拆解后结构**：原 `chat.py` 852 行单文件已拆解为 7 文件，`chat.py` 保留为 293 行的 router 装配 + 11 个 `@router` 薄注册 wrapper。
>
> | 文件 | 职责 |
> |------|------|
> | `chat.py` (293行) | create_chat_router 依赖注入 + ChatRequest/ChatResponse 模型 + 11 个 @router 薄注册 |
> | `_helpers.py` (136行) | **共享函数**: `prepare_session_context`/`persist_session`（消除 /stream 和 /ws 重复）+ `_inject_session_image_refs` + `_trigger_evaluation` |
> | `chat_handlers.py` (321行) | handle_chat (POST /) + handle_chat_with_files (POST /upload) |
> | `stream_handlers.py` (211行) | handle_chat_stream (POST /stream SSE) + handle_websocket_chat (WS /ws) |
> | `approval_handlers.py` (75行) | handle_approve_tool + handle_cancel_workflow |
> | `workflow_handlers.py` (125行) | handle_receive_workflow_event + handle_query_workflow_status + handle_workflow_stream |
> | `session_handlers.py` (45行) | handle_list_sessions + handle_export_session |

| 路径 | 端点 | 特点 |
|------|------|------|
| **HTTP /stream** | `POST /stream` SSE | 每请求新建 ReActEngine 但复用 ToolRegistry；finally 块调 `persist_session` 共享函数 |
| **WebSocket /ws** | `WS /ws` AgentLoop | 连接级 AgentLoop，包裹 ReActEngine，支持中断/反馈；`_prepare_session_context`/`_persist_session` 委托共享函数 |

两路径共享 hooks（SafetyHook + PolicyHook）、skill_registry、approval_store、context_compactor、image_store、after_tool_hooks、output_guardrail；session 持久化通过 `_helpers.py` 共享函数消除重复。

**完整端点（11 个）**：
- `POST /`（基础 chat，返回 ChatResponse）
- `POST /upload`（图片上传，multipart/form-data，返回 ChatResponse）
- `POST /stream`（SSE 流式）
- `WS /ws`（AgentLoop WebSocket）
- `POST /cancel`（取消当前流）
- `POST /workflow/events`（L2 → L1 事件入口）
- `GET /sessions`（列出所有 session，调试用）
- `GET /sessions/{session_id}/export`（导出完整对话历史）
- `GET /workflow/stream/{session_id}`（workflow 事件 SSE）
- `POST /workflow/status`（查询 workflow 状态）
- `POST /approve`（resolve ApprovalStore，human-in-the-loop）

### 2.6 通知系统 + WorkflowEventBus

- **NotificationStore**（`interaction/notifications/store.py`）：进程内 pub/sub。
  - **5 类 NotificationType**：CONFIRMATION_REQUEST / ESCALATION / ALERT / INSTRUCTION / WORKFLOW_UPDATE。
  - **4 状态 NotificationStatus**：PENDING / ACKNOWLEDGED / RESOLVED / EXPIRED。
  - `add()` 存储 + 推送 subscriber queue；`get_pending(operator_id)`；`update_status()`；`cleanup_expired()`。
  - `get_notification_store()` 进程级单例。
- **WorkflowEventBus**（`interaction/workflow_events.py`）：L2 Temporal workflow 事件 → L1 观察者 → 注入下一轮 system prompt（boundary-pinning §3 长路径）。
  - `publish(event)`：存 `_history`（limit 50，新订阅者回看）+ 更新 `_workflow_status` + 广播 `_subscribers[session_id]`（多 Queue 支持多标签页）+ 通知 `_publish_hooks`。
  - `get_session_workflow_summary(session_id)`：供 ReActEngine `_build_system_prompt` 读取注入下一轮 prompt。
  - `get_workflow_event_bus()` 进程级单例。
- **WorkflowObserver**（`control/workflow_observer.py`）：订阅 WorkflowEventBus publish hooks，仅 3 类 KEY_EVENTS（workflow_completed/failed/paused）推 NotificationStore；app.py lifespan 启动/停止。

### 2.7 三层 Guardrails（`cognitiveplane/governance/guardrails.py`）

- **GuardrailAction**：PASS / WARN / REJECT。
- **AfterToolHook（ABC）**：`after_execute(tool_name, arguments, result, session_id) -> GuardrailResult` — 工具结果护栏。
  - **WeldingAfterToolHook**：2 条焊接规则（welding_current_overload 电流>500A / preheat_too_low 预热<100°C），REJECT 时返回 replacement。
- **OutputGuardrail（ABC）**：`check_output(reply, tools_used, session_id) -> GuardrailResult` — LLM 输出护栏。
  - **WeldingOutputGuardrail**：3 条规则（bypass_standard 绕过国标 / skip_inspection 跳过检测 / fabricate_parameter 伪造参数），REJECT 时返回合规提示。
- **接入**：chat.py 实例化后传入 ReActEngine + AgentLoop。

### 2.8 LLM-as-Judge 评估框架（`cognitiveplane/governance/evaluation.py` + `eval_dataset.py`）

- **EvaluationResult / EvaluationInput** dataclass。
- **WeldingLLMJudge**：4 维度评分（helpfulness / safety / tool_efficiency / grounding），独立 judge prompt，max_tokens=1024，含 `_repair_truncated_json` 修复截断 JSON。
- **ScoreRecorder**：NoOpScoreRecorder / LangfuseScoreRecorder（挂载分数到 Langfuse trace）。
- **Evaluator**：`evaluate()` 同步 / `evaluate_background()` 异步不阻塞返回。
- **GOLDEN_SET**（`eval_dataset.py`）：4 个 golden 用例（正常工艺咨询 / 危险建议-跳过检测 / 违规参数-电流过大 / 国标依据充分）；`run_golden_set()` 跑全量回归；`python -m cognitiveplane.governance.eval_dataset` 命令行执行。

### 2.9 LLMResponseCache（`cognitiveplane/capability/response_cache.py`）

- **LLMResponseCache**：进程内 LRU + 可选 TTL。
- **缓存内容**：`LLMResponse` 对象（仅 temperature==0 的确定性请求）。
- **Key**：messages + model + temperature + max_tokens + top_p + response_format(schema) + tools 的 SHA256；排除 caller/case_id/purpose。
- **不缓存**：temperature>0（随机性）/ tool_calls 响应（应实时）/ stream()（实时性）。
- **容量**：默认 maxsize=256（约 512KB）。
- **接入**：`bootstrap/llm_setup.py` 的 `build_llm_cache()` 工厂构造，传入 OpenAIProvider；`bootstrap/__init__.py` re-export `bootstrap_llm`/`build_dependencies`/`build_tool_registry_for_mcp`/`build_llm_cache`/`build_seed_knowledge`，`app.py` 从 989 行瘦身至 240 行（仅保留 .env 加载 + create_app + lifespan + main）。

### 2.10 L1 目录结构（boundary-pinning 拆解后）

```
cognitiveplane/
├── app.py                    # FastAPI composition root (240行, 从989行瘦身)
├── bootstrap/                # 装配分离 (从 app.py 拆出, 4文件)
│   ├── __init__.py          # re-export 全部公开符号
│   ├── llm_setup.py         # probe_chat/bootstrap_llm/build_llm_cache (126行)
│   ├── dependencies.py      # build_dependencies (151行, 调 build_seed_knowledge)
│   └── seed_knowledge.py    # build_seed_knowledge (504行, 6类种子数据收进函数)
├── control/
│   ├── react.py             # shim (78行, re-export ReActEngine 等公开符号)
│   ├── agent_loop.py        # AgentLoop 双 task
│   ├── engine/              # ReActEngine 拆解 (从 react.py 2728行拆出, 6文件)
│   ├── registry/            # 工具/MCP 注册表 (从 control/ 重组, 5文件)
│   └── tools/               # 14 个 L1 工具实现
├── interaction/api/         # Chat API (从 chat.py 852行拆解, 7文件)
└── ...
```

---

## 三、L2 控制平面 — 算法与结构

### 3.1 RunWorkflowSpec — 唯一生产 Temporal workflow

- **拓扑排序**：Kahn 算法，入度 0 的节点入队，循环剥入度；检测环 + 孤立节点。
- **节点执行**：`execute_node` activity，按 `retry_policy`（max_attempts / initial_interval / backoff）重试，异常分类（transient retry / fatal fail / signal wait）。
- **dependency_results 注入**：上游节点输出通过 `dependency_results` dict 注入下游节点 input_data，作为 WeldMap 数据传递的回退机制。

### 3.2 HumanGate Signal — 双重校验

- **L1 ApprovalGate**（同步阻塞，5 分钟超时）+ **L2 HumanGateSignal**（Temporal signal，长流程节点暂停）双重门禁。
- `send_human_gate_signal(workflow_id, node_id, decision)` 让 L1 feedback 能驱动 L2 节点继续/终止。

### 3.3 pause / resume / cancel_by_user

- 协作式暂停：Temporal signal handler，`pause_event` set 时 `await wait()`，`resume_event` set 时恢复。
- `cancel_by_user` 走 Temporal cancellation + cleanup activity。

### 3.4 其他

- **mocked_nodes 透明化**：未注册 capability 走 fallback mock 路径，结果标注 `mocked=True`。
- **on_failure 决策**：节点失败时按 `failure_strategy`（retry / skip / abort）处理，记 EventLog。
- **Worker**：Composition Root，注册 `RunWorkflowSpec` + `execute_node` activity。

**技术债**：feedback 信号链未端到端验证（CTO Briefing P0）；默认 NullWorkflowLaunchPort（生产需 app.py 装配 Temporal adapter）。

---

## 四、L3 执行平面 — 算法与结构

### 4.0 L3 interface 拆解（`executionplane/interface/`）

> **拆解后结构**：原 `interface/__init__.py` 698 行单文件已拆解为 6 文件，`__init__.py` 保留为 61 行 re-export + `__all__`。
>
> | 文件 | 职责 |
> |------|------|
> | `__init__.py` (61行) | re-export 全部公开 API |
> | `result.py` (146行) | IqaResult + _convert_output_to_result |
> | `config.py` (123行) | 4 全局单例 + configure_mllm/register_standard/list_available_standards/get_standard_info |
> | `async_api.py` (243行) | run_iqa/run_iqa_folder/run_iqa_batch |
> | `sync_api.py` (102行) | 同步包装 |
> | `io.py` (61行) | save_results_to_json/print_summary |

### 4.1 ActivityPool（`executionplane/pool.py`）

9 个 capability：IQA / PPA / MEA / RDA / VDA / RVA / MTA / HCA / Annotation。

**真实注册 3/9**：IQA、PPA、Annotation。其余 6 个走 mock fallback。

### 4.2 IQA — 图像质量评估

| 算法 | 实现 | 成熟度 |
|------|------|--------|
| 分辨率检查 | min(w,h) ≥ 512 | complete |
| 曝光检查 | 灰度均值 ∈ [45, 90] | complete |
| 对焦检查 | Laplacian 方差 ≥ 50 | complete |
| 完整性检查 | 焊缝区域比 ≥ 30%（阈值分割） | complete |
| 路由决策 | PASS / SUGGEST_REVIEW / REJECT | complete |
| 置信度 | 加权综合 4 项指标 | complete |

### 4.3 PPA — 图像预处理

根据 IQA 报告自适应调优：降噪（高斯/双边）、锐化（Unsharp Mask）、亮度校正（Gamma）、对比度增强（CLAHE）。策略映射写死但可配置。

### 4.4 Annotation — 标注（`executionplane/activities/annotation/activity.py`）

- **10 个单一 action**：list_datasets / get_dataset / create_job / list_jobs / get_job / list_tasks / create_task / trigger_ai / upload_images / assign_task。
- **auto_annotate 复合 action**：list_datasets → get_dataset → create_job → upload_images → create_task → trigger_ai（6 步 plan-and-execute，每步记 `steps[]`，失败 abort）。
- **version_id 4 级 fallback**：params.version_id → params.dataset_id+get_dataset → dependency_results 上游 → list_datasets 取第一个。
- **每次 execute 新建短生命周期 MCP client**（Temporal activity 无状态语义）。

**技术债**：auto_annotate 是复合节点（CTO Briefing 要求拆分为 6 独立 Activities 走 ToolPool 隔离，尚未做）；trigger_ai 失败 silent skip 不回滚。

### 4.4.1 Activity 契约（`executionplane/activities/contracts.py`）

- **独立副本**：打破 L3 → L2 直接 import（端口/适配器隔离违规），是 `controlplane/domain/activity.py` 的独立副本。
- **ActivityStatus(str, Enum)**：OK / MARGINAL / NG / ERROR。
- **ActivityInput**：control_point_id + workflow_context + params。
- **ActivityOutput**：status + data(dict|None) + error(str|None) — 无顶层 mocked 字段（mocked 标记在 data.mocked）。
- **技术债**：双份维护（contracts.py 与 controlplane/domain/activity.py 内容重复）。

### 4.5 WeldMap 黑板层（`executionplane/weldmap/`）

- **层级路径**：`weldmap:///{workflow_id}/{image|mask|annotations|validation|rendering|decision}/`
- **6 个域**：image_quality / image_preprocess / mask / annotations / validation / decision。
- **CAS 乐观锁**：`write_path` 带版本号，冲突返回 `WriteResult.conflict=True`。
- **Event Sourcing**：`WeldMapEvent` 不可变写事件，支持回放。
- **实现**：InMemoryWeldMapClient（开发/测试完整），生产 Redis/MinIO+DB 未实现。

### 4.6 Label Studio MCP Client（`executionplane/integrations/label_studio/client.py`）

- **9 个业务方法**（类型安全 API，给 L3 activity 用）：list_datasets / get_dataset / create_job / list_jobs / get_job / list_tasks / create_task / trigger_ai / upload_images / assign_task。
- **共享传输层**：`shared/mcp/client.py` HTTPMCPClient（Streamable HTTP）+ JWT token 注入 `Authorization: Bearer`；L1 通过 `adapters/mcp/label_studio_server.py` 适配同一传输层。
- **响应解析**：`_parse_tool_response` 统一处理 JSON dict 透传 / 纯文本正则提取 `jobId|taskId`。

### 4.7 能力层 + QualityStandard

- **capabilities/**：IQA / PPA / MEA / RDA / VDA / RVA / MTA / HCA / Annotation 各自独立算法实现（与 Activity 解耦）。
- **QualityStandard + Registry**：标准条款数据结构 + 注册表，供 search_standards 查询。

---

## 五、跨层桥接与横切关注

### 5.1 L1→L2 桥接（`cognitiveplane/bridge/`）

- **EventConnector.on_workflow_spec(spec)** → 写 WeldMap workflow domain → `WorkflowLauncher.launch(spec)` → `TemporalWorkflowLaunchPort.submit` → `temporal_client.start_workflow(RunWorkflowSpec)`。
- **boundary-pinning §6.2 重写**：废弃 legacy `BrainDecision → WorkflowTemplate` 翻译，直接接收 `design_workflow` 产出的 `WorkflowSpec`。
- **控制能力**：`query_status` / `send_human_gate_signal` / `send_signal`（pause/resume/cancel_by_user）/ `cancel_workflow`。
- **默认 NullWorkflowLaunchPort**（仅记录不提交），生产环境 app.py 替换为 Temporal adapter。

### 5.2 ImageStore（`cognitiveplane/interaction/image_store.py`）

- **双轨设计**：thumbnail（data URL，注入 LLM content，成本闸门）+ original（raw bytes，工具内部取，高清分析）。
- **image_ref 格式**：`PENDING:{session_id}:{index}`。
- **session-scoped**：Phase 3 内存存储，Phase 4+ 切 MinIO。

### 5.3 前端（`cognitiveplane/static/chat.html`）

| 功能 | 实现 | 成熟度 |
|------|------|--------|
| **流式渲染** | `handleStreamEvent` 处理 thinking/tool_call/tool_result/token/final/error；打字机效果 | complete |
| **approval 弹窗** | 按 tool_name 动态文案（8 个工具：launch_workflow/create_job/upload_images/create_task/trigger_ai/assign_task/request_confirmation/escalate），各有 approve/reject/title/resolvedOk | complete |
| **通知弹窗** | `showNotificationPopup` 渲染 confirmation_request/escalation/workflow_update；payload.options ≥2 时 N 按钮 2 列 grid | complete |
| **WebSocket 连接** | `connectChatWs()` 建立 `/ws`，失败 fallback 到 HTTP `/stream` | complete |
| **中断按钮** | `stopChat()` 优先 WS `sendInterrupt()`，fallback HTTP abort | complete |
| **反馈机制** | `sendFeedback(correction, category)` 通过 WS 发 `{type:"feedback",...}` | complete |

**技术债**：HTTP /stream fallback 路径与 WS 路径事件处理逻辑重复；TOOL_LABELS 硬编码。

### 5.4 其他横切

- **Langfuse v3 集成**：trace / span / generation 三层，LLM 调用全记录。
- **EventLog**：架构层事件溯源（BrainEventType 13 类），不依赖 LLM。
- **ValidationPipeline**：5 级验证（syntax / semantic / safety / policy / completeness）。
- **Memory 系统**：MemoryWritePort + retrieval（`_fetch_memory_hits` 注入下一轮 system prompt）。
- **LLMResponseCache**（`capability/response_cache.py`）：LRU+TTL，仅缓存 temperature==0 请求，maxsize=256。
- **三层 Guardrails**（`governance/guardrails.py`）：AfterToolHook + OutputGuardrail，焊接领域 5 条规则，默认 WARN 不阻断。
- **LLM-as-Judge 评估**（`governance/evaluation.py` + `eval_dataset.py`）：4 维度评分 + GOLDEN_SET 4 用例回归 + Langfuse 分数挂载。
- **ToolFailureReflector**（`control/registry/tool_failure_reflector.py`）：schema 校验失败自动修复，design_workflow 专用策略。
- **WorkflowObserver**（`control/workflow_observer.py`）：长路径 Temporal 观察者，3 类 KEY_EVENTS 推 NotificationStore，app.py lifespan 管理。

### 5.5 shared/ 跨层共享层

- **`shared/mcp/`**：MCP 传输层共享。
  - `client.py`：MCPClient ABC + StdioMCPClient + HTTPMCPClient（Streamable HTTP）。
  - `protocol.py`：MCPRequestBuilder / MCPResponse / RPCError / ToolCallResult / ToolDescriptor。
- **`shared/labelstudio/`**：Label Studio 认证配置共享。
  - `auth.py`：`login_labelstudio()` 获取 JWT token。
  - `config.py`：`LabelStudioConfig` 配置 dataclass。
- **设计意图**：L1（`cognitiveplane/adapters/mcp/`）与 L3（`executionplane/mcp/`）共享传输层但不共享业务封装，避免 L1 依赖 L3（boundary-pinning §1.5 ToolPool 隔离）。

---

## 六、算法先进性总览

### 6.1 真正的算法亮点（complete 且有创新）

1. **ApprovalGate 架构层强制阻塞** — 相比 Claude Code 用 system prompt 约束的脆弱方式，架构层 `await event.wait()` 阻塞，LLM 完全无法绕过；相比 AutoGen conversation turn，同一轮 ReAct 内暂停。
2. **Context Compaction MUST PRESERVE / CAN DROP** — 直接借鉴 Anthropic Context Engineering，明确告诉 LLM 保留什么丢弃什么，而非架构硬编码语义密度分级。
3. **DSML fallback 解析** — DeepSeek 等模型不支持原生 function calling 时，从 `<｜｜DSML｜｜tool_calls>` 文本格式解析 tool_calls，让非 FC 模型也能用 ReAct。
4. **Session Notes + 失败反思** — 每轮工具执行后生成简短笔记存 session，下一轮注入；连续 2 次失败触发 verification gate，按工具+错误类型生成针对性提示。
5. **同轮同名工具上限 + 静默 reject** — 防止 LLM 穷举 query 反复查同一工具，超限不 yield 给前端（避免架构内部信息泄露）。
6. **三层知识获取 fallback** — search_standards → web_search → request_confirmation，明确禁止穷举式调用反模式。
7. **upload_image_to_dataset L1 包装工具** — 解决 LLM 有 image_ref 但 MCP 要 base64 的断层，工具内部自动转换。
8. **AgentLoop 双 task + WebSocket** — 连接级生命周期管理，包裹 ReActEngine，支持中断/反馈，复用外部 ToolRegistry。
9. **WeldMap 黑板层 CAS 乐观锁 + Event Sourcing** — 跨 Activity 状态传递，支持回放。
10. **L2 拓扑排序 + dependency_results 注入** — DAG 节点依赖解析 + 上游输出注入下游作为 WeldMap 回退机制。
11. **ToolFailureReflector schema 反射** — jsonschema ValidationError 时自动修复（design_workflow 6 类错误含 depends_on 模糊匹配），降低 LLM 因 schema 错误重试浪费的迭代次数。
12. **三层 Guardrails 焊接领域护栏** — AfterToolHook + OutputGuardrail + 5 条焊接规则（电流/预热/绕过国标/跳过检测/伪造参数），架构层 REJECT 危险输出。
13. **LLM-as-Judge 评估闭环** — 4 维度评分 + GOLDEN_SET 4 用例回归测试 + Langfuse 分数挂载，让 LLM 输出质量可量化追踪。
14. **LLMResponseCache 确定性缓存** — 仅缓存 temperature==0 请求，key 含 tools+response_format SHA256，降低重复推理成本。

### 6.2 简陋或 stub 的部分

| 项 | 现状 | 影响 |
|---|------|------|
| Tier-3 Embedding Rules | 实际是关键词子串匹配，非 embedding | 命名误导，无语义匹配能力 |
| L3 Activity Pool | 6/9 mock（MEA/RDA/VDA/RVA/MTA/HCA） | 全流程质检只能跑 IQA+PPA+Annotation |
| Context Compaction token 估算 | 4 chars/token 粗估 | 中文场景偏差大 |
| auto_annotate 复合节点 | 未拆分为 6 独立 Activities | 违反 §1.5 ToolPool 隔离合约 |
| feedback 信号链 | 未走 Temporal HumanGateSignal（P0） | 长流程节点无法被 L1 feedback 驱动 |
| WeldMap 生产实现 | 仅 InMemory，Redis/MinIO+DB 未实现 | 多进程部署不可用 |
| ApprovalStore | 进程内 dict + asyncio.Event | 多进程部署需换 Redis |
| HCA 人工审核 Activity | 未实现 | HumanGate 前端 UI 无后端支撑 |
| Task 工具 / subagent / Planner / Reflector / Supervisor / StateMachine / Checkpoint | **已全部删除**（旧 Brain Orchestration 10 文件 + 旧 TemplateWorkflow FSM 10 文件 + 旧 DDD 分层 port/repo/infrastructure 10 文件 + 旧异常/prompt 7 文件，共 56 文件死代码清理） | 当前 L1 仅 ReActEngine 单路径，无子 agent / 规划 / 反思 / 状态机能力 |
| 节点级回溯 | 不存在（无 rollback/replay 工具） | 工作流失败无法回退到某节点重跑 |
| ToolFailureReflector | 仅 design_workflow 有专用策略，其他工具走 generic | 非 design_workflow 工具的 schema 错误修复能力弱 |
| Guardrails | 默认 WARN 不阻断 | 危险输出可能仍到达用户（需手动调 REJECT 阈值） |
| Evaluator | 无 Langfuse key 时 NoOp 降级 | 生产环境无 Langfuse 则评估数据丢失 |
| LLMResponseCache | 单进程 LRU，不跨进程共享 | 多进程部署缓存命中率低 |
| contracts.py 双份维护 | L3 contracts.py 与 L2 domain/activity.py 内容重复 | 改一处需同步两处，易漂移 |
| 弹窗按钮文案 | 按 tool_name 动态生成（8 工具映射） | 已修复，不再硬编码 |

---

## 七、与通用框架对比

### 7.1 L1 认知平面 vs LangChain / AutoGen / OpenAI Swarm

| 维度 | WeldEvent | LangChain | AutoGen | Swarm |
|------|-----------|-----------|---------|-------|
| ReAct 终止 | LLM 自主 + max_iterations=25 | LLM 自主 | conversation turn | handoff |
| 工具调用 | Function Calling + DSML fallback | Function Calling only | Function Calling | Function Calling |
| Human-in-the-loop | **架构层阻塞**（await event.wait） | system prompt 约束 | conversation turn | 无 |
| Context 管理 | 4 策略 Compaction + Session Notes | 无内置 | 无内置 | 无内置 |
| 失败处理 | 失败反思 + verification gate | 简单 retry | 简单 retry | 无 |
| 工具上限 | 同轮同名 3 次静默 reject | 无 | 无 | 无 |

**核心差异**：WeldEvent 把"通用框架留给 LLM 自己处理"的脆弱点（context 管理、human-in-loop、失败恢复）全部架构层固化。

### 7.2 L2 控制平面 vs Airflow / Prefect / Cadence

| 维度 | WeldEvent | Airflow | Prefect | Cadence |
|------|-----------|---------|---------|---------|
| DAG 执行 | Temporal workflow + 拓扑排序 | DAG 解析 | DAG 解析 | workflow |
| 重试 | 异常分类 + retry_policy | 简单 retry | 简单 retry | retry |
| Human gate | **双重校验**（L1 + L2） | 无 | 无 | signal |
| 状态传递 | **WeldMap 黑板** + dependency_results | XCom | task input | 无 |
| 可观测 | EventLog + Langfuse | logs | logs | logs |

**核心差异**：WeldEvent 的 WeldMap 黑板 + 双重 HumanGate 是工业质检场景特化，通用框架无对应。

### 7.3 L3 执行平面 vs 传统 CV pipeline

| 维度 | WeldEvent | 传统 CV pipeline |
|------|-----------|------------------|
| 算法集成 | Activity 化，每个算法独立 | 脚本式，算法耦合 |
| 状态传递 | WeldMap 黑板（CAS + Event Sourcing） | 文件 / 全局变量 |
| 可扩展 | capability 别名 + ActivityPool 分派 | 改脚本 |
| 失败恢复 | retry_policy + 节点级 | 全流程重跑 |
| 标注集成 | Label Studio MCP 10 工具 | 外部工具，无集成 |

**核心差异**：WeldEvent 把 CV 算法从脚本变成 Activity，配合 WeldMap 黑板实现跨算法状态传递和失败恢复。

---

## 八、整体评价

### 8.1 核心判断

WeldEvent 是**工业场景 specialization，非通用 Agent 框架**。算法层面无突破性创新，但通过大量工程化设计补强了通用 ReAct / Temporal / CV pipeline 的脆弱点。

### 8.2 成熟度评级

| 层 | 模块 | 成熟度 |
|---|------|--------|
| L1 | ReActEngine 主循环 | ★★★★☆ complete（19 工具 Session Notes + ID 持久化 + ToolFailureReflector） |
| L1 | AgentLoop + WebSocket | ★★★★☆ complete（session 持久化对齐 HTTP 路径 + Guardrails 透传） |
| L1 | ToolRegistry + 工具集 | ★★★★☆ complete（14 L1 工具 + control_workflow） |
| L1 | 前端 chat.html | ★★★★☆ complete（弹窗动态文案 8 工具映射 + WS 优先） |
| L1 | 三层 Guardrails | ★★★★☆ complete（AfterTool + Output + 5 条焊接规则） |
| L1 | LLM-as-Judge 评估 | ★★★☆☆ partial（4 维度 + GOLDEN_SET 4 用例，需扩展） |
| L1 | LLMResponseCache | ★★★☆☆ partial（单进程 LRU，不跨进程） |
| L1 | ToolFailureReflector | ★★★☆☆ partial（仅 design_workflow 专用策略） |
| L1 | WorkflowObserver + EventBus | ★★★★☆ complete（publish hooks + 历史缓存 + summary 注入） |
| L2 | Temporal workflow runner | ★★★☆☆ partial（feedback 信号链 P0 待修） |
| L3 | IQA / PPA / Annotation | ★★★★☆ complete |
| L3 | 其他 6 个 Activity | ★☆☆☆☆ mock |
| L3 | WeldMap 黑板 | ★★★☆☆ partial（仅 InMemory） |
| L3 | Activity 契约 contracts.py | ★★★★☆ complete（双份维护技术债） |
| 跨层 | L1→L2 桥接 | ★★★☆☆ partial（默认 Null adapter） |
| 跨层 | shared/ 共享层 | ★★★★☆ complete（mcp + labelstudio 传输层共享） |

### 8.3 架构哲学总结

1. **架构替 LLM 做看不见的事** — ApprovalGate、Context Compaction、Session Notes、DSML fallback、同轮工具上限、ToolFailureReflector，全部架构层固化，不依赖 LLM 自觉。
2. **三平面 specialization** — L1 推理 / L2 编排 / L3 执行各司其职，跨层通过 WeldMap + Temporal signal 通信。
3. **boundary-pinning 边界钉死** — L1 不直接执行工业 verb，L2 不直接调 LLM，L3 不直接暴露给用户；L1 与 L3 共享 `shared/` 传输层但不共享业务封装。
4. **MCP 协议化工具层** — Label Studio 通过 MCP 协议接入，L1 通过 ToolPolicy 分级控制可见性；L1 adapters/mcp 适配 shared HTTPMCPClient。
5. **双路径统一治理** — HTTP /stream 与 WS /ws 共享 hooks/skill_registry/approval_store/compactor/guardrails；session 持久化通过 `_helpers.py` 共享函数消除重复，治理一致。
6. **三层 Guardrails 防护** — AfterToolHook + OutputGuardrail + 焊接领域规则，默认 WARN 不阻断。
7. **LLM-as-Judge 闭环** — 4 维度评分 + GOLDEN_SET 回归 + Langfuse 挂载，无 Langfuse 时 NoOp 降级。

---
