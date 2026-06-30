"""ReActEngine — Reasoning+Acting loop with 3-tier fallback.

Source: 7-plane redesign spec §7 Interaction — ReAct.

Tier 1: Full ReAct with Function Calling (LLM available)
Tier 2: Structured output intent analysis + rule-based routing
Tier 3: Semantic embedding + rule engine (no LLM)
"""

import asyncio
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from cognitiveplane.control.deps import CognitiveDependencies
from cognitiveplane.control.hooks import BeforeToolHook, HookDecision, HookResult
from cognitiveplane.control.tool_registry import ToolRegistry
from cognitiveplane.control.tools import ToolResult
from cognitiveplane.governance.policy_ratchet import (
    PolicyRatchet,
    TRIGGER_CAS_CONFLICT,
    TRIGGER_REASK_REJECT,
    TRIGGER_SCHEMA_FAIL,
    TRIGGER_TIMEOUT,
)
from cognitiveplane.shared.dto.context import ContextSnapshot

if TYPE_CHECKING:
    from cognitiveplane.control.event_log import EventLog
    from cognitiveplane.interaction.image_store import ImageStore

EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]

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

# Plan §4.2.2 line 819-822: ratchet trigger thresholds
TOOL_TIMEOUT_SECONDS = 30.0  # §4.2.2: "工具调用超时 (timeout > 30s)"

