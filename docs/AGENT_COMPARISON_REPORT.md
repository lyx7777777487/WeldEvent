# Agent 先进思想借鉴报告 — trae-agent / Codex / Claude Code vs WeldEvent

> 基于对三个开源/文档化 agent 的实际源码与官方文档阅读，严谨对照 WeldEvent 当前代码状态，输出可落地的借鉴清单。
> 对照日期：2026-07-08（v2 修订：补充深度源码阅读 + AgentLoop 双 task 对比维度）
> 对照基线：WeldEvent `cognitiveplane/` + `controlplane/` + `executionplane/` 当前 HEAD


---

## 一、三 Agent 先进思想提炼

### 1.1 trae-agent（ByteDance）— 研究友好型 SE Agent

| 思想 | 源码位置 | 核心机制 |
|------|---------|---------|
| **显式五状态机** | `agent_basics.py:AgentStepState` | THINKING / CALLING_TOOL / REFLECTING / COMPLETED / ERROR，每步显式状态转移 |
| **每步即时反思** | `base_agent.py:_tool_call_handler` 末尾 `reflect_on_result()` | 工具执行后立即生成反思消息注入 messages，不等失败累积 |
| **parallel_tool_calls** | `base_agent.py:276-279` | 配置开关，`parallel_tool_call` vs `sequential_tool_call` 二选一 |
| **TrajectoryRecorder** | `base_agent.py:_record_handler` + `agent_basics.py:AgentExecution/AgentStep` | per-step 结构化轨迹：step_number/state/thought/tool_calls/tool_results/llm_response/reflection/error/llm_usage |
| **task_done + 二次验证** | `trae_agent.py:196-209` | `llm_indicates_task_completed` 检查 tool_calls 含 task_done + `_is_task_completed` 验证 patch 非空（`must_patch=="true"` 时 get_git_diff + remove_patches_to_tests 过滤测试目录）→ 失败注入 `task_incomplete_message` |
| **sequentialthinking 工具** | `tools/sequential_thinking_tool.py` (278行) | ThoughtData（thought/thought_number/total_thoughts/next_thought_needed/is_revision/revises_thought/branch_from_thought/branch_id/needs_more_thoughts）+ thought_history[] + branches{} + total_thoughts 可动态加步 |
| **Docker 沙箱** | `DockerManager` + `DockerToolExecutor` | bash/edit/json_edit 走容器执行 |
| **MCP YAML 声明式** | `trae_agent.py:44-51` + `discover_mcp_tools()` (61-89) | `mcp_servers_config` dict + `allow_mcp_servers` list 白名单 + `connect_and_discover` 运行时拉取 + 失败 client 清理 |
| **max_steps 可配置** | `agent_config.max_steps`（默认 200） | CLI 参数，非硬编码 |
| **Lakeview 步骤摘要** | docs | 每步生成简洁人友好摘要给用户，非原始 token 流 |
| **显式 7 步方法论 system prompt** | `prompt/agent_prompt.py:TRAE_AGENT_SYSTEM_PROMPT` | 7 步引导：Understand→Explore→**Reproduce（Crucial Step）**→Debug→Fix→Verify（含新测试+边界）→Summarize；要求 absolute path 组合；引导用 sequentialthinking（total_thoughts 5-25）；用 task_done 收尾 |
| **new_task 显式构建 messages** | `trae_agent.py:90-133` | system prompt + user_message（[Project root path] + [Problem statement] + optional base_commit/must_patch/patch_path）+ start_recording |
| **reflect_on_result 可覆盖** | `trae_agent.py:151-152` override 为 `return None` | TraeAgent 禁用默认反思，靠 task_done 二次验证替代 |
| **resource cleanup** | `base_agent.py:_close_tools` + `cleanup_mcp_clients` | 执行结束显式释放工具资源（BashTool）+ MCP client 防异步 context 泄漏 |
| **CLIConsole 状态台** | `base_agent.py:_update_cli_console` | 每步 update_status(step, execution) 实时显示 |

### 1.2 Codex（OpenAI）— 工业级多模态 Agent

