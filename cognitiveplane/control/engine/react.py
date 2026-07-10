"""ReActEngine — Reasoning+Acting loop with 3-tier fallback.

Source: 7-plane redesign spec §7 Interaction — ReAct.

Tier 1: Full ReAct with Function Calling (LLM available)
Tier 2: Structured output intent analysis + rule-based routing
Tier 3: Semantic embedding + rule engine (no LLM)

This module contains the core ReAct loop. Supporting code (approval gate,
session notes, system prompt builder, tool execution pipeline) lives in
sibling modules within the engine package.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from cognitiveplane.adapters.observability.tracing import observe
from cognitiveplane.control.deps import CognitiveDependencies
from cognitiveplane.control.hooks import BeforeToolHook, HookDecision, HookResult
from cognitiveplane.control.skills import Skill, SkillRegistry
from cognitiveplane.control.registry.tool_registry import ToolRegistry
from cognitiveplane.control.tools import ToolResult
from cognitiveplane.governance.guardrails import (
    AfterToolHook,
    GuardrailAction,
    OutputGuardrail,
)
from cognitiveplane.governance.policy_ratchet import (
    PolicyRatchet,
    TRIGGER_TIMEOUT,
)
from cognitiveplane.shared.dto.context import ContextSnapshot

# Engine-internal imports (sibling modules)
from cognitiveplane.control.engine.approval import (
    ApprovalRequest,
    ApprovalStore,
    APPROVAL_REQUIRED_TOOLS,
    APPROVAL_TIMEOUT_SECONDS,
    summarize_for_approval,
)
from cognitiveplane.control.engine.session_notes import (
    TOOL_TIMEOUT_SECONDS,
    derive_note,
    derive_failure_reflection,
    build_progress_note,
)
from cognitiveplane.control.engine.system_prompt import SystemPromptBuilder
from cognitiveplane.control.engine.tool_execution import (
    HookRunner,
    RatchetRecorder,
    ToolCallValidator,
    ToolExecutor,
)

if TYPE_CHECKING:
    from cognitiveplane.control.event_log import EventLog
    from cognitiveplane.interaction.image_store import ImageStore
    from cognitiveplane.interaction.workflow_events import WorkflowEventBus

EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]

logger = logging.getLogger("react")

# Plan §3.6 line 596-606: Tool reliability tiers
# Tier-A (retriable): info-fetching tools — schema failure → retry 1 time
# Tier-B (precise): action tools — schema failure → reject immediately
TIER_A_TOOLS = frozenset({
    "web_search",
    "search_standards",
    "search_cases",
    "search_process",
    "read_weldmap",
    "explain_decision",
    "archive_memory",
})
TIER_B_TOOLS = frozenset({
    "adjust_parameter",
    "design_workflow",
    "escalate",
})
MAX_TIER_A_RETRIES = 1  # §3.6 line 601: "schema 校验失败 → 自动 retry 1 次"


class InteractionTier(Enum):
    REACT_FUNCTION_CALLING = "react_function_calling"
    STRUCTURED_OUTPUT = "structured_output"
    EMBEDDING_RULES = "embedding_rules"


@dataclass
class InteractionResponse:
    """Response from the ReAct engine."""
    text_reply: str = ""
    decisions: list[dict[str, Any]] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    tier_used: InteractionTier = InteractionTier.REACT_FUNCTION_CALLING
    error: str | None = None
    # P3-5 fix: 结构化回传 launch_workflow 产出的 workflow_id 列表，
    # 避免下游从 LLM 回复文本正则提取（脆弱）。
    workflow_ids: list[str] = field(default_factory=list)
    # thinking 模式下 LLM 返回的推理内容，需回传给下一轮 API 调用。
    reasoning_content: str | None = None


class ReActEngine:
    """3-tier ReAct engine — core interaction loop.

    Tier 1 (Function Calling): LLM reasons → tool call → hook → execute → observe → repeat
    Tier 2 (Structured Output): LLM intent classification → rule dispatch
    Tier 3 (Embedding Rules): Semantic match → rule engine (no LLM)
    """

    def __init__(
        self,
        deps: CognitiveDependencies,
        tool_registry: ToolRegistry | None = None,
        hooks: list[BeforeToolHook] | None = None,
        max_iterations: int = 25,
        event_callback: EventCallback | None = None,
        image_store: "ImageStore | None" = None,
        event_log: "EventLog | None" = None,
        policy_ratchet: PolicyRatchet | None = None,
        after_tool_hooks: list[AfterToolHook] | None = None,
        output_guardrail: OutputGuardrail | None = None,
        skill_registry: SkillRegistry | None = None,
        approval_store: ApprovalStore | None = None,
        context_compactor: Any = None,
        workflow_event_bus: "WorkflowEventBus | None" = None,
        trajectory_store: Any = None,  # P0-3: TrajectoryStore 实例
    ) -> None:
        """Construct ReActEngine.

        image_store: session-scoped image storage (plan §A.3). Forwarded to
            ToolRegistry for AnalyzeImageTool construction. None disables
            vision tool registration.
        event_log: optional EventLog for recording architecture-side actions
            (plan §0.1 规则 1: 架构替 LLM 做的看不见的事必须记进 EventLog).
            When None, worldview injection silently skips logging.
        policy_ratchet: optional PolicyRatchet for §4.2.2 棘轮机制草稿区.
            When None, default-constructs (governance/policy_ratchet.yaml).
            Phase 2: write-only draft store, never affects ToolPolicy.
        after_tool_hooks: 工具结果护栏列表（Phase 4 Guardrails）。
            在工具执行后、结果回填给 LLM 前调用。REJECT 时替换为 replacement。
        output_guardrail: LLM 输出护栏（Phase 4 Guardrails）。
            在最终回复返回前调用。REJECT 时替换为安全回复。
        approval_store: 架构级 human-in-the-loop 确认门禁。当非 None 时，
            APPROVAL_REQUIRED_TOOLS 中的工具（如 launch_workflow）执行前
            会 emit approval_request 事件并阻塞，直到前端 /chat/approve 端点
            resolve。None 时禁用 approval gate（LLM 可直接执行）。
        context_compactor: 可选的 ContextCompactor 实例（cognitiveplane/memory/compaction.py）。
            当 history 接近 token 上限时，自动调 compact() 压缩——让 LLM 自己总结
            旧历史（保留架构决策/未解决 bug/实现细节，丢弃冗余工具输出），对应
            Anthropic Context Engineering 的 Compaction 技术。None 时禁用压缩
            （history 全量注入，多轮后可能 token 爆炸）。
        workflow_event_bus: 可选的 WorkflowEventBus 实例（boundary-pinning §3 长路径
            Temporal 观察者接入点）。注入后，每轮 _build_system_prompt 会读取
            session 内的 workflow 状态摘要，让 LLM 感知到长路径异步执行的
            workflow 完成事件，无需 LLM 主动 query。None 时禁用注入（短路径场景）。
        """
        self._deps = deps
        # 若调用方未传 tool_registry, 默认构造时把 approval_store 透传给
        # ToolRegistry, 让 RequestConfirmationTool 能阻塞等待用户选项.
        if tool_registry is not None:
            self._tools = tool_registry
        else:
            self._tools = ToolRegistry(
                deps, image_store=image_store, approval_store=approval_store
            )
        self._hooks = hooks or []
        self._max_iterations = max_iterations
        self._event_callback = event_callback
        self._event_log = event_log
        # P2-24 分析: _ratchet 保持实例属性 — PolicyRatchet 内部有 threading.Lock
        # 保护文件写入（原子 write-temp + rename），并发 run() 调 record() 不会
        # 丢数据。不同 session 的草稿记录交错是可接受的（草稿区本就是全局运维
        # 评审输入，不按 session 隔离）。与 _tools_used 不同：_tools_used 是
        # list.append 非原子会丢数据，_ratchet.record() 有锁保护。
        self._ratchet = policy_ratchet or PolicyRatchet()
        # Guardrails（Phase 4）— 三层护栏中的 after_tool + output 层
        self._after_tool_hooks = after_tool_hooks or []
        self._output_guardrail = output_guardrail
        # Agent Skills（Phase 5）— 场景化技能注册与选择
        self._skill_registry = skill_registry
        # 架构级 approval gate — human-in-the-loop 确认门禁
        self._approval_store = approval_store
        # Context Compaction — 长对话自动压缩（Anthropic Context Engineering）
        self._compactor = context_compactor
        # 长路径 Temporal 观察者接入点 — boundary-pinning §3
        # 注入后 _build_system_prompt 会读取 workflow 状态摘要注入到 system prompt
        self._workflow_event_bus = workflow_event_bus
        # P0-3: 轨迹存储 — 记录每次 ReAct 执行的 per-step 推理轨迹
        self._trajectory_store = trajectory_store

        # ── Engine helper instances (extracted from former methods) ──
        self._hook_runner = HookRunner(self._hooks)
        self._ratchet_recorder = RatchetRecorder(self._ratchet)
        self._tool_validator = ToolCallValidator(
            tools=self._tools,
            hook_runner=self._hook_runner,
            ratchet=self._ratchet_recorder,
            skill_registry=self._skill_registry,
        )
        self._tool_executor = ToolExecutor(
            tools=self._tools,
            ratchet=self._ratchet_recorder,
            after_tool_hooks=self._after_tool_hooks,
            output_guardrail=self._output_guardrail,
        )
        self._prompt_builder = SystemPromptBuilder(
            tools=self._tools,
            deps=deps,
            workflow_event_bus=workflow_event_bus,
            event_log=event_log,
        )

    async def _select_skill_async(self, user_input: str | list[dict], session: dict | None = None) -> Skill | None:
        """根据用户输入选择最匹配的 Skill — LLM-first，对齐 Claude Code 渐进式披露。

        选择策略：
          1. 短句保持锁定 — "确认/好的/同意"等短回复保持当前 skill
          2. 关键词高分捷径 — score >= 3 直接命中（极少数高置信度）
          3. LLM 语义分类（首选）— LLM 看到所有 skill 元数据，自主决策
          4. LLM 不可用 → 回退关键词匹配
          5. 无匹配 → fallback 通用模式
        """
        if self._skill_registry is None:
            return None
        raw_text = self._skill_registry._coerce_text(user_input)
        clean_text = self._strip_system_injection(raw_text)

        llm = self._deps.capability.llm_provider
        skill = await self._skill_registry.select_skill_semantic(
            clean_text, llm, session=session
        )
        if skill and session is not None:
            session["locked_skill"] = skill.name
        return skill

    @staticmethod
    def _strip_system_injection(text: str) -> str:
        """剥离 chat.py _inject_session_image_refs 注入的 [系统：...] 段落。

        注入格式: <用户原始消息>\n\n[系统：...]
        剥离后只保留用户原始消息，避免注入的"图片/分析/image_ref"等词
        污染 skill 触发词匹配。
        """
        if not text:
            return text
        # 找注入段起点（双换行后的 [系统：）
        marker = "\n\n[系统："
        idx = text.find(marker)
        if idx == -1:
            # 兼容单换行情况
            marker = "\n[系统："
            idx = text.find(marker)
        if idx == -1:
            return text
        return text[:idx]

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Fire event callback if registered. Swallow callback errors —
        streaming must never break the core ReAct loop."""
        if self._event_callback is None:
            return
        try:
            await self._event_callback(event_type, payload)
        except Exception:
            logger.debug("event_callback failed", exc_info=True)

    @staticmethod
    def _needs_web_search_consent(
        tool_name: str,
        session: dict[str, Any] | None,
    ) -> bool:
        """联网检索默认需要用户许可；同会话内批准一次后复用。"""
        if tool_name != "web_search":
            return False
        permission = (session or {}).get("web_search_permission")
        return not bool(permission and permission.get("granted"))

    async def _publish_approval_notification(
        self,
        *,
        approval_id: str,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        iteration: int,
        summary: str,
    ) -> None:
        """approval gate 通知推送 — 已废弃。

        notifications.store 模块已删除，approval_request 事件现在通过 SSE
        直接推送到前端（见 run_stream 中的 yield {"event": "approval_request", ...}）。
        此方法保留为空壳避免破坏潜在的外部调用，无副作用。
        """
        return

    def _select_tier(self) -> InteractionTier:
        """Choose interaction tier based on LLM capabilities (plan §2.2).

        LLMTier-1 (function calling)  — primary path, LLM fully autonomous.
        LLMTier-2 (structured output) — LLM lacks FC but supports JSON mode.
        LLMTier-3 (keyword rules)     — no LLM or LLM lacks both.

        Tier is selected at startup per LLM capability, NOT switched at runtime
        (runtime fallback is §3.6 ToolReliability, a different axis).
        """
        llm = self._deps.capability.llm_provider
        if llm is None:
            return InteractionTier.EMBEDDING_RULES
        if llm.supports_function_calling:
            return InteractionTier.REACT_FUNCTION_CALLING
        if llm.supports_json_mode:
            return InteractionTier.STRUCTURED_OUTPUT
        return InteractionTier.EMBEDDING_RULES

    @observe(name="react.run")
    async def run(
        self,
        user_input: str | list[dict],
        context: ContextSnapshot,
        session: dict[str, Any] | None = None,
    ) -> InteractionResponse:
        """Run the ReAct loop with automatic tier selection.

        user_input: str (text-only) OR list[dict] (OpenAI multimodal content:
            [{"type":"text","text":...}, {"type":"image_url","image_url":{"url":...}}]).
            When the primary LLM doesn't support vision, image_url parts are
            filtered out at the engine layer (plan §2.3 dual-track + line 3166).
        """
        # P1-R3-1 fix: tools_used 改 run() 局部变量，避免并发 run() 互相覆盖实例属性
        tools_used: list[str] = []
        # P3-5 fix: 收集 launch_workflow 产出的 workflow_id（结构化回传，避免正则提取）
        workflow_ids: list[str] = []
        tier = self._select_tier()
        # Agent Skills: LLM-first 语义分类（对齐 Claude Code 渐进式披露）
        skill = await self._select_skill_async(user_input, session)

        # P0-3: TrajectoryRecorder
        from cognitiveplane.control.trajectory import TrajectoryRecorder
        session_id = str((session or {}).get("session_id", "default"))
        trajectory = TrajectoryRecorder(
            task=self._coerce_text(user_input) if isinstance(user_input, list) else user_input,
            session_id=session_id,
        )
        trajectory.start()

        if tier == InteractionTier.REACT_FUNCTION_CALLING:
            if session is not None:
                async def runtime_event_callback(event_type: str, payload: dict[str, Any]) -> None:
                    await self._emit(event_type, payload)

                session["_runtime_event_callback"] = runtime_event_callback
            response = await self._run_react(user_input, context, session or {}, tier, tools_used, workflow_ids, skill=skill, trajectory=trajectory)
        elif tier == InteractionTier.STRUCTURED_OUTPUT:
            response = await self._run_structured(self._coerce_text(user_input), context, session or {}, tools_used, skill=skill)
        else:
            response = await self._run_embedding_rules(self._coerce_text(user_input), context, session or {}, tools_used, skill=skill)

        trajectory.finalize(result=response.text_reply, success=response.error is None)
        # 存入 session 供导出端点 GET /sessions/{id}/trajectory 使用
        if session is not None:
            session["_trajectory"] = trajectory
        if self._trajectory_store is not None:
            self._trajectory_store.put(session_id, trajectory)

        # Phase 4 Guardrails: OutputGuardrail — LLM 最终输出护栏
        # REJECT 时替换为安全回复（在 emit final 之前）
        response.text_reply = await self._tool_executor.check_output_guardrail(
            reply=response.text_reply,
            tools_used=response.tools_used,
            session_id=session_id,
        )

        await self._emit("final", {
            "reply": response.text_reply,
            "tools_used": response.tools_used,
            "tier": response.tier_used.value,
            "error": response.error,
        })
        return response

    @observe(name="react.run_stream")
    async def run_stream(
        self,
        user_input: str | list[dict],
        context: ContextSnapshot,
        session: dict[str, Any] | None = None,
    ):
        """流式 ReAct loop — async generator，yield 事件 dict。

        与 run() 的差异：
          - 工具轮（有 tool_calls）：用 complete()，yield tool_call/tool_result 事件
          - 最终轮（无 tool_calls）：用 stream() 真流式输出 token

        事件类型：
          - thinking:    进入新迭代
          - tool_call:   工具被调用（含 rejected/retry/schema_invalid 信息）
          - tool_result: 工具返回
          - token:       最终轮 LLM 流式 token（逐 token yield）
          - final:       流式结束（含完整 reply + tools_used + workflow_ids）
          - error:       异常

        用途：chat.py 的 /chat/stream SSE 端点桥接此生成器。
        设计说明：provider.stream() 只 yield content，不含 tool_calls 分片。
        因此工具轮用 complete() 拿完整 tool_calls；最终轮（LLM 决定不调工具）
        才调 stream() 逐 token 输出，给用户打字机体验。最终轮会多一次 LLM
        调用（complete 判断无 tool_calls + stream 真流式），但只发生一次，
        成本可接受，换取真流式体验。
        """
        tools_used: list[str] = []
        workflow_ids: list[str] = []
        tier = self._select_tier()
        # Agent Skills: LLM-first 语义分类（对齐 Claude Code 渐进式披露）
        skill = await self._select_skill_async(user_input, session)

        # P0-3: TrajectoryRecorder
        session_id_trajectory = str((session or {}).get("session_id", "default"))
        trajectory = None
        if tier == InteractionTier.REACT_FUNCTION_CALLING:
            from cognitiveplane.control.trajectory import TrajectoryRecorder
            trajectory = TrajectoryRecorder(
                task=self._coerce_text(user_input) if isinstance(user_input, list) else user_input,
                session_id=session_id_trajectory,
            )
            trajectory.start()

        # 非 Tier-1 降级：直接调 run()，把结果包装成 final 事件
        if tier != InteractionTier.REACT_FUNCTION_CALLING:
            response = await self.run(user_input, context, session)
            yield {
                "event": "final",
                "data": {
                    "reply": response.text_reply,
                    "tools_used": response.tools_used,
                    "workflow_ids": response.workflow_ids,
                    "tier": response.tier_used.value,
                    "error": response.error,
                },
            }
            return

        llm = self._deps.capability.llm_provider
        if llm is None:
            yield {"event": "error", "data": {"message": "LLM provider unavailable"}}
            return

        session = session or {}
        session_id = str(session.get("session_id", "default"))
        runtime_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def runtime_event_callback(event_type: str, payload: dict[str, Any]) -> None:
            await runtime_events.put({"event": event_type, "data": payload})

        session["_runtime_event_callback"] = runtime_event_callback

        messages = await self._prompt_builder.build(context, session, skill=skill)
        # 注入历史对话（含 Context Compaction：超阈值时让 LLM 自己总结压缩）
        messages, compaction_meta = await self._inject_history(messages, session)
        if compaction_meta:
            yield {"event": "compaction", "data": compaction_meta}
        messages.append({"role": "user", "content": self._adapt_user_input(llm, user_input)})

        tier_a_failures: dict[str, int] = {}
        # 同轮同名工具调用计数 — 防止 LLM 反复调同一工具穷举参数
        # (如 search_standards 换 7 种 query 反复查)。超过 SAME_TOOL_LIMIT
        # 后 reject 并提示 LLM 基于已有结果回答。
        same_tool_counts: dict[str, int] = {}
        SAME_TOOL_LIMIT = 3
        # 询问节奏控制 — 防止 LLM 连续 request_confirmation 轰炸用户。
        # 本轮已问次数达到 ASK_LIMIT 后，下一次 request_confirmation 被拦截，
        # 强制 LLM 基于已有信息先给诊断/推进。
        ask_count_this_round = 0
        ASK_LIMIT = 2
        final_reply = ""
        allowed_tools = skill.allowed_tools if skill else None
        # P0-1: Subagent 工具白名单 — 覆盖 skill 的 allowed_tools
        subagent_config = session.get("subagent")
        if subagent_config and isinstance(subagent_config, dict):
            allowed_tools = list(subagent_config.get("allowed_tools", []))
        # P0-3: 收集本轮工具调用结果（用于轨迹记录）
        stream_tool_calls: list[dict] = []
        stream_tool_results: list[dict] = []
        for i in range(self._max_iterations):
            # 增强 E：进度感知（Anthropic "course-correct early and often" 借鉴）
            messages.append({
                "role": "system",
                "content": build_progress_note(i, tools_used, self._max_iterations),
            })
            try:
                response = await llm.complete(
                    self._make_llm_request(messages, allowed_tools=allowed_tools)
                )
            except Exception as e:
                yield {"event": "error", "data": {"message": f"LLM error: {e}"}}
                return

            tool_calls = response.tool_calls
            # 架构层保证：工具轮（LLM 决定调工具）时，把 LLM 同时返回的推理内容
            # （content）通过 thinking 事件暴露给前端。这是 agent loop 的核心透明度
            # ——不依赖 system prompt 约束 LLM "展示推理"，而是架构直接把 LLM 返回的
            # reasoning 推给用户。LLM 在调用工具时常附带思考文本（如分析依据、为何
            # 选此节点），这正是 Claude Code 风格的 thinking 块。
            # 注意：最终轮（无 tool_calls）的 content 是最终回复，后面会走 token 流式，
            # 不走 thinking，避免重复展示。
            if response.content and tool_calls:
                yield {
                    "event": "thinking",
                    "data": {
                        "iteration": i + 1,
                        "tools_so_far": list(tools_used),
                        "reasoning": response.content,
                    },
                }

            if not tool_calls:
                # 最终轮 — LLM 决定不再调工具。
                # Phase 4 Guardrails: OutputGuardrail — 用 complete() 的 content 预检
                # REJECT 时不流式输出（token 一旦发出无法撤回），直接发 replacement
                session_id = str(session.get("session_id", "default"))
                checked = await self._tool_executor.check_output_guardrail(
                    reply=response.content,
                    tools_used=tools_used,
                    session_id=session_id,
                )
                if checked != response.content:
                    # 被护栏拦截 — 发送 replacement 作为单个 token
                    final_reply = checked
                    yield {"event": "token", "data": {"content": final_reply}}
                else:
                    # 通过护栏 — 用 complete() 的 content 分 chunk 输出模拟流式。
                    # 不再调 llm.stream() 重新发请求：stream() 不传 tools 也不解析
                    # DSML fallback，DeepSeek 在 stream 路径下会用 DSML 文本格式
                    # "调工具"（如 <｜｜DSML｜｜tool_calls>...），直接泄露到用户看到的
                    # content。complete() 已经过 DSML fallback 解析（若有 DSML 会变成
                    # tool_calls 走工具轮，不会到这里），content 是干净的最终回复。
                    content = response.content or ""
                    # 防御：DSML fallback 解析失败时 content 可能残留 DSML 标记，
                    # 清理掉避免泄露内部 token。
                    content = re.sub(
                        r'<｜｜DSML｜｜tool_calls>.*?</｜｜DSML｜｜tool_calls>',
                        '',
                        content,
                        flags=re.DOTALL,
                    ).strip()
                    # 分 chunk yield 模拟打字机（4 字符/chunk，10ms 间隔）
                    chunk_size = 4
                    for i in range(0, len(content), chunk_size):
                        chunk = content[i:i + chunk_size]
                        final_reply += chunk
                        yield {"event": "token", "data": {"content": chunk}}
                        await asyncio.sleep(0.01)

                yield {
                    "event": "final",
                    "data": {
                        "reply": final_reply,
                        "tools_used": tools_used,
                        "workflow_ids": workflow_ids,
                        "tier": tier.value,
                        "error": None,
                        "reasoning_content": response.reasoning_content,
                    },
                }
                if trajectory is not None:
                    trajectory.finalize(result=final_reply, success=True)
                    session["_trajectory"] = trajectory
                    if self._trajectory_store is not None:
                        self._trajectory_store.put(session_id, trajectory)
                return

            # 工具轮 — 并发执行多个 tool_call（与 _run_react 逻辑一致）
            # P1-2 fix: 阶段 1 校验抽到公共 _validate_tool_call,消除 300 行重复
            # rejected/retry 立即处理,accepted 收集到 pending_executions 进入并发
            pending_executions: list[dict] = []
            # P0-3: 重置本轮收集器
            stream_tool_calls.clear()
            stream_tool_results.clear()
            for tool_call in tool_calls:
                # 同轮同名工具调用上限检查 — 防止 LLM 反复调同一工具穷举参数
                # (如 search_standards 换 7 种 query 反复查)。超过 SAME_TOOL_LIMIT
                # 后 reject,提示 LLM 基于已有结果回答。
                # 静默处理 — 不 yield tool_call 事件给前端,避免内部架构信息泄露。
                # 只 extend messages 让 LLM 下一轮看到 reject 反馈。
                _tc_name = tool_call.get("function", {}).get("name", "")
                _cur_count = same_tool_counts.get(_tc_name, 0)
                if _tc_name and _cur_count >= SAME_TOOL_LIMIT:
                    _reason = (
                        f"Tool {_tc_name} 已调用 {_cur_count} 次,达到同轮上限 "
                        f"{SAME_TOOL_LIMIT}。请基于已返回的结果综合回答,不要再用"
                        f"不同 query/standard_id 反复查同一工具。如需外部信息调 web_search。"
                    )
                    _tool_call_id = tool_call.get("id", "")
                    _rej_msgs = [
                        {"role": "assistant", "content": None, "tool_calls": [tool_call], "reasoning_content": response.reasoning_content},
                        {"role": "tool", "tool_call_id": _tool_call_id,
                         "content": json.dumps({"error": _reason}, ensure_ascii=False)},
                    ]
                    messages.extend(_rej_msgs)
                    continue

                # 询问节奏控制 — request_confirmation 轰炸拦截。
                # 本轮已问 ASK_LIMIT 次后，拒绝再次询问，强制 LLM 先给诊断/推进。
                if _tc_name == "request_confirmation" and ask_count_this_round >= ASK_LIMIT:
                    _reason = (
                        f"本轮已连续询问用户 {ask_count_this_round} 次,达到节奏上限 "
                        f"{ASK_LIMIT}。用户体验上连续弹窗是轰炸。请基于已有信息先给出"
                        f"初步诊断或推进到能推进的步骤,等用户在下一轮自然反馈后再问"
                        f"剩余问题。如确实缺关键信息无法推进,在回复里说明需要用户"
                        f"补充什么,但不要调 request_confirmation。"
                    )
                    _tool_call_id = tool_call.get("id", "")
                    _rej_msgs = [
                        {"role": "assistant", "content": None, "tool_calls": [tool_call], "reasoning_content": response.reasoning_content},
                        {"role": "tool", "tool_call_id": _tool_call_id,
                         "content": json.dumps({"error": _reason, "blocked_by": "ask_pace_control"}, ensure_ascii=False)},
                    ]
                    messages.extend(_rej_msgs)
                    continue

                outcome = await self._tool_validator.validate(
                    tool_call, context, session_id, tier_a_failures, tools_used,
                    allowed_tools=allowed_tools,
                    session=session,
                    reasoning_content=response.reasoning_content,
                )
                # accepted 时 increment 同轮调用计数(用于下一轮上限判断)
                if outcome["kind"] == "accepted":
                    same_tool_counts[outcome["tool_name"]] = (
                        same_tool_counts.get(outcome["tool_name"], 0) + 1
                    )
                # emit tool_call 事件(rejected 和 accepted 都 emit)
                yield {"event": "tool_call", "data": outcome["event_data"]}
                # extend messages(outcome 已构造好 assistant+tool 序列)
                messages.extend(outcome["messages"])

                # P0-3: 记录工具调用到本轮收集器
                tool_name = outcome.get("tool_name", tool_call.get("function", {}).get("name", ""))
                arguments = outcome.get("arguments", tool_call.get("function", {}).get("arguments", "{}"))
                stream_tool_calls.append({
                    "name": tool_name,
                    "arguments": arguments,
                    "accepted": outcome["kind"] == "accepted",
                })

                if outcome["kind"] == "accepted":
                    # 询问节奏控制 — 记录本轮 request_confirmation 调用次数
                    if outcome["tool_name"] == "request_confirmation":
                        ask_count_this_round += 1
                    # P2-4 fix: 注入 session_id 供 launch_workflow 去重
                    # 只对 launch_workflow 注入 — 其他工具（如 list_datasets）
                    # 的 schema 不允许 session_id 字段，注入会触发 Schema validation 失败。
                    args_with_session = dict(outcome["arguments"])
                    if outcome["tool_name"] == "launch_workflow":
                        args_with_session.setdefault("session_id", session_id)
                    pending_executions.append({
                        "tool_call": outcome["tool_call"],
                        "tool_name": outcome["tool_name"],
                        "tool_call_id": outcome["tool_call_id"],
                        "arguments": args_with_session,
                    })
                # rejected_retry / rejected_fatal 已 emit + messages 已 extend,直接 continue
                continue

            # ── 架构级 approval gate（human-in-the-loop）──
            # 在阶段 2 执行前，对 APPROVAL_REQUIRED_TOOLS 中的工具暂停等待用户确认。
            # 这是架构保证，不依赖 system prompt 约束 LLM "等用户确认"。
            # emit approval_request 事件 → 前端展示确认 UI → 用户决定 →
            # POST /chat/approve 端点 resolve → req.event.set() → 恢复执行。
            if self._approval_store is not None and pending_executions:
                still_pending: list[dict] = []
                for item in pending_executions:
                    tname = item["tool_name"]
                    requires_approval = (
                        tname in APPROVAL_REQUIRED_TOOLS
                        or self._needs_web_search_consent(tname, session)
                    )
                    if not requires_approval:
                        still_pending.append(item)
                        continue
                    # 需要审批 — 构造请求并阻塞等待
                    approval_id = f"{session_id}-iter{i+1}-{tname}-{id(item) & 0xffffff}"
                    summary = summarize_for_approval(tname, item["arguments"])
                    req = self._approval_store.create(
                        approval_id, session_id, i + 1, tname,
                        item["arguments"], summary,
                    )
                    await self._publish_approval_notification(
                        approval_id=approval_id,
                        session_id=session_id,
                        tool_name=tname,
                        arguments=item["arguments"],
                        iteration=i + 1,
                        summary=summary,
                    )

                    yield {
                        "event": "approval_request",
                        "data": {
                            "approval_id": approval_id,
                            "tool": tname,
                            "summary": summary,
                            "iteration": i + 1,
                        },
                    }
                    # 阻塞等待用户决定（带超时）
                    try:
                        await asyncio.wait_for(req.event.wait(), timeout=APPROVAL_TIMEOUT_SECONDS)
                    except asyncio.TimeoutError:
                        req.decision = "timeout"
                    self._approval_store.remove(approval_id)

                    if req.decision == "approved":
                        # 用户批准 — 保留执行
                        if tname == "web_search":
                            session["web_search_permission"] = {
                                "granted": True,
                                "approved_at_iteration": i + 1,
                                "query": item["arguments"].get("query", ""),
                            }
                        still_pending.append(item)
                        yield {
                            "event": "tool_call",
                            "data": {
                                "tool": tname,
                                "approved": True,
                                "feedback": req.feedback or "",
                            },
                        }
                    else:
                        # 用户拒绝 / 超时 — 从 pending 移除，回填拒绝结果给 LLM
                        reject_reason = {
                            "rejected": "用户拒绝执行",
                            "timeout": "确认超时已取消",
                        }.get(req.decision or "", f"已取消 ({req.decision})")
                        fb = req.feedback or ""
                        yield {
                            "event": "tool_call",
                            "data": {
                                "tool": tname,
                                "rejected": True,
                                "reason": f"{reject_reason}{(' — ' + fb) if fb else ''}",
                            },
                        }
                        # 回填 tool 结果给 LLM，让它知道用户拒绝并可调整方案
                        messages.append({
                            "role": "assistant", "content": None,
                            "tool_calls": [item["tool_call"]],
                            "reasoning_content": response.reasoning_content,
                        })
                        messages.append({
                            "role": "tool", "tool_call_id": item["tool_call_id"],
                            "content": json.dumps({
                                "status": "rejected",
                                "error": reject_reason,
                                "feedback": fb,
                            }, ensure_ascii=False),
                        })
                pending_executions = still_pending

            # 阶段 2：并发执行 — 实际 execute 是最耗时部分，tool_calls 彼此独立可并发
            # 总耗时由最慢的工具决定，而非各工具耗时之和
            if pending_executions:
                execution_tasks = [
                    asyncio.create_task(
                        self._tool_executor.execute_with_timeout(
                            item["tool_name"], item["arguments"], session=session,
                        )
                    )
                    for item in pending_executions
                ]
                task_to_index = {task: idx for idx, task in enumerate(execution_tasks)}
                results: list[tuple[ToolResult, bool] | None] = [None] * len(execution_tasks)
                pending_tasks = set(execution_tasks)
                queue_task = asyncio.create_task(runtime_events.get())
                while pending_tasks:
                    done, _ = await asyncio.wait(
                        pending_tasks | {queue_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if queue_task in done:
                        event = queue_task.result()
                        yield event
                        finished_queue_task = queue_task
                        queue_task = asyncio.create_task(runtime_events.get())
                        done.remove(finished_queue_task)
                    for task in done:
                        pending_tasks.remove(task)
                        results[task_to_index[task]] = task.result()
                queue_task.cancel()
                try:
                    await queue_task
                except asyncio.CancelledError:
                    pass
                # 排空残留事件 — delegate (subagent) 工具可能在完成前最后
                # 一刻往队列塞了 subagent_completed 事件，但 queue_task
                # 已被取消。不排空会导致前端子 agent 面板永远停在"执行中"。
                while not runtime_events.empty():
                    try:
                        residual = runtime_events.get_nowait()
                        yield residual
                    except asyncio.QueueEmpty:
                        break
            else:
                results = []

            # 阶段 3：按 tool_calls 原始顺序处理结果，保持 messages / tools_used 顺序
            # （LLM 上下文顺序敏感）
            for item, (result, timed_out) in zip(pending_executions, results):
                tool_name = item["tool_name"]
                tool_call = item["tool_call"]
                tool_call_id = item["tool_call_id"]
                arguments = item["arguments"]

                if timed_out:
                    self._ratchet_recorder.record(
                        tool_name=tool_name,
                        trigger=TRIGGER_TIMEOUT,
                        failure_pattern=f"Tool execution exceeded {TOOL_TIMEOUT_SECONDS}s",
                        arguments_preview=self._ratchet_recorder.preview_arguments(arguments),
                        session_id=session_id,
                    )
                tools_used.append(tool_name)

                if tool_name == "launch_workflow" and result.output:
                    wf_id = result.output.get("workflow_id")
                    if wf_id and result.output.get("status") in ("launched", "COMPLETED", "RUNNING") and wf_id not in workflow_ids:
                        workflow_ids.append(wf_id)
                    # launch_workflow 成功后清理 current_plan（plan 已执行，不再需要注入）
                    if result.output.get("status") in ("launched", "COMPLETED", "RUNNING"):
                        session.pop("current_plan", None)
                        # 工作流启动=阶段切换（设计→执行/标注），解锁 skill 让下轮重新匹配。
                        # 否则用户说"开始标注"时仍锁定 workflow_design，调不到 list_datasets。
                        session.pop("locked_skill", None)

                # Plan-and-Execute: design_workflow 成功后持久化方案到 session。
                # 下一轮 ReAct 注入 plan 到 system prompt，LLM 知道"已设计过"，
                # 不会重复 design_workflow 循环。对应 Devin/Claude Code 的 plan 持久化。
                if tool_name == "design_workflow" and result.output and not result.error:
                    session["current_plan"] = {
                        "workflow_id": result.output.get("workflow_id"),
                        "objective": result.output.get("workflow_spec", {}).get("objective", ""),
                        "nodes": [
                            {
                                "node_id": n.get("node_id"),
                                "capability": n.get("capability"),
                                "depends_on": n.get("depends_on", []),
                                "action": (n.get("input") or {}).get("action", ""),
                            }
                            for n in result.output.get("workflow_spec", {}).get("nodes", [])
                        ],
                    }

                # Phase 4 Guardrails: AfterToolHook — 工具结果护栏
                result = await self._tool_executor.run_after_tool_hooks(
                    tool_name=tool_name,
                    arguments=arguments,
                    result=result,
                    session_id=session_id,
                )

                # 推送中间进度事件（如 workflow node 逐步完成）
                for pe in getattr(result, "progress_events", []) or []:
                    yield {"event": "workflow_progress", "data": pe}

                yield {
                    "event": "tool_result",
                    "data": {
                        "tool": tool_name,
                        "result": result.to_json(),
                        "error": result.error,
                        "error_type": result.error_type,
                    },
                }

                # P0-3: 记录工具结果到本轮收集器
                stream_tool_results.append({
                    "tool": tool_name,
                    "error": result.error,
                    "error_type": result.error_type,
                })

                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                    "reasoning_content": response.reasoning_content,
                })
                messages.append({
                    "role": "tool", "tool_call_id": tool_call_id,
                    "content": json.dumps(result.to_json()),
                })

                # 增强 A：Session Notes 派生（Anthropic Context Engineering 借鉴）
                # 工具执行后生成简短笔记存 session["notes"]，下一轮注入 system prompt。
                # 解决 LLM 跨轮失忆——不知道自己上轮做过什么。FIFO 上限 8 条。
                note = derive_note(tool_name, result, arguments)
                if note:
                    notes_list = session.setdefault("notes", [])
                    notes_list.append(note)
                    if len(notes_list) > 8:
                        session["notes"] = notes_list[-8:]

                # 增强 B：卡住检测 + 失败反思（借鉴 Anthropic "give Claude a way to verify"）
                # 连续 2 次相同工具相同参数失败 → 注入 verification gate 提示换策略
                if result.error:
                    fp = self._tool_executor.args_fingerprint(arguments)
                    fail_key = f"{tool_name}:{fp}"
                    fail_counts = session.setdefault("fail_counts", {})
                    fail_counts[fail_key] = fail_counts.get(fail_key, 0) + 1
                    if fail_counts[fail_key] >= 2:
                        reflection = derive_failure_reflection(tool_name, result, arguments)
                        if reflection:
                            stuck_note = (f"🚨 卡住：{tool_name} 连续失败 "
                                          f"{fail_counts[fail_key]} 次 — {reflection}")
                            notes_list = session.setdefault("notes", [])
                            notes_list.append(stuck_note)
                            if len(notes_list) > 8:
                                session["notes"] = notes_list[-8:]
                else:
                    # 成功时清理该工具的失败计数
                    fail_counts = session.get("fail_counts", {})
                    prefix = f"{tool_name}:"
                    for k in list(fail_counts.keys()):
                        if k.startswith(prefix):
                            del fail_counts[k]

            # P0-3: 记录本轮推理轨迹（run_stream 模式）
            if trajectory is not None:
                from cognitiveplane.control.trajectory import AgentStep
                step = AgentStep(
                    step_number=i + 1,
                    state="CALLING_TOOL" if stream_tool_calls else "COMPLETED",
                    tool_calls=stream_tool_calls.copy(),
                    tool_results=stream_tool_results.copy(),
                    llm_usage={
                        "prompt_tokens": getattr(response, "usage", {}).get("prompt_tokens", 0) if hasattr(response, "usage") else 0,
                        "completion_tokens": getattr(response, "usage", {}).get("completion_tokens", 0) if hasattr(response, "usage") else 0,
                    } if hasattr(response, "usage") else None,
                )
                trajectory.record_step(step)

        # max_iterations reached — 安全兜底
        final_reply = "Max iterations reached. Please refine your request."
        if trajectory is not None:
            trajectory.finalize(result=final_reply, success=False)
            session["_trajectory"] = trajectory
            if self._trajectory_store is not None:
                self._trajectory_store.put(session_id, trajectory)
        yield {"event": "token", "data": {"content": final_reply}}
        yield {
            "event": "final",
            "data": {
                "reply": final_reply,
                "tools_used": tools_used,
                "workflow_ids": workflow_ids,
                "tier": tier.value,
                "error": None,
            },
        }

    async def _run_react(
        self,
        user_input: str | list[dict],
        context: ContextSnapshot,
        session: dict[str, Any],
        tier: InteractionTier,
        tools_used: list[str],
        workflow_ids: list[str] | None = None,  # P3-5: 收集 launch_workflow 产出
        skill: Skill | None = None,  # Phase 5: 选中的 Agent Skill
        trajectory: "TrajectoryRecorder | None" = None,  # P0-3: 推理轨迹记录器
    ) -> InteractionResponse:
        """Tier 1: Full ReAct with Function Calling."""
        llm = self._deps.capability.llm_provider
        if llm is None:
            return await self._run_embedding_rules(self._coerce_text(user_input), context, session, tools_used, skill=skill)

        # P0-1: Subagent 工具白名单 — 覆盖 skill 的 allowed_tools
        subagent_config = session.get("subagent")
        if subagent_config and isinstance(subagent_config, dict):
            allowed_tools = list(subagent_config.get("allowed_tools", []))
        else:
            allowed_tools = skill.allowed_tools if skill else None

        # Real session_id for max_calls_per_session enforcement (plan §4.2 line 761)
        session_id = str(session.get("session_id", "default")) if session else "default"

        messages = await self._prompt_builder.build(context, session, skill=skill)
        # 注入历史对话（含 Context Compaction：超阈值时让 LLM 自己总结压缩）
        # run() 非流式，不 emit compaction 事件；run_stream() 会 emit。
        messages, _compaction_meta = await self._inject_history(messages, session)
        messages.append({"role": "user", "content": self._adapt_user_input(llm, user_input)})

        # Plan §3.6 line 601: per-tool_name consecutive failure counter
        # Tier-A: 1 malformed → emit error → LLM retries → if still malformed → ToolFailureObservation
        tier_a_failures: dict[str, int] = {}
        # 同轮同名工具调用计数 — 防止 LLM 反复调同一工具穷举参数
        same_tool_counts: dict[str, int] = {}
        SAME_TOOL_LIMIT = 3
        # 询问节奏控制 — 与 run_stream 保持一致，防止 _run_react 路径无限制提问
        ask_count_this_round = 0
        ASK_LIMIT = 2
        for i in range(self._max_iterations):
            await self._emit("thinking", {
                "iteration": i + 1,
                "tools_so_far": list(tools_used),
            })
            # 增强 E：进度感知（与 run_stream 保持一致）
            messages.append({
                "role": "system",
                "content": build_progress_note(i, tools_used, self._max_iterations),
            })
            try:
                response = await llm.complete(
                    self._make_llm_request(
                        messages,
                        allowed_tools=allowed_tools,
                    )
                )
            except Exception as e:
                return InteractionResponse(
                    text_reply=f"LLM error: {e}",
                    tools_used=tools_used,
                    tier_used=tier,
                    error=str(e),
                    workflow_ids=workflow_ids or [],
                )

            tool_calls = response.tool_calls
            if not tool_calls:
                # Plan §2.1 line 291: LLM 自主决定何时结束 — no tool_calls = LLM chose to stop.
                return InteractionResponse(
                    text_reply=response.content,
                    tools_used=tools_used,
                    tier_used=tier,
                    workflow_ids=workflow_ids or [],
                    reasoning_content=response.reasoning_content,
                )

            # 阶段 1：顺序校验，收集待执行项（rejected / retry 仍在校验阶段立即处理，不进入并发）
            # P1-2 fix: 校验抽到公共 _validate_tool_call,与 run_stream 共用同一逻辑
            pending_executions: list[dict] = []
            for tool_call in tool_calls:
                # 同轮同名工具调用上限检查 — 防止 LLM 反复调同一工具穷举参数
                # 静默处理 — 不 emit tool_call 事件给前端,避免内部架构信息泄露。
                _tc_name = tool_call.get("function", {}).get("name", "")
                _cur_count = same_tool_counts.get(_tc_name, 0)
                if _tc_name and _cur_count >= SAME_TOOL_LIMIT:
                    _reason = (
                        f"Tool {_tc_name} 已调用 {_cur_count} 次,达到同轮上限 "
                        f"{SAME_TOOL_LIMIT}。请基于已返回的结果综合回答。如需外部信息调 web_search。"
                    )
                    _tool_call_id = tool_call.get("id", "")
                    _rej_msgs = [
                        {"role": "assistant", "content": None, "tool_calls": [tool_call], "reasoning_content": response.reasoning_content},
                        {"role": "tool", "tool_call_id": _tool_call_id,
                         "content": json.dumps({"error": _reason}, ensure_ascii=False)},
                    ]
                    messages.extend(_rej_msgs)
                    continue

                # 询问节奏控制 — request_confirmation 轰炸拦截（与 run_stream 一致）
                if _tc_name == "request_confirmation" and ask_count_this_round >= ASK_LIMIT:
                    _reason = (
                        f"本轮已连续询问用户 {ask_count_this_round} 次,达到节奏上限 "
                        f"{ASK_LIMIT}。请基于已有信息先给出初步诊断或推进到能推进的"
                        f"步骤,等用户在下一轮自然反馈后再问剩余问题。"
                    )
                    _tool_call_id = tool_call.get("id", "")
                    _rej_msgs = [
                        {"role": "assistant", "content": None, "tool_calls": [tool_call], "reasoning_content": response.reasoning_content},
                        {"role": "tool", "tool_call_id": _tool_call_id,
                         "content": json.dumps({"error": _reason, "blocked_by": "ask_pace_control"}, ensure_ascii=False)},
                    ]
                    messages.extend(_rej_msgs)
                    continue

                outcome = await self._tool_validator.validate(
                    tool_call, context, session_id, tier_a_failures, tools_used,
                    allowed_tools=allowed_tools,
                    session=session,
                    reasoning_content=response.reasoning_content,
                )
                # accepted 时 increment 同轮调用计数
                if outcome["kind"] == "accepted":
                    same_tool_counts[outcome["tool_name"]] = (
                        same_tool_counts.get(outcome["tool_name"], 0) + 1
                    )
                    # 询问节奏控制 — 记录本轮 request_confirmation 调用次数
                    if outcome["tool_name"] == "request_confirmation":
                        ask_count_this_round += 1
                # emit tool_call 事件(rejected 和 accepted 都 emit)
                await self._emit("tool_call", outcome["event_data"])
                # extend messages(outcome 已构造好 assistant+tool 序列)
                messages.extend(outcome["messages"])

                if outcome["kind"] == "accepted":
                    # P2-4 fix: 注入 session_id 供 launch_workflow 去重
                    # 只对 launch_workflow 注入 — 其他工具（如 list_datasets）
                    # 的 schema 不允许 session_id 字段，注入会触发 Schema validation 失败。
                    args_with_session = dict(outcome["arguments"])
                    if outcome["tool_name"] == "launch_workflow":
                        args_with_session.setdefault("session_id", session_id)
                    pending_executions.append({
                        "tool_call": outcome["tool_call"],
                        "tool_name": outcome["tool_name"],
                        "tool_call_id": outcome["tool_call_id"],
                        "arguments": args_with_session,
                    })
                # rejected_retry / rejected_fatal 已 emit + messages 已 extend,直接 continue
                continue

            # 阶段 2：并发执行 — 实际 execute 是最耗时部分，tool_calls 彼此独立可并发
            # 总耗时由最慢的工具决定，而非各工具耗时之和
            if pending_executions:
                results = await asyncio.gather(
                    *[self._tool_executor.execute_with_timeout(item["tool_name"], item["arguments"], session=session) for item in pending_executions],
                    return_exceptions=False,
                )
            else:
                results = []

            # 阶段 3：按 tool_calls 原始顺序处理结果，保持 messages / tools_used 顺序
            # （LLM 上下文顺序敏感）
            for item, (result, timed_out) in zip(pending_executions, results):
                tool_name = item["tool_name"]
                tool_call = item["tool_call"]
                tool_call_id = item["tool_call_id"]
                arguments = item["arguments"]

                if timed_out:
                    self._ratchet_recorder.record(
                        tool_name=tool_name,
                        trigger=TRIGGER_TIMEOUT,
                        failure_pattern=f"Tool execution exceeded {TOOL_TIMEOUT_SECONDS}s",
                        arguments_preview=self._ratchet_recorder.preview_arguments(arguments),
                        session_id=session_id,
                    )
                tools_used.append(tool_name)

                # P3-5 fix: launch_workflow 成功时收集 workflow_id 到 workflow_ids
                if workflow_ids is not None and tool_name == "launch_workflow" and result.output:
                    wf_id = result.output.get("workflow_id")
                    if wf_id and result.output.get("status") in ("launched", "COMPLETED", "RUNNING") and wf_id not in workflow_ids:
                        workflow_ids.append(wf_id)
                        # 工作流启动=阶段切换（设计→执行/标注），解锁 skill 让下轮重新匹配。
                        session.pop("locked_skill", None)

                # Phase 4 Guardrails: AfterToolHook — 工具结果护栏
                # 在工具结果回填给 LLM 前校验，REJECT 时替换为安全反馈
                result = await self._tool_executor.run_after_tool_hooks(
                    tool_name=tool_name,
                    arguments=arguments,
                    result=result,
                    session_id=session_id,
                )

                # 推送中间进度事件（如 workflow node 逐步完成）
                for pe in getattr(result, "progress_events", []) or []:
                    await self._emit("workflow_progress", pe)

                await self._emit("tool_result", {
                    "tool": tool_name,
                    "result": result.to_json(),
                    "error": result.error,
                    "error_type": result.error_type,
                })

                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                    "reasoning_content": response.reasoning_content,
                })
                messages.append({
                    "role": "tool", "tool_call_id": tool_call_id,
                    "content": json.dumps(result.to_json()),
                })

            # P0-3: 记录本轮推理轨迹
            if trajectory is not None:
                from cognitiveplane.control.trajectory import AgentStep
                step = AgentStep(
                    step_number=i + 1,
                    state="CALLING_TOOL" if tool_calls else "COMPLETED",
                    tool_calls=[{
                        "name": tc.get("function", {}).get("name", ""),
                        "arguments": tc.get("function", {}).get("arguments", ""),
                    } for tc in tool_calls],
                    tool_results=[{
                        "tool": item["tool_name"],
                        "error": result.error,
                        "error_type": result.error_type,
                    } for item, (result, _) in zip(pending_executions, results)] if pending_executions else [],
                    llm_usage={
                        "prompt_tokens": getattr(response, "usage", {}).get("prompt_tokens", 0) if hasattr(response, "usage") else 0,
                        "completion_tokens": getattr(response, "usage", {}).get("completion_tokens", 0) if hasattr(response, "usage") else 0,
                    } if hasattr(response, "usage") else None,
                )
                trajectory.record_step(step)

        # max_iterations reached — safety guardrail, not LLM choice.
        return InteractionResponse(
            text_reply="Max iterations reached. Please refine your request.",
            tools_used=tools_used,
            tier_used=tier,
            workflow_ids=workflow_ids or [],
        )

    @staticmethod
    def _coerce_text(user_input: str | list[dict]) -> str:
        """Flatten multimodal content to plain text (for tier 2/3 fallback).

        plan §2.3: image_url parts have no text payload; we keep text parts
        joined by newline. image_id references are already in the text payload
        (chat.py injects them), so the tool layer still works after degradation.
        """
        if isinstance(user_input, str):
            return user_input
        parts = [p.get("text", "") for p in user_input if p.get("type") == "text"]
        return "\n".join(p for p in parts if p)

    def _adapt_user_input(
        self, llm: Any, user_input: str | list[dict]
    ) -> str | list[dict]:
        """Adapt user_input to the LLM's vision capability.

        plan §2.3 dual-track + line 3166: ReActEngine accepts multimodal
        content. When the primary LLM is text-only (e.g. DeepSeek), image_url
        parts are filtered out at the engine layer — thumbnails stay in the
        tool layer via image_id references. When the primary is multimodal,
        full content is forwarded.
        """
        if isinstance(user_input, str):
            return user_input
        if getattr(llm, "supports_vision", False):
            return user_input
        # Text-only LLM: strip image_url parts, keep text.
        return self._coerce_text(user_input)

    async def _inject_history(
        self,
        messages: list[dict],
        session: dict[str, Any],
    ) -> tuple[list[dict], dict[str, Any] | None]:
        """注入历史对话，返回 (messages_after_injection, compaction_meta)。

        当配置了 self._compactor 且 history 接近 token 上限时，先调
        compact() 让 LLM 自己总结旧历史（保留架构决策/未解决 bug/实现细节，
        丢弃冗余工具输出），再注入。compaction_meta 含压缩前后 token 估算和
        采用的策略，供调用方 emit 事件让前端可见。

        参考 Anthropic Context Engineering 的 Compaction 技术：
        "把消息历史交给模型自己总结压缩，压缩后带上最近访问过的内容继续"。
        """
        history: list[dict] = session.get("history", []) if session else []
        if not history:
            return messages, None

        # 组装完整 messages 副本（system + history）供 compactor 评估
        full_for_compaction = list(messages)
        for msg in history:
            role = msg.get("role")
            content = msg.get("content")
            if role in ("user", "assistant") and content:
                new_msg = {"role": role, "content": content}
                # thinking 模式下 assistant 消息须保留 reasoning_content，
                # 否则下轮 API 调用会因缺少该字段而校验报错。
                if role == "assistant" and msg.get("reasoning_content"):
                    new_msg["reasoning_content"] = msg["reasoning_content"]
                full_for_compaction.append(new_msg)

        compaction_meta: dict[str, Any] | None = None
        compacted = full_for_compaction
        if self._compactor is not None:
            pre_tokens = self._compactor._estimate_tokens(full_for_compaction)
            try:
                compacted = await self._compactor.compact(
                    full_for_compaction, event_log=self._event_log,
                )
            except Exception:
                # 压缩失败不阻塞主流程 — 回退到全量注入
                compacted = full_for_compaction
            post_tokens = self._compactor._estimate_tokens(compacted)
            if compacted is not full_for_compaction:
                compaction_meta = {
                    "pre_tokens": pre_tokens,
                    "post_tokens": post_tokens,
                    "reduced": pre_tokens - post_tokens,
                }

        # compacted 含 system + (可能压缩的 history)，取非 system 部分追加
        # 到调用方传入的 messages（messages 已含 system prompt）
        for msg in compacted:
            if msg.get("role") == "system":
                continue  # system 已由 _build_system_prompt 构造，不重复注入
            new_msg = {"role": msg["role"], "content": msg["content"]}
            if msg.get("reasoning_content"):
                new_msg["reasoning_content"] = msg["reasoning_content"]
            messages.append(new_msg)

        return messages, compaction_meta

    async def _run_structured(
        self,
        user_input: str,
        context: ContextSnapshot,
        session: dict[str, Any],
        tools_used: list[str],
        skill: Skill | None = None,
    ) -> InteractionResponse:
        """Tier 2: Structured output intent analysis + rule-based routing."""
        llm = self._deps.capability.llm_provider
        if llm is None:
            return await self._run_embedding_rules(user_input, context, session, tools_used, skill=skill)

        # Simple intent classification via structured prompt
        intent_prompt = (
            f"Classify this user input into one of these intents: "
            f"knowledge_query, workflow_design, intervention, monitoring, free_chat.\n\n"
            f"User input: {user_input}\n\n"
            f"Respond with only the intent name."
        )
        llm_content: str | None = None
        try:
            response = await llm.complete(
                self._make_llm_request([{"role": "user", "content": intent_prompt}])
            )
            intent = response.content.strip().lower()
            llm_content = response.content
        except Exception:
            intent = "free_chat"

        # Route by intent
        # adjust_parameter removed 2026-06-26 (industrial verb → executionplane ToolPool,
        # boundary-pinning §2.1). "intervention" intent now routes to escalate (human handoff)
        # — adjusting production parameters is an industrial action requiring human gate.
        tool_map = {
            "knowledge_query": "search_standards",
            "workflow_design": "design_workflow",
            "intervention": "escalate",
            "monitoring": "read_weldmap",
        }
        tool_name = tool_map.get(intent)
        allowed_tools = set(skill.allowed_tools) if skill else None
        if tool_name and self._tools.is_llm_visible(tool_name) and (
            allowed_tools is None or tool_name in allowed_tools
        ):
            # design_workflow 入口形状是 WorkflowSpec 草案 (objective + reason),
            # 不是旧 query 形状 — fallback 路径必须用新入口, 否则 schema 校验拒绝.
            # 其它工具仍走 query 形状.
            if tool_name == "design_workflow":
                arguments = {
                    "objective": user_input,
                    "reason": f"Tier-2 structured-output fallback routing (intent={intent})",
                }
            else:
                arguments = {"query": user_input}
            result = await self._tools.execute(tool_name, arguments)
            tools_used.append(tool_name)
            return InteractionResponse(
                text_reply=json.dumps(result.to_json(), ensure_ascii=False),
                tools_used=tools_used,
                tier_used=InteractionTier.STRUCTURED_OUTPUT,
            )

        return InteractionResponse(
            text_reply=llm_content or user_input,
            tools_used=tools_used,
            tier_used=InteractionTier.STRUCTURED_OUTPUT,
        )

    async def _run_embedding_rules(
        self,
        user_input: str,
        context: ContextSnapshot,
        session: dict[str, Any],
        tools_used: list[str],
        skill: Skill | None = None,
    ) -> InteractionResponse:
        """Tier 3: Keyword matching + rule engine (no LLM)."""
        text = user_input.lower()
        tool_descriptions = {
            tool.name: tool.to_embedding_description()
            for tool in [self._tools.get_tool(n) for n in self._tools.list_tools()]
            if tool is not None
        }

        # Simple keyword-based matching
        keyword_map = {
            "search_standards": ["标准", "参数", "规范", "standard", "parameter"],
            "search_cases": ["案例", "历史", "类似", "case", "history"],
            "search_process": ["工艺", "流程", "process", "welding"],
            "read_weldmap": ["进度", "状态", "走到哪", "progress", "status"],
            "design_workflow": ["设计", "方案", "检测", "design", "workflow"],
            # adjust_parameter keywords merged into escalate (industrial action → human handoff)
            "escalate": ["升级", "人工", "escalate", "human", "调整", "修改", "干预", "adjust", "intervene"],
            "archive_memory": ["保存", "记忆", "存档", "save", "memory"],
            "explain_decision": ["解释", "为什么", "explain", "why"],
            "web_search": ["搜索", "查询", "最新", "网上", "search", "web", "latest"],
        }

        allowed_tools = set(skill.allowed_tools) if skill else None
        best_match = None
        best_score = 0
        for tool_name, keywords in keyword_map.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > best_score and self._tools.is_llm_visible(tool_name) and (
                allowed_tools is None or tool_name in allowed_tools
            ):
                best_score = score
                best_match = tool_name

        if best_match:
            # design_workflow 入口形状是 WorkflowSpec 草案 (objective + reason),
            # 不是旧 query 形状 — fallback 路径必须用新入口, 否则 schema 校验拒绝.
            if best_match == "design_workflow":
                arguments = {
                    "objective": user_input,
                    "reason": "Tier-3 keyword-rules fallback routing",
                }
            else:
                arguments = {"query": user_input}
            result = await self._tools.execute(best_match, arguments)
            tools_used.append(best_match)
            # P3-6 fix: 不把原始 JSON 当回复给用户(体验差),构造友好文本
            return InteractionResponse(
                text_reply=self._format_fallback_reply(best_match, result),
                tools_used=tools_used,
                tier_used=InteractionTier.EMBEDDING_RULES,
            )

        return InteractionResponse(
            text_reply="无法识别操作意图。请尝试更具体的描述，例如：查询标准参数、设计检测方案、查看产线进度。",
            tools_used=tools_used,
            tier_used=InteractionTier.EMBEDDING_RULES,
        )

    def _format_fallback_reply(self, tool_name: str, result: ToolResult) -> str:
        """P3-6 fix: Tier-3 fallback 友好回复格式化。

        原代码把 json.dumps(result.to_json()) 当回复给用户,体验差。
        改为按 tool 类型构造人类可读文本,失败时显式说明。
        """
        if result.error:
            return f"⚠️ 操作失败({tool_name}):{result.error}\n\n请换用更具体的描述重试,或联系人工支持。"
        output = result.output or {}
        # 按 tool 类型给友好摘要
        if tool_name == "search_standards":
            items = output.get("standards") or output.get("results") or []
            if isinstance(items, list) and items:
                names = [it.get("name", it.get("title", str(it)))[:40] if isinstance(it, dict) else str(it)[:40] for it in items[:5]]
                return f"✅ 查到 {len(items)} 条相关标准:\n" + "\n".join(f"  • {n}" for n in names)
            return "未查到匹配标准,请换关键词重试。"
        if tool_name == "search_cases":
            items = output.get("cases") or output.get("results") or []
            if isinstance(items, list) and items:
                return f"✅ 找到 {len(items)} 个相关案例(前 5 个见详情)。"
            return "未查到相关历史案例。"
        if tool_name == "design_workflow":
            wf_id = output.get("workflow_id", "")
            node_count = len(output.get("nodes", []))
            return f"✅ 已设计检测方案(workflow_id={wf_id}, {node_count} 个节点)。\n下一步:用 launch_workflow 启动该方案。"
        if tool_name == "read_weldmap":
            progress = output.get("progress") or output.get("status", "")
            return f"✅ 当前产线进度:{progress}" if progress else "暂无产线进度数据。"
        # 通用兜底
        return f"✅ 已执行 {tool_name}。\n详情:{json.dumps(output, ensure_ascii=False)[:200]}"

    def _make_llm_request(
        self,
        messages: list[dict],
        allowed_tools: list[str] | None = None,
    ) -> Any:
        """Build LLMRequest from messages.

        Phase 5 Agent Skills: 传入 allowed_tools 可限制 LLM 可见的工具白名单。
        ReAct 主路径覆盖 max_tokens/temperature，确保输出质量和长度。
        """
        from cognitiveplane.capability.provider import LLMRequest
        tool_defs = self._tools.get_llm_tool_definitions(allowed_tools=allowed_tools)
        return LLMRequest(
            messages=messages,
            tools=tool_defs if tool_defs else None,
            caller="react_engine",
            max_tokens=8192,
            temperature=0.3,  # 0.3 而非 0.7：Agent 场景需要高 system prompt 遵从度，
            # 0.7 下 LLM 容易忽略后段引导（委派 subagent / 知识库 fallback / 询问节奏）。
            # 0.3 既保留一定探索性，又确保 LLM 遵守 system prompt 中的决策框架。
            top_p=0.95,
        )