# Plan §5.1 line 894-902: Worldview files to auto-inject
WELDEVENT_MD_PATH = Path("WELDEVENT.md")
OPERATOR_MD_PATH = Path("OPERATOR.md")


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
        max_iterations: int = 6,
        event_callback: EventCallback | None = None,
        image_store: "ImageStore | None" = None,
        event_log: "EventLog | None" = None,
        policy_ratchet: PolicyRatchet | None = None,
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
        """
        self._deps = deps
        self._tools = tool_registry or ToolRegistry(deps, image_store=image_store)
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

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Fire event callback if registered. Swallow callback errors —
        streaming must never break the core ReAct loop."""
        if self._event_callback is None:
            return
        try:
            await self._event_callback(event_type, payload)
        except Exception:
            pass

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

        if tier == InteractionTier.REACT_FUNCTION_CALLING:
            response = await self._run_react(user_input, context, session or {}, tier, tools_used, workflow_ids)
        elif tier == InteractionTier.STRUCTURED_OUTPUT:
            response = await self._run_structured(self._coerce_text(user_input), context, session or {}, tools_used)
        else:
            response = await self._run_embedding_rules(self._coerce_text(user_input), context, session or {}, tools_used)

        await self._emit("final", {
            "reply": response.text_reply,
            "tools_used": response.tools_used,
            "tier": response.tier_used.value,
            "error": response.error,
        })
        return response

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

        messages = await self._build_system_prompt(context, session)
        # 注入历史对话（与 _run_react 一致），避免 LLM 失忆
        history: list[dict] = session.get("history", []) if session else []
        for msg in history:
            role = msg.get("role")
            content = msg.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": self._adapt_user_input(llm, user_input)})

        tier_a_failures: dict[str, int] = {}
        final_reply = ""
        for i in range(self._max_iterations):
            yield {"event": "thinking", "data": {"iteration": i + 1, "tools_so_far": list(tools_used)}}

            try:
                response = await llm.complete(self._make_llm_request(messages))
            except Exception as e:
                yield {"event": "error", "data": {"message": f"LLM error: {e}"}}
                return

            tool_calls = response.tool_calls
            if not tool_calls:
                # 最终轮 — LLM 决定不再调工具。
                # 调 stream() 真流式输出 token（同 messages），给用户打字机体验。
                # stream 失败时回退到 complete() 已有的 content。
                try:
                    async for token in llm.stream(self._make_llm_request(messages)):
                        if token:
                            final_reply += token
                            yield {"event": "token", "data": {"content": token}}
                except Exception:
                    final_reply = response.content
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
                return

            # 工具轮 — 并发执行多个 tool_call（与 _run_react 逻辑一致）
            # 阶段 1：顺序校验，收集待执行项（rejected / retry 仍在校验阶段立即处理，不进入并发）
            pending_executions: list[dict] = []
            for tool_call in tool_calls:
                tool_name = tool_call.get("function", {}).get("name", "")
                tool_call_id = tool_call.get("id", "")
                arguments_str = tool_call.get("function", {}).get("arguments", "{}")

                if not self._tools.is_llm_visible(tool_name):
                    yield {
                        "event": "tool_call",
                        "data": {
                            "tool": tool_name,
                            "arguments": {},
                            "rejected": True,
                            "reason": f"Tool {tool_name} is not available in the current phase",
                        },
                    }
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps({
                            "rejected": True,
                            "reason": f"Tool {tool_name} is not available in the current phase.",
                        }),
                    })
                    continue

                try:
                    arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
                    parse_error: str | None = None
                except json.JSONDecodeError as e:
                    arguments = {}
                    parse_error = f"Invalid JSON arguments: {e}. Raw: {arguments_str[:200]}"

                if parse_error is not None:
                    is_tier_a = tool_name in TIER_A_TOOLS
                    failures_so_far = tier_a_failures.get(tool_name, 0)
                    if is_tier_a and failures_so_far < MAX_TIER_A_RETRIES:
                        tier_a_failures[tool_name] = failures_so_far + 1
                        yield {
                            "event": "tool_call",
                            "data": {
                                "tool": tool_name, "arguments": arguments,
                                "rejected": True, "reason": parse_error, "retry": True,
                            },
                        }
                        messages.append({"role": "assistant", "content": None, "tool_calls": [tool_call]})
                        messages.append({
                            "role": "tool", "tool_call_id": tool_call_id,
                            "content": json.dumps({"error": parse_error, "hint": "Please retry with valid JSON arguments matching the tool schema."}),
                        })
                        continue
                    tier_a_failures[tool_name] = 0
                    tools_used.append(tool_name)
                    self._record_ratchet(
                        tool_name=tool_name,
                        trigger=TRIGGER_SCHEMA_FAIL if is_tier_a else TRIGGER_REASK_REJECT,
                        failure_pattern=parse_error,
                        arguments_preview={"raw": arguments_str[:200]} if isinstance(arguments_str, str) else {},
                        session_id=session_id,
                    )
                    yield {
                        "event": "tool_call",
                        "data": {
                            "tool": tool_name, "arguments": arguments,
                            "rejected": True, "reason": parse_error, "retry": False,
                        },
                    }
                    messages.append({"role": "assistant", "content": None, "tool_calls": [tool_call]})
                    messages.append({
                        "role": "tool", "tool_call_id": tool_call_id,
                        "content": json.dumps({"error": parse_error, "fatal": True, "hint": "Tool call rejected. Use a different approach or tool."}),
                    })
                    continue

                if tool_name in TIER_A_TOOLS:
                    tier_a_failures[tool_name] = 0

                schema_error = self._check_schema(tool_name, arguments)
                if schema_error is not None:
                    is_tier_a = tool_name in TIER_A_TOOLS
                    failures_so_far = tier_a_failures.get(tool_name, 0)
                    if is_tier_a and failures_so_far < MAX_TIER_A_RETRIES:
                        tier_a_failures[tool_name] = failures_so_far + 1
                        yield {
                            "event": "tool_call",
                            "data": {
                                "tool": tool_name, "arguments": arguments,
                                "rejected": True, "reason": schema_error,
                                "retry": True, "schema_invalid": True,
                            },
                        }
                        messages.append({"role": "assistant", "content": None, "tool_calls": [tool_call]})
                        messages.append({
                            "role": "tool", "tool_call_id": tool_call_id,
                            "content": json.dumps({"error": schema_error, "hint": "Arguments failed JSON Schema validation. Please retry with valid arguments."}),
                        })
                        continue
                    tier_a_failures[tool_name] = 0
                    tools_used.append(tool_name)
                    self._record_ratchet(
                        tool_name=tool_name,
                        trigger=TRIGGER_SCHEMA_FAIL if is_tier_a else TRIGGER_REASK_REJECT,
                        failure_pattern=schema_error,
                        arguments_preview=self._preview_arguments(arguments),
                        session_id=session_id,
                    )
                    yield {
                        "event": "tool_call",
                        "data": {
                            "tool": tool_name, "arguments": arguments,
                            "rejected": True, "reason": schema_error,
                            "retry": False, "schema_invalid": True,
                        },
                    }
                    messages.append({"role": "assistant", "content": None, "tool_calls": [tool_call]})
                    messages.append({
                        "role": "tool", "tool_call_id": tool_call_id,
                        "content": json.dumps({"error": schema_error, "fatal": True, "hint": "Schema-invalid arguments rejected. Use a different approach or tool."}),
                    })
                    continue

                hook_result = await self._run_hooks(tool_name, arguments, context, session_id=session_id)
                if hook_result.decision == HookDecision.DENY:
                    if tool_name in TIER_B_TOOLS:
                        self._record_ratchet(
                            tool_name=tool_name,
                            trigger=TRIGGER_REASK_REJECT,
                            failure_pattern=f"PolicyHook DENY: {hook_result.reason}",
                            arguments_preview=self._preview_arguments(arguments),
                            session_id=session_id,
                        )
                    yield {
                        "event": "tool_call",
                        "data": {
                            "tool": tool_name, "arguments": arguments,
                            "rejected": True, "reason": hook_result.reason,
                        },
                    }
                    messages.append({
                        "role": "tool", "tool_call_id": tool_call_id,
                        "content": json.dumps({"rejected": True, "reason": hook_result.reason}),
                    })
                    continue

                # 通过所有校验 — emit accepted tool_call，加入待执行列表（不立即 execute）
                yield {"event": "tool_call", "data": {"tool": tool_name, "arguments": arguments}}
                pending_executions.append({
                    "tool_call": tool_call,
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "arguments": arguments,
                })
                continue

            # 阶段 2：并发执行 — 实际 execute 是最耗时部分，tool_calls 彼此独立可并发
            # 总耗时由最慢的工具决定，而非各工具耗时之和
            if pending_executions:
                results = await asyncio.gather(
                    *[self._execute_with_timeout(item["tool_name"], item["arguments"]) for item in pending_executions],
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
                    self._record_ratchet(
                        tool_name=tool_name,
                        trigger=TRIGGER_TIMEOUT,
                        failure_pattern=f"Tool execution exceeded {TOOL_TIMEOUT_SECONDS}s",
                        arguments_preview=self._preview_arguments(arguments),
                        session_id=session_id,
                    )
                tools_used.append(tool_name)

                if tool_name == "launch_workflow" and result.output:
                    wf_id = result.output.get("workflow_id")
                    if wf_id and result.output.get("status") == "launched" and wf_id not in workflow_ids:
                        workflow_ids.append(wf_id)

                yield {
                    "event": "tool_result",
                    "data": {
                        "tool": tool_name,
                        "result": result.to_json(),
                        "error": result.error,
                        "error_type": result.error_type,
                    },
                }

                messages.append({"role": "assistant", "content": None, "tool_calls": [tool_call]})
                messages.append({
                    "role": "tool", "tool_call_id": tool_call_id,
                    "content": json.dumps(result.to_json()),
                })

        # max_iterations reached — 安全兜底
        final_reply = "Max iterations reached. Please refine your request."
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
    ) -> InteractionResponse:
        """Tier 1: Full ReAct with Function Calling."""
        llm = self._deps.capability.llm_provider
        if llm is None:
            return await self._run_embedding_rules(self._coerce_text(user_input), context, session, tools_used)

        # Real session_id for max_calls_per_session enforcement (plan §4.2 line 761)
        session_id = str(session.get("session_id", "default")) if session else "default"

        messages = await self._build_system_prompt(context, session)
        # 注入历史对话（chat.py 从 session.messages 传入），让 LLM 能看到之前几轮
        # 上下文，避免每轮都是无状态对话导致"失忆"。
        history: list[dict] = session.get("history", []) if session else []
        for msg in history:
            # 只保留 role + content，过滤掉 tool_call_id 等内部字段
            role = msg.get("role")
            content = msg.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": self._adapt_user_input(llm, user_input)})

        # Plan §3.6 line 601: per-tool_name consecutive failure counter
        # Tier-A: 1 malformed → emit error → LLM retries → if still malformed → ToolFailureObservation
        tier_a_failures: dict[str, int] = {}
        for i in range(self._max_iterations):
            await self._emit("thinking", {
                "iteration": i + 1,
                "tools_so_far": list(tools_used),
            })
            try:
                response = await llm.complete(
                    self._make_llm_request(messages)
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
                )

            # 阶段 1：顺序校验，收集待执行项（rejected / retry 仍在校验阶段立即处理，不进入并发）
            pending_executions: list[dict] = []
            for tool_call in tool_calls:
                tool_name = tool_call.get("function", {}).get("name", "")
                tool_call_id = tool_call.get("id", "")
                arguments_str = tool_call.get("function", {}).get("arguments", "{}")
                if not self._tools.is_llm_visible(tool_name):
                    await self._emit("tool_call", {
                        "tool": tool_name,
                        "arguments": {},
                        "rejected": True,
                        "reason": f"Tool {tool_name} is not available in the current phase",
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps({
                            "rejected": True,
                            "reason": f"Tool {tool_name} is not available in the current phase.",
                        }),
                    })
                    continue
                try:
                    arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
                    parse_error: str | None = None
                except json.JSONDecodeError as e:
                    arguments = {}
                    parse_error = f"Invalid JSON arguments: {e}. Raw: {arguments_str[:200]}"

                # Plan §3.6 line 601: Tier-A schema 校验失败 → 自动 retry 1 次;
                # Tier-B 直接拒绝, 不重试
                if parse_error is not None:
                    is_tier_a = tool_name in TIER_A_TOOLS
                    failures_so_far = tier_a_failures.get(tool_name, 0)
                    if is_tier_a and failures_so_far < MAX_TIER_A_RETRIES:
                        # First failure — emit error, let LLM retry in next iteration
                        tier_a_failures[tool_name] = failures_so_far + 1
                        await self._emit("tool_call", {
                            "tool": tool_name,
                            "arguments": arguments,
                            "rejected": True,
                            "reason": parse_error,
                            "retry": True,
                        })
                        messages.append({
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [tool_call],
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": json.dumps({
                                "error": parse_error,
                                "hint": "Please retry with valid JSON arguments matching the tool schema.",
                            }),
                        })
                        continue
                    # Tier-B OR Tier-A exhausted retries → ToolFailureObservation
                    # Reset counter so next attempt is fresh (§3.6: "进入 ToolFailureObservation, LLM 自主决定下一步")
                    tier_a_failures[tool_name] = 0
                    tools_used.append(tool_name)
                    # Plan §4.2.2 line 819-820: ratchet trigger
                    # - Tier-A 连续 2 次 schema fail → schema_fail
                    # - Tier-B schema fail → reask_reject (§3.6 line 660: Tier-B 走 OnFailAction.REASK)
                    self._record_ratchet(
                        tool_name=tool_name,
                        trigger=TRIGGER_SCHEMA_FAIL if is_tier_a else TRIGGER_REASK_REJECT,
                        failure_pattern=parse_error,
                        arguments_preview={"raw": arguments_str[:200]} if isinstance(arguments_str, str) else {},
                        session_id=session_id,
                    )
                    await self._emit("tool_call", {
                        "tool": tool_name,
                        "arguments": arguments,
                        "rejected": True,
                        "reason": parse_error,
                        "retry": False,
                    })
                    messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [tool_call],
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps({
                            "error": parse_error,
                            "fatal": True,
                            "hint": "Tool call rejected. Use a different approach or tool.",
                        }),
                    })
                    continue

                # Successful JSON parse — reset Tier-A failure counter for this tool
                if tool_name in TIER_A_TOOLS:
                    tier_a_failures[tool_name] = 0

                # Plan §3.5 原则 5: schema 校验 (ToolRegistry.validate_arguments).
                # JSON 解析成功但 schema 不合规 (缺 required / 类型错 / enum 非法) →
                # 走与 JSON parse 失败相同的 Tier-A retry / Tier-B reject 路径 (plan §3.6 line 653).
                schema_error = self._check_schema(tool_name, arguments)
                if schema_error is not None:
                    is_tier_a = tool_name in TIER_A_TOOLS
                    failures_so_far = tier_a_failures.get(tool_name, 0)
                    if is_tier_a and failures_so_far < MAX_TIER_A_RETRIES:
                        tier_a_failures[tool_name] = failures_so_far + 1
                        await self._emit("tool_call", {
                            "tool": tool_name,
                            "arguments": arguments,
                            "rejected": True,
                            "reason": schema_error,
                            "retry": True,
                            "schema_invalid": True,
                        })
                        messages.append({
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [tool_call],
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": json.dumps({
                                "error": schema_error,
                                "hint": "Arguments failed JSON Schema validation. Please retry with valid arguments.",
                            }),
                        })
                        continue
                    # Tier-B OR Tier-A exhausted retries → ToolFailureObservation + ratchet
                    tier_a_failures[tool_name] = 0
                    tools_used.append(tool_name)
                    self._record_ratchet(
                        tool_name=tool_name,
                        trigger=TRIGGER_SCHEMA_FAIL if is_tier_a else TRIGGER_REASK_REJECT,
                        failure_pattern=schema_error,
                        arguments_preview=self._preview_arguments(arguments),
                        session_id=session_id,
                    )
                    await self._emit("tool_call", {
                        "tool": tool_name,
                        "arguments": arguments,
                        "rejected": True,
                        "reason": schema_error,
                        "retry": False,
                        "schema_invalid": True,
                    })
                    messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [tool_call],
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps({
                            "error": schema_error,
                            "fatal": True,
                            "hint": "Schema-invalid arguments rejected. Use a different approach or tool.",
                        }),
                    })
                    continue

                # Run hooks — first DENY wins
                hook_result = await self._run_hooks(tool_name, arguments, context, session_id=session_id)
                if hook_result.decision == HookDecision.DENY:
                    # Plan §4.2.2 line 820: Tier-B 被 OnFailAction.REASK 拒绝 → reask_reject
                    if tool_name in TIER_B_TOOLS:
                        self._record_ratchet(
                            tool_name=tool_name,
                            trigger=TRIGGER_REASK_REJECT,
                            failure_pattern=f"PolicyHook DENY: {hook_result.reason}",
                            arguments_preview=self._preview_arguments(arguments),
                            session_id=session_id,
                        )
                    await self._emit("tool_call", {
                        "tool": tool_name,
                        "arguments": arguments,
                        "rejected": True,
                        "reason": hook_result.reason,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps({
                            "rejected": True,
                            "reason": hook_result.reason,
                        }),
                    })
                    continue

                # 通过所有校验 — emit accepted tool_call，加入待执行列表（不立即 execute）
                await self._emit("tool_call", {
                    "tool": tool_name,
                    "arguments": arguments,
                })
                pending_executions.append({
                    "tool_call": tool_call,
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "arguments": arguments,
                })
                continue

            # 阶段 2：并发执行 — 实际 execute 是最耗时部分，tool_calls 彼此独立可并发
            # 总耗时由最慢的工具决定，而非各工具耗时之和
            if pending_executions:
                results = await asyncio.gather(
                    *[self._execute_with_timeout(item["tool_name"], item["arguments"]) for item in pending_executions],
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
                    self._record_ratchet(
                        tool_name=tool_name,
                        trigger=TRIGGER_TIMEOUT,
                        failure_pattern=f"Tool execution exceeded {TOOL_TIMEOUT_SECONDS}s",
                        arguments_preview=self._preview_arguments(arguments),
                        session_id=session_id,
                    )
                tools_used.append(tool_name)

                # P3-5 fix: launch_workflow 成功时收集 workflow_id 到 workflow_ids
                if workflow_ids is not None and tool_name == "launch_workflow" and result.output:
                    wf_id = result.output.get("workflow_id")
                    if wf_id and result.output.get("status") == "launched" and wf_id not in workflow_ids:
                        workflow_ids.append(wf_id)

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
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(result.to_json()),
                })

        # max_iterations reached — safety guardrail, not LLM choice.
        return InteractionResponse(
            text_reply="Max iterations reached. Please refine your request.",
            tools_used=tools_used,
            tier_used=tier,
            workflow_ids=workflow_ids or [],
        )

    async def _run_hooks(
        self, tool_name: str, arguments: dict, context: ContextSnapshot, session_id: str = "default"
    ) -> HookResult:
        """Run all beforeTool hooks in order. First DENY wins.

        session_id: real per-session identifier for max_calls_per_session enforcement
            (plan §4.2 line 761). Defaults to "default" when session dict missing.
        """
        for hook in self._hooks:
            result = await hook.before_execute(tool_name, arguments, context, session_id=session_id)
            if result.decision == HookDecision.DENY:
                return result
        return HookResult(decision=HookDecision.ALLOW)

    def _check_schema(self, tool_name: str, arguments: dict) -> str | None:
        """Plan §3.5 原则 5: pre-validate arguments against tool schema.

        Returns error message if invalid, None if valid or tool unknown (unknown
        tools fall through to ToolRegistry.execute which returns Unknown tool error).
        """
        tool = self._tools.get_tool(tool_name)
        if tool is None:
            # Unknown tool — let ToolRegistry.execute return the Unknown tool error.
            return None
        try:
            is_valid, error = self._tools.validate_arguments(tool_name, arguments)
        except Exception:
            # Validation itself failed (e.g. malformed schema) — don't block execution,
            # let tool.execute handle it. Schema validation is a safety net, not a gate.
            return None
        return error if not is_valid else None

    async def _execute_with_timeout(
        self, tool_name: str, arguments: dict
    ) -> tuple[ToolResult, bool]:
        """Execute tool with §4.2.2 timeout guard.

        Returns (result, timed_out). On timeout, returns a synthetic ToolResult
        with error so the ReAct loop continues — the LLM sees the timeout as a
        tool failure observation and decides next step autonomously.
        """
        import asyncio
        try:
            result = await asyncio.wait_for(
                self._tools.execute(tool_name, arguments),
                timeout=TOOL_TIMEOUT_SECONDS,
            )
            return result, False
        except asyncio.TimeoutError:
            return ToolResult(
                error=f"Tool '{tool_name}' timed out after {TOOL_TIMEOUT_SECONDS}s",
                error_type="tool_timeout",
            ), True

    def _record_ratchet(
        self,
        tool_name: str,
        trigger: str,
        failure_pattern: str,
        arguments_preview: dict[str, Any] | None = None,
        session_id: str = "",
    ) -> None:
        """Plan §4.2.2 line 824-828: append draft entry. Swallow errors —
        ratchet failure must never break the ReAct loop.

        P2-10 fix: 透传 session_id 让草稿记录可区分 session。
        """
        try:
            self._ratchet.record(
                tool_name=tool_name,
                trigger_event=trigger,
                failure_pattern=failure_pattern,
                suggested_rule=self._suggest_rule(tool_name, trigger),
                arguments_preview=arguments_preview or {},
                session_id=session_id,
            )
        except Exception:
            pass

    @staticmethod
    def _suggest_rule(tool_name: str, trigger: str) -> str:
        """Heuristic suggested_rule for Phase 5+ 评审. Not auto-applied."""
        if trigger == TRIGGER_SCHEMA_FAIL:
            return f"Consider tightening {tool_name} parameter schema or adding examples"
        if trigger == TRIGGER_REASK_REJECT:
            return f"Consider pre-validating {tool_name} required fields before LLM call"
        if trigger == TRIGGER_TIMEOUT:
            return f"Consider adding async timeout hint to {tool_name} description or splitting work"
        if trigger == TRIGGER_CAS_CONFLICT:
            return f"Consider retry policy for {tool_name} on CAS conflict"
        return ""

    @staticmethod
    def _preview_arguments(arguments: dict) -> dict[str, Any]:
        """Truncate argument values for ratchet log. Avoids leaking large payloads."""
        preview: dict[str, Any] = {}
        for k, v in arguments.items():
            if isinstance(v, str) and len(v) > 100:
                preview[k] = v[:100] + "...(truncated)"
            elif isinstance(v, (dict, list)):
                s = json.dumps(v, ensure_ascii=False, default=str)
                preview[k] = s[:100] + "...(truncated)" if len(s) > 100 else v
            else:
                preview[k] = v
        return preview

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

    async def _run_structured(
        self,
        user_input: str,
        context: ContextSnapshot,
        session: dict[str, Any],
        tools_used: list[str],
    ) -> InteractionResponse:
        """Tier 2: Structured output intent analysis + rule-based routing."""
        llm = self._deps.capability.llm_provider
        if llm is None:
            return await self._run_embedding_rules(user_input, context, session, tools_used)

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
        if tool_name and self._tools.is_llm_visible(tool_name):
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

        best_match = None
        best_score = 0
        for tool_name, keywords in keyword_map.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > best_score and self._tools.is_llm_visible(tool_name):
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
            return InteractionResponse(
                text_reply=json.dumps(result.to_json(), ensure_ascii=False),
                tools_used=tools_used,
                tier_used=InteractionTier.EMBEDDING_RULES,
            )

        return InteractionResponse(
            text_reply="无法识别操作意图。请尝试更具体的描述，例如：查询标准参数、设计检测方案、查看产线进度。",
            tools_used=tools_used,
            tier_used=InteractionTier.EMBEDDING_RULES,
        )

    async def _build_system_prompt(
        self, context: ContextSnapshot, session: dict[str, Any]
    ) -> list[dict[str, str]]:
        """Build system prompt with worldview injection + context + tools.

        Plan §5.1 line 894-902: ReAct Loop 启动时自动注入:
          - WELDEVENT.md (项目级规约)
          - OPERATOR.md (操作员偏好)
          - CASE_BRIEF (当前 case 简报)
          - Memory.search (相关历史修正/类似案例)

        注入方式: 提供"世界观"，不规定"步骤"
        关键: 这是上下文，不是流水线第一步 — 即使 LLM 完全不引用也算正确行为
        所有 section 都是 optional，缺失文件/空数据/Memory 故障时优雅跳过。

        Plan §0.1 规则 1 (line 145): 架构替 LLM 做的看不见的事必须记进 EventLog.
        本方法在注入成功后 emit 一个 STATE_TRANSITION 事件, 记录注入了哪些
        section + Memory hit ids, LLM 下一轮可查.
        """
        tools_desc = "\n".join(
            f"- {name}: {self._tools.get_tool(name).description}"
            for name in self._tools.list_llm_tools()
            if self._tools.get_tool(name) is not None
        )

        sections: list[str] = []

        # ── Worldview sections (plan §5.1 line 894-902) ──
        worldview, injected_meta = await self._build_worldview_section(context)
        if worldview:
            sections.append(worldview)
            # §0.1 规则 1: 记进 EventLog, LLM 下一轮可查
            self._record_worldview_injection(injected_meta)

        # ── L3 Activity Catalog (LLM 自主编排工作流的能力边界) ──
        # 用户是业务人员，不会明确指定 capability 名。LLM 需参考本 catalog
        # 自主选择 activity，通过 design_workflow 的 nodes 参数传入编排结果。
        # 已实现的 activity 真实执行；未实现的虚拟执行（返回占位结果，不报错）。
        sections.append(_build_activity_catalog_section())

        # ── Tools + algorithmic flow (Claude Code "write algorithms not rules") ──
        sections.append(
            "你可以使用以下工具来完成任务：\n\n"
            f"{tools_desc}\n\n"
            "任务执行流程：\n"
            "1. 阅读用户问题，判断是否需要外部信息\n"
            "   - 不需要 → 直接回答\n"
            "   - 需要 → 进入步骤 2\n"
            "2. 选择最相关的 1 个工具调用\n"
            "3. 检查工具返回：\n"
            "   - 信息足够 → 进入步骤 4\n"
            "   - 信息不够 → 换一个工具，回到步骤 2\n"
            "   - 工具失败 → 说明失败原因，决定重试或换工具\n"
            "4. 综合工具结果，用中文给出最终答案\n\n"
            "约束：\n"
            "- 不重复调用同一工具的相同参数\n"
            "- 优先用工具拿信息再回答，不要凭空猜测\n"
            "- 当用户用业务语言提工作流需求（如'检查这张图'/'预处理一下'/'帮我标注'），"
            "调用 design_workflow 时通过 `nodes` 参数显式编排，从上方 L3 Activity Catalog "
            "选择 capability，不要等用户指定节点名\n"
        )

        return [
            {
                "role": "system",
                "content": (
                    "你是 WeldEvent 工业质检Agent系统的决策引擎。\n\n"
                    + "\n\n".join(sections)
                ),
            }
        ]

    def _record_worldview_injection(self, meta: dict[str, Any]) -> None:
        """§0.1 规则 1: 架构替 LLM 做的看不见的事记进 EventLog.

        复用 STATE_TRANSITION 枚举 + data.phase="worldview_injected" 标记,
        不新增事件类型 (避免扩散到 DecisionPipelineView.apply).
        """
        if self._event_log is None:
            return
        try:
            from cognitiveplane.control.event_log import BrainEventType
            self._event_log.emit(
                BrainEventType.STATE_TRANSITION,
                source="react_engine",
                data={"phase": "worldview_injected", **meta},
            )
        except Exception:
            # EventLog failure must never break ReAct
            pass

    async def _build_worldview_section(
        self, context: ContextSnapshot
    ) -> tuple[str | None, dict[str, Any]]:
        """Plan §5.1 line 894-902: build worldview section from 4 sources.

        Returns (section_text, injection_metadata). section_text is None if all
        4 sources are empty/missing — caller skips the section. injection_metadata
        always populated for EventLog recording (§0.1 规则 1).
        """
        blocks: list[str] = []
        injected_sections: list[str] = []
        memory_hit_ids: list[str] = []

        # 1. WELDEVENT.md — 项目级规约
        weldevent_md = _read_md_if_exists(WELDEVENT_MD_PATH)
        if weldevent_md:
            blocks.append("## 项目规约 (WELDEVENT.md)\n\n" + weldevent_md)
            injected_sections.append("weldevent_md")

        # 2. OPERATOR.md — 操作员偏好
        operator_md = _read_md_if_exists(OPERATOR_MD_PATH)
        if operator_md:
            blocks.append("## 操作员偏好 (OPERATOR.md)\n\n" + operator_md)
            injected_sections.append("operator_md")

        # 3. CASE_BRIEF — 当前 case 简报 (从 context 构造)
        case_brief = _format_case_brief(context)
        if case_brief:
            blocks.append("## 当前 Case 简报\n\n" + case_brief)
            injected_sections.append("case_brief")

        # 4. Memory.search — 相关历史修正/类似案例
        memory_hits, memory_hit_ids = await self._fetch_memory_hits(context)
        if memory_hits:
            blocks.append("## 相关历史 (Memory.search)\n\n" + memory_hits)
            injected_sections.append("memory_search")

        meta: dict[str, Any] = {
            "sections": injected_sections,
            "memory_hit_ids": memory_hit_ids,
            "memory_hit_count": len(memory_hit_ids),
        }

        if not blocks:
            return None, meta
        return "# 世界观 (上下文，不是步骤)\n\n" + "\n\n".join(blocks), meta

    async def _fetch_memory_hits(
        self, context: ContextSnapshot
    ) -> tuple[str | None, list[str]]:
        """Plan §5.1 line 901: Memory.search 相关历史修正/类似案例.

        Returns (formatted_text, memory_hit_ids). Both are None/empty if
        MemoryDeps.search is None, raises, or returns empty.
        """
        memory_search = self._deps.memory.search
        if memory_search is None:
            return None, []
        try:
            from cognitiveplane.shared.ports.memory import (
                MemorySearchInput, MemorySearchQuery,
            )
            query = MemorySearchInput(
                query=MemorySearchQuery(
                    case_features={
                        "case_id": context.case_id.value,
                        "event_type": context.event_type.value,
                    },
                    feature_vector=[0.0],  # Phase 2: in-memory stub ignores vector
                    max_results=3,
                    min_confidence=0.5,
                )
            )
            output = await memory_search.search(query)
        except Exception:
            # Memory failure must never break ReAct (plan §5.1: worldview is optional)
            return None, []

        if not output.results:
            return None, []

        lines: list[str] = []
        hit_ids: list[str] = []
        for i, hit in enumerate(output.results, 1):
            content_preview = _format_memory_content(hit.content)
            lines.append(
                f"{i}. [相似度 {hit.similarity_score:.2f} | 状态 {hit.promotion_status.value}] "
                f"{content_preview}"
            )
            hit_id = str(getattr(hit.memory_id, "value", hit.memory_id))
            hit_ids.append(hit_id)
        return "\n".join(lines), hit_ids

    def _make_llm_request(self, messages: list[dict]) -> Any:
        """Build LLMRequest from messages."""
        from cognitiveplane.capability.provider import LLMRequest
        tool_defs = self._tools.get_llm_tool_definitions()
        return LLMRequest(
            messages=messages,
            tools=tool_defs if tool_defs else None,
            caller="react_engine",
        )