| 思想 | 源码位置 | 核心机制 |
|------|---------|---------|
| **SQ/EQ 双队列协议** | `protocol/src/protocol.rs` | Submission Queue (Op 枚举) + Event Queue (Event 枚举)，统一前后端契约 |
| **CodexThread 线程模型** | `core/src/codex_thread.rs:149-303` | `submit(op)` + `steer_input(input, additional_context, expected_turn_id, client_user_message_id, responseapi_client_metadata)` 运行中转向 + `inject_if_running(items)` 运行中注入 model-visible items（无活跃 turn 则原样返回） + `try_start_turn_if_idle(items)` idle 时自动启动 turn（含 TryStartTurnIfIdleRejectionReason 稳定拒绝原因）+ `set_thread_memory_mode(mode)` 持久化记忆资格 + `forked_from_thread_id` 父子 fork |
| **CodexThreadSettingsOverrides** | `codex_thread.rs:131-148` | 14 个可覆盖设置：environments/workspace_roots/approval_policy/approvals_reviewer/sandbox_policy/permission_profile/active_permission_profile/windows_sandbox_level/model/effort/summary/service_tier/collaboration_mode/personality；`preview_thread_settings_overrides` 不提交预览 |
| **ModelClientSession turn-scoped** | `core/src/client.rs:252-266` | per-turn session 缓存 WebSocket + `turn_state: OnceLock<String>` sticky routing token（turn 开始从响应头收，同 turn 内请求回放，不同 turn 禁止复用）+ `last_request` 增量请求复用判定 |
| **WebSocket prewarm v2** | `client.rs` doc + `WebsocketSession` | `response.create` with `generate=false` 预热连接，下个请求复用 same connection + `previous_response_id`；prewarm 失败走正常 retry/fallback；`RESPONSES_WEBSOCKETS_V2_BETA_HEADER_VALUE="responses_websockets=2026-02-06"` |
| **transport fallback session-scoped** | `client.rs:228` doc | 一个 turn 激活 HTTP fallback 后，同 session 后续 turn 都用 HTTP |
| **ModelClientState session-scoped** | `client.rs:186-202` | thread_id + provider + auth_env_telemetry + session_source + originator + enable_request_compression + include_attestation + attestation_provider + disable_websockets(AtomicBool) + cached_websocket_session(StdMutex) |
| **prompt_cache_key_override** | `client.rs:237` | ModelClient 字段，允许覆盖 prompt cache key |
| **MemoriesClient 服务端记忆** | `client.rs:154` `MEMORIES_SUMMARIZE_ENDPOINT="/memories/trace_summarize"` | 服务端 trace 总结记忆 |
| **CompactClient 服务端 compaction** | `client.rs:150` `RESPONSES_COMPACT_ENDPOINT="/responses/compact"` + `COMPACT_REQUEST_TIMEOUT_IDLE_MULTIPLIER=4` | 服务端压缩，unary 超时倍数 4 |
| **子 Agent 系统** | `core/src/agent/` | 96 预置 agent 名字池 (`agent_names.txt`) + TOML 声明（`builtins/awaiter.toml` + `explorer.toml`：model_reasoning_effort + developer_instructions + behavior rules）+ `agent_resolver.rs` resolve_agent_target 解析引用 + SubAgentSource/MultiAgentVersion/MultiAgentMode 协议级多 agent + register_session_root 父子注册 |
| **AGENTS.md 项目文档发现** | `core/src/agents_md.rs` (16KB) | 从 cwd 向上找 .git marker 定位 root → 收集路径上所有 AGENTS.md + AGENTS.override.md → project_doc_max_bytes 预算 → InstructionProvenance(Project/Internal) 溯源 → 多环境标签 |
| **Guardian 审批分级** | `core/src/codex_delegate.rs` (33KB) | GuardianApprovalRequest + GuardianRiskLevel + GuardianUserAuthorization + routes_approval_to_guardian + spawn_approval_request_review 异步审批 + MCP per-tool approval（MCP_TOOL_APPROVAL_ACCEPT/_FOR_SESSION/_DECLINE_SYNTHETIC）+ ExecApprovalRequestEvent + ApplyPatchApprovalRequestEvent + ElicitationRequestEvent |
| **沙箱三平台** | `codex-rs/exec/` | macOS Seatbelt / Linux Landlock+bubblewrap / Windows elevated token + FileSystemSandboxPolicy + NetworkSandboxPolicy + .git 只读 + SandboxEnforcement 协议级 |
| **Rollout + StateDb** | `codex_rollout_trace` crate + `codex_thread.rs:222-228` | InferenceTraceContext + CompactionTraceContext + StateDbHandle + StoredThread + StoredThreadHistory + ThreadMetadataPatch + `ensure_rollout_materialized()` + `flush_rollout()` |
| **Attestation 请求签名** | `client.rs:197-198` `include_attestation` + `attestation_provider: Option<Arc<dyn AttestationProvider>>` | 请求签名验证 |
| **Realtime WebRTC** | `client.rs` + protocol | 语音对话 + 音频帧 |
| **thread_lifecycle_contributors** | `codex_thread.rs:199-220` `emit_thread_resume_lifecycle` + `emit_thread_idle_lifecycle_if_idle` | 扩展点：thread resume/idle 时触发 contributor（session_store + thread_store 注入） |
| **out_of_band_elicitations** | `codex_thread.rs:154-160` `OutOfBandElicitations{count, registration}` | 带外主动索取输入计数 + 注册 |
| **W3cTraceContext** | `codex_thread.rs:229-235` `submit_with_trace(op, trace)` | W3C trace context 传播，分布式追踪 |

### 1.3 Claude Code（Anthropic）— 团队协作型 Agent

| 思想 | 文档/源码位置 | 核心机制 |
|------|---------|---------|
| **四种并行模式** | docs/en/agents | Subagents（单会话内）/ Agent view（独立会话后台）/ Agent teams（lead+teammates 共享 task list）/ Dynamic workflows（JS 脚本编排） |
| **声明式 Subagent** | docs/en/sub-agents | Markdown + YAML frontmatter（name/description/tools/model/permissionMode/mcpServers/hooks/maxTurns/skills/memory/effort/background/isolation）+ 5 级 scope 优先级 + builtins（Explore/Plan/general-purpose）+ fork 继承 context |
| **Dynamic Workflows** | docs/en/workflows | JS 脚本编排 dozens-hundreds subagent + 阶段化 + 对抗式交叉验证 + 可恢复（cached 结果）+ 可保存为命令 + ultracode 模式 + 16 并发/1000 总量限制 |
| **30+ Hook 事件** | docs/en/hooks | SessionStart/End + PreToolUse/PostToolUse/PostToolUseFailure + PermissionDenied + SubagentStart/Stop + TaskCreated/Completed + Stop/StopFailure + TeammateIdle + ConfigChange/CwdChanged/FileChanged + WorktreeCreate/Remove + PreCompact/PostCompact + Elicitation/ElicitationResult；3 种 handler（Command/HTTP/Prompt-Agent）；async 后台 |
| **Skills 系统** | docs/en/skills | SKILL.md + YAML frontmatter + 动态 context 注入（`` !`cmd` ``）+ 4 级 scope + live change detection + nested discovery + compaction 后重注入（5000 tokens/skill, 25000 total） |
| **CLAUDE.md 层级记忆** | docs/en/memory + claude-directory | root→cwd 路径所有 CLAUDE.md + CLAUDE.local.md(gitignore) + AGENTS.md 兼容 + `.claude/rules/` path-scoped + per-agent MEMORY.md |
| **Context 可视化** | docs/en/context-window | `/context` 按类别显示 token 占用 + `/compact focus on X` 定向压缩 + `/clear` 切任务 + compaction 后重注入表 |
| **5 级 Permission Mode** | docs/en/permission-modes | Default / Accept edits / Auto / Bypass / Plan mode（只读研究） |
| **worktree 隔离** | docs/en/worktrees | 并行 session 各自 git checkout + .worktreeinclude 控制 |
| **Plugin marketplace** | docs/en/plugins + `plugins/` 目录源码 | 组件打包 + scoped name + agents/hooks/skills/MCP 打包分发；仓库内已含 10+ 官方 plugin（agent-sdk-dev/claude-opus-4-5-migration/code-review/commit-commands/explanatory-output-style/feature-dev/frontend-design/...） |
| **feature-dev 7-phase workflow** | `plugins/feature-dev/README.md` (310行完整) | 7 阶段：Discovery→Codebase Exploration（2-3 code-explorer 并行）→Clarifying Questions（等用户答）→Architecture Design（3 code-architect 并行出 minimal/clean/pragmatic 三方案让用户选）→Implementation（等批准）→Quality Review（3 code-reviewer 并行按 simplicity/bugs/conventions 三 focus）→Summary |
| **3 种专用 subagent** | `plugins/feature-dev/README.md` Agents 节 | **code-explorer**（trace 执行路径+数据流+架构层，输出 entry points with file:line）+ **code-architect**（pattern 分析+架构决策+组件设计+实现 roadmap+build sequence）+ **code-reviewer**（CLAUDE.md 合规+bug 检测+code quality，confidence-based filtering 只报 ≥80 高置信度问题，分 75-100 critical / 50-74 important） |
| **对抗式交叉验证** | `plugins/feature-dev/README.md` Phase 6 | 3 个 reviewer 不同 focus 并行审查，consolidate 后按 severity 分级，用户决定 fix now/fix later/proceed as-is |
| **/feature-dev 命令** | `plugins/feature-dev/commands/` | `/feature-dev <description>` 或 `/feature-dev` 交互式引导 |