# ---------------------------------------------------------------------------
# Module-level helpers for §5.1 worldview injection
# ---------------------------------------------------------------------------


def _read_md_if_exists(path: Path) -> str | None:
    """Return file content if exists, else None. Phase 2: files are optional."""
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except Exception:
        return None


def _build_activity_catalog_section() -> str:
    """Build L3 Activity Catalog section for system prompt.

    LLM 自主编排工作流的能力边界：
      - 用户用业务语言提需求（如"这张图有没有问题"）
      - LLM 参考 catalog 自动选择 activity，通过 design_workflow 的 nodes
        参数传入编排结果（capability + depends_on）
      - 已实现(✅)的 activity 真实执行；未实现(⚠️)的虚拟执行（占位返回）
    """
    try:
        from cognitiveplane.control.tools.activity_catalog import get_catalog_text
        catalog = get_catalog_text()
    except Exception:
        # Activity catalog 不可用时静默跳过（不应阻塞 ReAct 主循环）
        return ""

    return (
        "# L3 执行层能力边界 (Activity Catalog)\n\n"
        "用户是业务人员，不会明确指定 capability 名。当用户的请求需要编排工作流时，"
        "你应参考下表自动选择 activity，并通过 design_workflow 工具的 `nodes` 参数"
        "传入编排结果（每个 node 含 capability + depends_on）。"
        "已实现的 activity 会真实执行；未实现的会虚拟执行（返回占位结果，不报错），"
        "因此你可以放心选择，不必担心未实现的 capability 导致失败。\n\n"
        f"{catalog}\n"
        "编排要点：\n"
        "- 节点依赖关系用 depends_on 引用前置 node_id（如 PPA 依赖 IQA 报告）\n"
        "- image_refs 由工具自动注入到每个 tool_task 节点，无需在 input_data 重复\n"
        "- on_failure 默认 'retry'，关键节点可显式设为 'escalate'\n"
    )


def _format_case_brief(context: ContextSnapshot) -> str | None:
    """Format CASE_BRIEF from ContextSnapshot. Return None if context is empty."""
    parts: list[str] = []
    case_id = getattr(context.case_id, "value", context.case_id)
    if case_id and str(case_id) != "unknown":
        parts.append(f"- Case ID: {case_id}")
    event_type = getattr(context.event_type, "value", context.event_type)
    if event_type:
        parts.append(f"- Event Type: {event_type}")
    if context.workflow_state:
        parts.append(f"- Workflow State: {json.dumps(context.workflow_state, ensure_ascii=False)}")
    if context.case_data:
        parts.append(f"- Case Data: {json.dumps(context.case_data, ensure_ascii=False)}")
    if not parts:
        return None
    return "\n".join(parts)


def _format_memory_content(content: Any) -> str:
    """Flatten MemoryContent to a short preview string.

    MemoryContent schema: summary (str), details (dict), feature_vector (list[float]).
    We only surface summary + key details; feature_vector is irrelevant for LLM context.
    """
    if hasattr(content, "model_dump"):
        content = content.model_dump()
    if isinstance(content, dict):
        summary = content.get("summary")
        details = content.get("details") or {}
        parts: list[str] = []
        if summary:
            parts.append(str(summary)[:200])
        if details:
            # Show up to 3 key-value pairs from details
            items = list(details.items())[:3]
            detail_str = ", ".join(f"{k}={v}" for k, v in items)
            parts.append(f"[{detail_str}]")
        if parts:
            return " ".join(parts)
        # Fallback: JSON dump
        return json.dumps(content, ensure_ascii=False, default=str)[:200]
    return str(content)[:200]