---

## 二、WeldEvent 当前状态严谨核实

### 2.1 已确认存在且完整的能力

| 能力 | 文件 | 状态 |
|------|------|------|
| ReActEngine 核心循环 | `cognitiveplane/control/react.py` (~2400行) | complete，max_iterations=25 |
| ApprovalGate 架构阻塞 | `react.py` 6 写入工具 `await event.wait(300s)` | complete |
| Context Compaction 4 策略 | `cognitiveplane/memory/compaction.py` | complete，SELF_COMPACT_SLIDING 默认 |
| Session Notes 19 工具 | `react.py:_derive_note` | complete，FIFO 8 条 |
| SAME_TOOL_LIMIT=3 静默 reject | `react.py` | complete |
| DSML fallback 解析 | `react.py` | complete |
| ToolFailureReflector | `react.py` | complete，schema 反射 |
| LLMResponseCache | `react.py` | complete，temp==0 缓存 |
| AgentLoop WebSocket | `cognitiveplane/control/agent_loop.py` (519行) | complete，双 task + session 持久化 |
| ToolRegistry 14 工具 | `cognitiveplane/control/tool_registry.py` | complete，phase=3 过滤 |
| MCP 接入 | `cognitiveplane/control/mcp_registry.py` + `adapters/mcp/` | complete，10 标注工具 |
| upload_image_to_dataset L1 包装 | `cognitiveplane/control/tools/upload_image_to_dataset.py` | complete |
| 三层 Guardrails | `cognitiveplane/governance/guardrails.py` | complete |
| LLM-as-Judge 评估 | `cognitiveplane/governance/evaluation.py` | complete |
| Skill Registry | `cognitiveplane/control/skills.py` | complete，代码内构建 |
| Memory 系统 | `cognitiveplane/memory/` (19 文件) | complete，WritePort + retrieval |
| ImageStore 双轨 | `cognitiveplane/interaction/image_store.py` | complete |
| EventLog 13 类 | `cognitiveplane/control/event_log.py` | complete |
| WorkflowEventBus | `cognitiveplane/interaction/workflow_events.py` | complete，L2→L1 注入 |
| Temporal DAG | `controlplane/` | complete，拓扑排序 + HumanGate 双重 |
| WeldMap 黑板 | `executionplane/weldmap/` | complete，CAS + Event Sourcing |
| 9 Activity Pool | `executionplane/pool.py` | 3/9 真实（IQA/PPA/Annotation），6 mock |
| 前端 WebSocket | `cognitiveplane/static/chat.html` (3335行) | complete，WS+HTTP fallback + 动态文案弹窗 |

### 2.2 已确认缺失或仅 Port 接口的能力

| 能力 | 文件状态 | 证据 |
|------|---------|------|
| **子 Agent 委派** | 仅 Port 接口 | `cognitiveplane/control/ports.py` 有 `SubAgentDelegationPort(ABC)` / `PlanningPort(ABC)` / `ReflectionPort(ABC)` / `ReasoningPort(ABC)` 定义，**无任何实现类** |
| **planner/reflector/sub_agent/supervisor** | 文件不存在 | Glob `cognitiveplane/control/{planner,reflector,sub_agent,supervisor,state_machine,checkpoint}*.py` 返回 No file found |
| **controlplane/port/ + infrastructure/ + repo/** | 目录不存在 | Glob 返回 No file found |
| **节点级回溯** | 不存在 | 无 rollback/replay 工具，无 checkpoint.py |
| **Hook 事件覆盖** | 仅 BeforeToolHook | `cognitiveplane/control/hooks.py` 只有 `SafetyHook(BeforeToolHook)` + `PolicyHook(BeforeToolHook)`，无 SessionStart/End、PreCompact/PostCompact、SubagentStart/Stop、FileChanged、Elicitation 等 |
| **声明式 Skill 文件** | 不存在 | `skills.py` 是代码内 `build_welding_skill_registry()`，无 SKILL.md 文件机制 |
| **CLAUDE.md / AGENTS.md 发现** | 不存在 | 无项目文档自动发现机制 |
| **沙箱** | 不存在 | L3 Activity 主进程执行，无 Seatbelt/Landlock/bwrap |
| **Trajectory 结构化导出** | 不存在 | EventLog 有事件但无 per-step AgentExecution 结构化对象（你之前导出对话日志失败就是这个） |
| **parallel_tool_calls** | 不存在 | react.py 严格串行，一次一工具 |
| **task_done 二次验证** | 不存在 | 靠 LLM 自主 finish，无架构验证 |
| **Permission mode 分级** | 不存在 | ApprovalGate 单层，无 default/acceptEdits/auto/bypass/plan 分级 |
| **worktree 隔离** | 不存在 | 单工作目录 |
| **Dynamic workflows 脚本编排** | 不存在 | L2 Temporal DAG 是工作流编排，但 L1 层无 subagent 脚本编排 |
| **服务端 compact / memories** | 不存在 | 仅本地 compaction |

---

## 三、核心维度逐项对比

### 3.1 会话生命周期与运行中干预（P1 重要差距，WeldEvent 有基础但缺 steer/inject）

| 维度 | trae-agent | Codex | Claude Code | WeldEvent |
|------|-----------|-------|-------------|-----------|
| 会话模型 | `execute_task()` 同步循环 | **CodexThread** 双队列 submit/next_event | session + subagent fork | **AgentLoop 双 task**（receive_task + react_task） |
| 运行中转向 | 无 | **steer_input()** 不中断当前 turn 注入输入 | 无 | 无（只有 interrupt 全中断） |
| 运行中注入 | 无 | **inject_if_running()** 向活跃 turn 注入 model-visible items | 无 | 无 |
| idle 自动启动 | 无 | **try_start_turn_if_idle()** 含拒绝原因 | 无 | 无 |
| 父子 fork | 无 | forked_from_thread_id + register_session_root | fork subagent 继承 context | 无 |
| 中断 | 无 | cancel | 无 | `_handle_interrupt` → `react_task.cancel()` |
| 反馈 | 无 | steer 即反馈 | feedback 机制 | `_handle_feedback` → MemoryWritePort + `_feedback_queue` |
| session 持久化 | 无 | StoredThread + StateDb | transcripts | `_prepare_session_context` + `_persist_session` |
| 设置覆盖 | 无 | **CodexThreadSettingsOverrides** 14 字段 preview/commit | permission mode 切换 | 无 |
| 生命周期扩展点 | 无 | **thread_lifecycle_contributors** resume/idle 触发 | hooks | hooks（仅 BeforeTool） |
| 分布式追踪 | 无 | **W3cTraceContext** submit_with_trace | 无 | Langfuse trace |
| 带外索取 | 无 | **out_of_band_elicitations** | Elicitation hook | request_confirmation |

**分析**：WeldEvent 的 AgentLoop 双 task（`receive_task` 消费 `_incoming` queue 分发 chat/feedback/interrupt + `react_task` 按需启动跑 `run_stream()`，同一时刻仅一个）是**比 trae-agent 和 Claude Code 都强的**——它支持 WebSocket 连接级生命周期管理、中断、反馈队列。但与 Codex 的 CodexThread 相比缺三个关键能力：

1. **steer_input（运行中转向）**— Codex 能在不中断当前 turn 的情况下注入新输入，WeldEvent 只能全中断重来。工业场景长工作流（IQA+PPA+标注）中途用户想加图或改参数，全中断代价大。
2. **inject_if_running（运行中注入）**— Codex 能向活跃 turn 注入 model-visible items（如新图片引用、新标准条款），WeldEvent 的反馈只能延迟到下一轮。
3. **try_start_turn_if_idle（idle 自动启动）**— Codex 的扩展点能在 thread idle 时自动启动 turn（如 WorkflowEventBus 检测到 L2 工作流完成自动启动总结 turn），WeldEvent 无此机制。

**WeldEvent 独特优势**：AgentLoop 的 `tool_registry` 透传 + `session_manager/image_sessions/router` 三参数持久化是 Codex 没有的工业场景特化——确保标注 MCP 工具可见 + 跨轮不丢图片记忆。

**借鉴建议**：
- **steer_input** — AgentLoop 加 `_steer_queue`，`_handle_steer` 不 cancel react_task 而是 await inject 点注入 messages
- **inject_if_running** — ReActEngine 暴露 `inject_items(items)` 方法，在下一轮 LLM 调用前 extend messages
- **try_start_turn_if_idle** — WorkflowEventBus 检测到 L2 工作流完成时，调 AgentLoop.try_start_turn_if_idle 自动启动总结 turn
- **CodexThreadSettingsOverrides** — AgentLoop 加 `preview_settings_overrides`，支持运行时切 model/effort/permission_mode

### 3.2 子 Agent 与并行编排（P0 致命差距）

| 维度 | trae-agent | Codex | Claude Code | WeldEvent |
|------|-----------|-------|-------------|-----------|
| 子 Agent 定义 | 无 | TOML 声明 + 96 名字池 + builtins/ | SKILL.md + YAML frontmatter + 5 级 scope | **仅 Port 接口，零实现** |
| 并行模式 | 无 | fork 父子线程 | 4 种（subagents/view/teams/workflows） | **无** |
| 编排脚本 | 无 | 无 | Dynamic workflows JS + 对抗验证 | L2 Temporal DAG（不同层） |
| fork 继承 context | 无 | forked_from_thread_id | fork subagent 继承完整 context | **无** |
| background 模式 | 无 | 无 | `background: true` 后台跑 | **无** |

**分析**：这是 WeldEvent 与三个 agent 的**最大差距**。Codex 和 Claude Code 都把"声明式子 agent"作为核心能力——用 Markdown/TOML 文件定义一个有独立 system prompt、独立工具集、独立 model、独立 permission 的子 agent，主 agent 按需委派。WeldEvent 的 `SubAgentDelegationPort` 接口早在 ports.py 定义了，但**从未实现**。

**WeldEvent 独特优势**：L2 Temporal DAG + WeldMap 黑板是三个通用 agent 都没有的工业级编排能力。但这是**工作流编排**（节点级 DAG），不是**认知编排**（subagent 委派）。两者互补，不替代。

**借鉴建议**：
- 复用现有 ReActEngine 做 subagent runner（一个 subagent = 一个独立 ReActEngine 实例 + 独立 session + 独立 tool_registry 子集）
- 采用 Claude Code 的 SKILL.md 声明式定义（Markdown + YAML frontmatter），存在 `.weldevent/agents/` 目录
- 5 级 scope 优先级：Managed > CLI > Project(`.weldevent/agents/`) > User(`~/.weldevent/agents/`) > Plugin
- 内置 subagent：`explorer`（只读搜索）、`planner`（工作流设计）、`annotator`（标注专家）
- 工具集隔离：subagent 的 `allowed-tools` 控制，主 agent 的 14 工具按需子集分配

### 3.3 项目文档与记忆系统（P0 致命差距）

| 维度 | Codex | Claude Code | WeldEvent |
|------|-------|-------------|-----------|
| 项目文档发现 | `agents_md.rs` 16KB，cwd 向上找 .git → 收集 AGENTS.md | CLAUDE.md 层级 + AGENTS.md 兼容 + `.claude/rules/` path-scoped | **无** |
| per-agent 记忆 | 无 | `~/.claude/agents/<name>/MEMORY.md` | **无** |
| compaction 后重注入 | 无 | 明确重注入表（system prompt/CLAUDE.md/auto memory 重注入；path-rules 丢失直到再触发） | **无明确机制** |
| 跨会话记忆 | MemoriesClient 服务端 | auto memory 磁盘持久 | Memory 系统仅单会话 |

**分析**：WeldEvent 的 `project_memory.md` 是 **Trae IDE 维护的**，不是 WeldEvent 自己的。服务重启后 WeldEvent 不知道自己的项目约定。

**借鉴建议**：
- 实现 `WELDEVENT.md` 发现：从 cwd 向上找 `.git` 或 `pyproject.toml` 定位 root → 收集路径上所有 `WELDEVENT.md` → 注入 system prompt
- 预算控制：`project_doc_max_bytes=8192`
- path-scoped rules：`.weldevent/rules/*.md` 带 `paths:` frontmatter，文件触发时加载
- compaction 后重注入表：明确 system_prompt / WELDEVENT.md / skill bodies 重注入，path-rules 丢失

### 3.4 Hooks 事件系统（P1 重要差距）

| 维度 | Claude Code | trae-agent | WeldEvent |
|------|-------------|-----------|-----------|
| 事件数量 | 30+ | 无 | 2（BeforeTool only） |
| 事件类型 | Session/Tool/Permission/Subagent/Task/Stop/Config/Cwd/File/Worktree/Compact/Elicitation | 无 | PreToolUse only |
| Handler 类型 | Command(shell) / HTTP / Prompt(LLM) / Agent(subagent) | 无 | Python class |
| async 后台 | 支持 | 无 | 不支持 |
| Elicitation | Elicitation + ElicitationResult 事件 | 无 | request_confirmation 工具（类似但非 hook） |

**分析**：WeldEvent 的 `hooks.py` 只有 51 行，仅 `SafetyHook` + `PolicyHook` 两个 BeforeToolHook。Claude Code 的 30+ 事件覆盖了完整生命周期。

**借鉴建议**（按优先级）：
1. **PreCompact / PostCompact** — 压缩前后触发，可保存关键 context
2. **SubagentStart / SubagentStop** — 配合子 agent 系统实现
3. **SessionStart / SessionEnd** — 会话级初始化/清理
4. **FileChanged** — 文件变更触发，可自动重载 skill/agent 定义
5. **Elicitation** — 主动向用户索取输入（比 request_confirmation 更系统化）
6. Handler 扩展：支持 shell command + HTTP webhook，不只 Python class

### 3.5 Context 管理（P1 重要差距）

| 维度 | Claude Code | Codex | trae-agent | WeldEvent |
|------|-------------|-------|-----------|-----------|
| 策略数 | `/compact` + `/clear` + focus 定向 | 服务端 `/responses/compact` | 无 | **4 策略（最丰富）** |
| 可视化 | `/context` 按类别 token 占用 | 无 | 无 | **无** |
| 定向压缩 | `/compact focus on X` | 无 | 无 | **无** |
| compaction 后重注入 | 明确重注入表 | 无 | 无 | **无明确表** |
| subagent 隔离大读 | 是 | 是 | 无 | **无 subagent** |
| 1M token 扩展 | Opus/Sonnet 4.6 `[1m]` | 无 | 无 | 无（受模型限制） |

**分析**：WeldEvent 的 4 策略 Compaction 是**比三个 agent 都强的**（SELF_COMPACT_SLIDING 借鉴 Anthropic Context Engineering）。但缺可视化和定向压缩。

**借鉴建议**：
1. **`/context` 端点** — 按 system/history/tools/skills 分类显示 token 占用，前端加可视化
2. **focus 定向压缩** — CompactionStrategy 加 `focus_hint` 参数，`/compact focus on 标注流程` 保留指定主题
3. **compaction 后重注入表** — 明确 system_prompt / WELDEVENT.md / current_plan / session_notes 重注入，其他丢失

### 3.6 Trajectory 与可观测（P1 重要差距）

| 维度 | trae-agent | Codex | Claude Code | WeldEvent |
|------|-----------|-------|-------------|-----------|
| 结构化轨迹 | TrajectoryRecorder per-step | Rollout + StateDb + StoredThread | transcripts | EventLog 13 类 |
| 字段 | step/state/messages/response/tool_calls/results/reflection/error | InferenceTraceContext + CompactionTraceContext | 无公开 | event_type + payload |
| 持久化 | JSON 文件 | StateDbHandle | ~/.claude/projects/ | 进程内 |
| 导出 | `--trajectory-file` | flush_rollout() | 无 | **失败**（你之前踩坑） |
| 恢复 | 无 | ensure_rollout_materialized() | 无 | 无 |

**分析**：WeldEvent 的 EventLog 是**架构层事件溯源**（BrainEventType 13 类），不是**LLM 推理轨迹**。两者维度不同。你之前导出对话日志失败，就是因为缺 per-step LLM 推理轨迹。

**借鉴建议**：
- 新建 `cognitiveplane/control/trajectory.py`
- `AgentExecution` dataclass：task / steps[] / final_result / success / total_tokens / execution_time
- `AgentStep` dataclass：step_number / state / llm_messages / llm_response / tool_calls / tool_results / reflection / error / timestamp
- ReActEngine 每轮 append step，流结束后 `finalize_recording()`
- 导出端点 `GET /sessions/{id}/trajectory` 返回 JSON（与现有 `/sessions/{id}/export` 互补）

### 3.7 沙箱与安全（P1 重要差距）

| 维度 | Codex | Claude Code | trae-agent | WeldEvent |
|------|-------|-------------|-----------|-----------|
| 沙箱 | Seatbelt/Landlock/bwrap/Win token | worktree + permission mode | Docker | **无** |
| 文件策略 | FileSystemSandboxPolicy | .worktreeinclude | 容器内 | 无 |
| 网络策略 | NetworkSandboxPolicy | 无 | 容器内 | 无 |
| .git 保护 | 强制只读 | worktree 隔离 | 容器内 | 无 |
| Attestation | 请求签名 | 无 | 无 | 无 |

**分析**：WeldEvent 当前 L3 Activity 在主进程执行 numpy/cv2 算法，无外部代码执行需求，**短期安全风险低**。但如果未来要跑用户上传的算法脚本或第三方 MCP，必须有沙箱。

**借鉴建议**（按场景需要）：
- 短期：L3 不跑外部代码，可不补
- 中期：如果接入第三方算法脚本，用 Docker（trae-agent 方案）最简单
- 长期：多进程部署时 ApprovalStore + SessionManager 要换 Redis（已在技术债里）

### 3.8 审批与权限（P2 增强项）

| 维度 | Codex | Claude Code | trae-agent | WeldEvent |
|------|-------|-------------|-----------|-----------|
| 机制 | Guardian risk level + reviewer 路由 | 5 级 permission mode | 无 | ApprovalGate `await event.wait()` |
| 分级 | GuardianRiskLevel | Default/AcceptEdits/Auto/Bypass/Plan | 无 | **单层** |
| 阻塞方式 | 异步审批 | 非阻塞问询 | 无 | **架构层强制阻塞**（最强） |
| MCP per-tool approval | 是 | 是 | 无 | 是（phase_override） |

**分析**：WeldEvent 的 ApprovalGate 是**比三个 agent 都强的**——`await event.wait(300s)` 架构层强制阻塞，LLM 完全无法绕过。Claude Code 的 permission mode 是非阻塞问询，Codex 的 Guardian 是异步审批。但 WeldEvent 缺分级。

**借鉴建议**：
- ApprovalStore 加 `risk_level` 字段：low（自动通过）/ medium（提示用户）/ high（强制阻塞）
- 5 级 permission mode：Default / AcceptEdits / Auto / Bypass / Plan（只读研究模式）
- Plan mode 特别有用：复杂工作流设计前先只读研究，出 plan 后再执行

### 3.9 工具调用机制（P2 增强项）

| 维度 | trae-agent | Codex | Claude Code | WeldEvent |
|------|-----------|-------|-------------|-----------|
| 并行调用 | parallel_tool_calls 配置 | 是 | 是 | **串行** |
| 同轮上限 | 无 | 无 | 无 | **SAME_TOOL_LIMIT=3 静默 reject**（独有） |
| 失败反思 | 每步即时 | 无 | PostToolUseFailure hook | **fail_counts ≥2 次触发**（延迟） |
| schema 反射 | 无 | 无 | 无 | **ToolFailureReflector**（独有） |
| task_done 验证 | 二次验证 | 无 | 无 | **无** |
| sequentialthinking | 278行完整实现 | 无 | 无 | **无** |

**分析**：WeldEvent 的 SAME_TOOL_LIMIT + ToolFailureReflector 是**三个 agent 都没有的独有优势**。但缺并行调用和 task_done 验证。

**借鉴建议**：
1. **parallel_tool_calls** — ToolExecutor 加 `parallel_tool_call` 分支，`asyncio.gather` 并行独立工具，多图分析场景提速
2. **task_done 二次验证** — 工作流场景：LLM 说完成 → 架构验证 WeldMap 是否真有结果 → 失败则继续
3. **即时反思** — 把 fail_counts 的"≥2 次"门槛降到"每次失败都反思"，但保留"按工具+错误类型生成针对性提示"的智能性
4. **sequentialthinking 工具** — 复杂工作流设计前让 LLM 先"显式思考"，thought_history 可回溯

### 3.10 Skills 系统（P2 增强项）

| 维度 | Claude Code | WeldEvent |
|------|-------------|-----------|
| 定义方式 | SKILL.md + YAML frontmatter | 代码内 `build_welding_skill_registry()` |
| 动态 context 注入 | `` !`cmd` `` 命令输出内联 | 无 |
| scope | 4 级（Enterprise/Personal/Project/Plugin） | 1 级（代码内） |
| live reload | 文件改即时生效 | 无 |
| nested discovery | monorepo 子目录按需加载 | 无 |
| compaction 后重注入 | 是（5000/skill, 25000 total） | 无明确机制 |

**分析**：WeldEvent 的 skill 是代码内硬编码，加新 skill 要改代码重新部署。

**借鉴建议**：
- 新建 `.weldevent/skills/<name>/SKILL.md` 机制
- YAML frontmatter：name / description / when_to_use / allowed-tools / disable-model-invocation
- 动态 context 注入：`` !`weldmap read workflow://{id}` `` 内联 WeldMap 状态
- live reload：watch 目录变更即时生效
- 现有 `build_welding_skill_registry()` 改为扫描 `.weldevent/skills/` + 内置 fallback

### 3.11 MCP 配置（P2 增强项）

| 维度 | trae-agent | Codex | Claude Code | WeldEvent |
|------|-----------|-------|-------------|-----------|
| 配置方式 | YAML 声明 | 协议级 | `.mcp.json` | **代码内 app.py** |
| per-tool approval | 无 | 是 | 是 | 是（phase_override） |
| per-subagent scope | 无 | 无 | 是 | 无 |

**借鉴建议**：
- 新建 `.weldevent/mcp.json` 声明 MCP server
- app.py 启动时扫描加载，取代代码内硬编码

---

## 四、WeldEvent 独有优势（三个 agent 都没有的，必须保住）

| 优势 | 机制 | 三个 agent 对比 |
|------|------|----------------|
| **L2 Temporal DAG 编排** | RunWorkflowSpec + 拓扑排序 + HumanGate 双重 + dependency_results 注入 | 三个通用 agent 全无 L2 工作流编排 |
| **WeldMap 黑板层** | 6 域层级路径 + CAS 乐观锁 + Event Sourcing | 三个通用 agent 无跨 Activity 状态传递 |
| **架构层强制 Approval 阻塞** | `await event.wait(300s)` LLM 无法绕过 | Claude Code 非阻塞问询，Codex 异步审批，trae-agent 无 |
| **DSML fallback** | DeepSeek 非 FC 模型可用 | 三个 agent 仅支持原生 FC |
| **Session Notes 19 工具** | 每轮工具笔记注入下一轮 | 三个 agent 无（靠 subagent 隔离替代） |
| **SAME_TOOL_LIMIT=3 静默 reject** | 防穷举式调用 + 不泄露架构内部 | 三个 agent 无 |
| **三层知识获取 fallback** | search_standards → web_search → request_confirmation | 三个 agent 无 |
| **ToolFailureReflector** | schema 失败自动修复 | 三个 agent 无 |
| **三层 Guardrails** | 焊接领域规则护栏 | 三个 agent 无领域护栏 |
| **LLMResponseCache** | temp==0 确定性缓存 | 三个 agent 无 |
| **upload_image_to_dataset L1 包装** | image_ref → base64 自动转换 | 三个 agent 无此断层桥接 |
| **9 Activity Pool 工业特化** | IQA/PPA/MEA/RDA/VDA/RVA/MTA/HCA/Annotation | 三个通用 agent 无工业算法 |
| **AgentLoop 双 task + WebSocket** | receive_task + react_task 连接级生命周期 + 中断/反馈队列 + tool_registry 透传 + session 持久化 | trae-agent/Claude Code CLI 同步；Codex 有 CodexThread 但无工业 tool_registry 透传 |
| **WebSocket 实时交互** | WS + 中断/反馈 + 动态文案弹窗 | 三个 agent CLI 同步 |

**结论**：WeldEvent 在**工业场景特化 + 架构层固化 + L2 编排**上有 13 项独有优势，是三个通用 agent 无法替代的。但缺的恰恰是**通用 agent 的认知编排能力**（子 agent + 项目文档 + hooks + trajectory）。

---

## 五、借鉴优先级总表

### P0 （影响能力上限）

| # | 借鉴项 | 来源 | 预估成本 | 价值 |
|---|--------|------|---------|------|
| 1 | **声明式 Subagent 系统**（含 3 种专用 subagent：explorer/architect/reviewer） | Codex TOML + Claude Code SKILL.md + feature-dev plugin | 高（复用 ReActEngine） | 最大 |
| 2 | **WELDEVENT.md 项目文档发现** | Codex agents_md.rs + Claude Code CLAUDE.md | 低 | 高 |
| 3 | **TrajectoryRecorder 结构化轨迹** | trae-agent AgentExecution/AgentStep | 中 | 高（解决导出失败） |
| 4 | **显式方法论 system prompt** | trae-agent 7 步 prompt | 低 | 高（引导 LLM 行为） |

### P1 （影响工程化）

| # | 借鉴项 | 来源 | 预估成本 | 价值 |
|---|--------|------|---------|------|
| 5 | **steer_input + inject_if_running**（运行中转向/注入） | Codex CodexThread | 中 | 高（长工作流不中断） |
| 6 | **try_start_turn_if_idle**（L2 完成自动启动总结 turn） | Codex | 中 | 中 |
| 7 | **CodexThreadSettingsOverrides**（运行时切 model/effort/permission） | Codex | 低 | 中 |
| 8 | **Hooks 事件扩展（30+→6 核心）** | Claude Code | 中 | 中 |
| 9 | **`/context` 可视化 + focus 定向压缩** | Claude Code | 中 | 中 |
| 10 | **parallel_tool_calls** | trae-agent + Codex | 中 | 中（多图提速） |
| 11 | **task_done + 二次验证**（WeldMap 结果验证） | trae-agent | 低 | 中 |
| 12 | **Permission mode 5 级分级** | Claude Code | 低 | 中 |
| 13 | **即时反思（每步而非 ≥2 次）** | trae-agent | 低 | 中 |
| 14 | **声明式 Skills SKILL.md** | Claude Code | 中 | 中 |
| 15 | **MCP YAML 声明式配置** | trae-agent + Claude Code | 低 | 低 |
| 16 | **prompt_cache_key_override** | Codex | 低 | 低（降本） |
| 17 | **W3cTraceContext 分布式追踪** | Codex | 中 | 低（单服务暂不需要） |

### P2 增强项（按需补）

| # | 借鉴项 | 来源 | 预估成本 | 价值 |
|---|--------|------|---------|------|
| 18 | **Dynamic workflows 脚本编排**（对抗式交叉验证） | Claude Code feature-dev | 高 | 中（与 L2 DAG 互补） |
| 19 | **sequentialthinking 工具**（分支思维+修订） | trae-agent | 中 | 低 |
| 20 | **Docker 沙箱** | trae-agent | 中 | 低（短期无需求） |
| 21 | **worktree 隔离** | Claude Code + Codex | 中 | 低（单用户场景） |
| 22 | **Plugin marketplace** | Claude Code | 高 | 低 |
| 23 | **服务端 compact / memories** | Codex | 高 | 低（受模型限制） |
| 24 | **Realtime WebRTC** | Codex | 高 | 低（非工业场景） |
| 25 | **thread_lifecycle_contributors 扩展点** | Codex | 中 | 低 |
| 26 | **WebSocket prewarm + sticky routing** | Codex | 高 | 低（需服务端配合） |

---

## 六、落地路线图建议

### 阶段一：认知编排补齐（P0）
1. **WELDEVENT.md 发现**（1 文件，低成本立竿见影）
   - 新建 `cognitiveplane/interaction/project_doc.py`
   - 从 cwd 向上找 `.git` / `pyproject.toml` 定位 root
   - 收集路径上所有 `WELDEVENT.md` + `WELDEVENT.local.md`
   - `project_doc_max_bytes=8192` 预算
   - 注入 system prompt

2. **显式方法论 system prompt**（改 react.py 现有 prompt，零新文件）
   - 借鉴 trae-agent 7 步法，写 WeldEvent 版：Understand→Explore（list_datasets/search_standards）→Design（design_workflow）→Verify（IQA/PPA）→Annotate（create_job/upload/create_task/trigger_ai）→Review（list_tasks/get_job）→Summarize
   - 引导 LLM 用 sequentialthinking 式显式思考（先 plan 再 act）
   - 引导用 task_done 收尾

3. **TrajectoryRecorder**（1 文件，解决导出失败）
   - 新建 `cognitiveplane/control/trajectory.py`
   - `AgentExecution` + `AgentStep` dataclass（含 step_number/state/thought/tool_calls/tool_results/llm_response/reflection/error/llm_usage/timestamp）
   - ReActEngine 每轮 append step
   - `GET /sessions/{id}/trajectory` 端点

4. **声明式 Subagent**（核心，复用 ReActEngine）
   - 新建 `cognitiveplane/control/subagent/` 目录
   - `SubagentDefinition` dataclass（name/description/tools/model/permissionMode/maxTurns）
   - `SubagentRunner`（复用 ReActEngine，独立 session + tool_registry 子集）
   - `.weldevent/agents/*.md` 文件扫描 + YAML frontmatter 解析
   - 内置 3 种专用 subagent（借鉴 Claude Code feature-dev）：
     - `weld-explorer`（只读：list_datasets/get_dataset/list_jobs/get_job/list_tasks/read_weldmap/search_standards）
     - `weld-architect`（设计：design_workflow + search_standards/cases/process）
     - `weld-reviewer`（审查：list_tasks/get_job + confidence-based filtering 只报高置信度问题）
   - 主 agent 的 `Agent` 工具：`delegate(subagent_name, task)`

### 阶段二：会话与工程化增强（P1）
5. **steer_input + inject_if_running** — AgentLoop 加 `_steer_queue` + ReActEngine 暴露 `inject_items()`，长工作流中途加图/改参数不中断
6. **try_start_turn_if_idle** — WorkflowEventBus 检测 L2 完成时自动启动总结 turn
7. **CodexThreadSettingsOverrides** — AgentLoop 加 `preview_settings_overrides`，运行时切 model/effort/permission_mode
8. **Hooks 扩展** — 加 PreCompact/PostCompact/SubagentStart/Stop/SessionStart/End
9. **`/context` 可视化** — 按 system/history/tools/skills 分类 token 占用
10. **focus 定向压缩** — CompactionStrategy 加 focus_hint
11. **parallel_tool_calls** — ToolExecutor 加并行分支（多图分析提速）
12. **task_done + WeldMap 二次验证** — 工作流完成时验证 WeldMap 是否真有结果
13. **Permission mode 5 级分级** — Default/AcceptEdits/Auto/Bypass/Plan
14. **即时反思** — fail_counts 门槛从 ≥2 降到 1（每步反思）
15. **声明式 Skills** — `.weldevent/skills/<name>/SKILL.md`
16. **MCP YAML 配置** — `.weldevent/mcp.json`
17. **prompt_cache_key_override** — ModelClient 加字段降本

### 阶段三：按需增强（P2）
18. Dynamic workflows（JS 脚本编排 + 对抗式交叉验证）/ sequentialthinking 工具 / Docker 沙箱 / worktree / Plugin / thread_lifecycle_contributors

---

## 七、核心结论

### 7.1 WeldEvent 的定位
WeldEvent 是**工业质检特化 agent**，不是通用 SE agent。三个对照对象（trae-agent/Codex/Claude Code）都是**通用 SE agent**。WeldEvent 在 L2 编排 + 工业算法 + 架构层固化 + AgentLoop 双 task 上有 14 项独有优势，但在 L1 认知编排（子 agent + 项目文档 + trajectory + 显式 prompt）上有 4 项 P0 致命差距。

### 7.2 核心差距
**子 Agent 系统**是最大差距。Codex 用 TOML + 96 名字池，Claude Code 用 SKILL.md + 4 种并行模式 + feature-dev plugin 的 3 种专用 subagent（explorer/architect/reviewer）+ 对抗式交叉验证，WeldEvent 只有 Port 接口从未实现。这导致 WeldEvent 无法 spawn 独立子任务，复杂任务全靠单线程 ReAct，长任务容易撞 max_iterations=25 上限。

**会话运行中干预**是第二大差距。Codex 的 CodexThread 支持 steer_input（不中断转向）+ inject_if_running（运行中注入）+ try_start_turn_if_idle（idle 自动启动），WeldEvent 的 AgentLoop 虽然有双 task + 中断 + 反馈，但只能全中断重来，无法运行中转向/注入。

### 7.3 核心优势
**架构层固化 + AgentLoop 双 task**是 WeldEvent 最大优势。ApprovalGate `await event.wait()` 强制阻塞、SAME_TOOL_LIMIT=3 静默 reject、Session Notes 19 工具、ToolFailureReflector schema 反射、AgentLoop receive_task/react_task 双 task 连接级生命周期 + tool_registry 透传 + session 持久化——这些都是三个通用 agent 没有的"架构替 LLM 做看不见的事"的工程化设计，补强了通用 ReAct 的脆弱点。

### 7.4 借鉴原则
- **保住 14 项独有优势**（含 AgentLoop 双 task），不盲目通用化
- **补齐 4 项 P0 差距**（子 agent + 项目文档 + trajectory + 显式 prompt）
- **优先补 steer/inject**（P1 但价值高，长工作流场景痛点）
- **按需补 P1 工程化**（hooks/context/parallel/permission）
- **L2 Temporal DAG 与 L1 subagent 互补**，不替代——L2 管工业工作流编排，L1 管认知任务委派

### 7.5 不建议借鉴的
- **Realtime WebRTC** — 非工业场景
- **Plugin marketplace** — 过重，单项目不需要
- **服务端 compact/memories** — 受模型 API 限制，WeldEvent 用 DeepSeek 不走 OpenAI 端点
- **1M token 扩展** — 受模型限制


