"""Tool execution pipeline — hooks, validation, execution, guardrails, ratchet.

Extracted from react.py. Contains:
  - HookRunner (was ReActEngine._run_hooks)
  - RatchetRecorder (was ReActEngine._record_ratchet + _suggest_rule + _preview_arguments)
  - ToolCallValidator (was ReActEngine._validate_tool_call + _build_validation_rejection + _check_schema)
  - ToolExecutor (was ReActEngine._execute_with_timeout + _args_fingerprint
    + _run_after_tool_hooks + _check_output_guardrail)
"""

import asyncio
import hashlib
import inspect
import json
import logging
from typing import Any, TYPE_CHECKING

from cognitiveplane.control.hooks import BeforeToolHook, HookDecision, HookResult
from cognitiveplane.control.tools import ToolResult
from cognitiveplane.control.registry.tool_registry import ToolRegistry
from cognitiveplane.control.registry.tool_failure_reflector import (
    ReflectionResult,
    reflect as reflect_schema_failure,
)
from cognitiveplane.governance.guardrails import (
    AfterToolHook,
    GuardrailAction,
    OutputGuardrail,
)
from cognitiveplane.governance.policy_ratchet import (
    PolicyRatchet,
    TRIGGER_CAS_CONFLICT,
    TRIGGER_REASK_REJECT,
    TRIGGER_SCHEMA_FAIL,
    TRIGGER_TIMEOUT,
)
from cognitiveplane.shared.dto.context import ContextSnapshot

if TYPE_CHECKING:
    from cognitiveplane.control.skills import SkillRegistry

logger = logging.getLogger("react")


class HookRunner:
    """Run beforeTool hooks in order. First DENY wins.

    Was ReActEngine._run_hooks.
    """

    def __init__(self, hooks: list[BeforeToolHook]) -> None:
        self._hooks = hooks

    async def run_before(
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


class RatchetRecorder:
    """Plan §4.2.2 ratchet draft recorder.

    Was ReActEngine._record_ratchet + _suggest_rule + _preview_arguments.
    """

    def __init__(self, ratchet: PolicyRatchet) -> None:
        self._ratchet = ratchet

    def record(
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
                suggested_rule=self.suggest_rule(tool_name, trigger),
                arguments_preview=arguments_preview or {},
                session_id=session_id,
            )
        except Exception:
            logger.debug("ratchet record failed", exc_info=True)

    @staticmethod
    def suggest_rule(tool_name: str, trigger: str) -> str:
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
    def preview_arguments(arguments: dict) -> dict[str, Any]:
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


class ToolCallValidator:
    """P1-2 fix: 公共工具调用校验 — run_stream 与 _run_react 复用。

    Was ReActEngine._validate_tool_call + _build_validation_rejection + _check_schema.
    """

    def __init__(
        self,
        tools: ToolRegistry,
        hook_runner: HookRunner,
        ratchet: RatchetRecorder,
        failure_reflector=reflect_schema_failure,
        skill_registry: "SkillRegistry | None" = None,
    ) -> None:
        self._tools = tools
        self._hook_runner = hook_runner
        self._ratchet = ratchet
        self._failure_reflector = failure_reflector
        self._skill_registry = skill_registry

    async def validate(
        self,
        tool_call: dict,
        context: ContextSnapshot,
        session_id: str,
        tier_a_failures: dict[str, int],
        tools_used: list[str],
        allowed_tools: list[str] | None = None,
        session: dict | None = None,
        reasoning_content: str | None = None,
    ) -> dict:
        """P1-2 fix: 公共工具调用校验协程 — run_stream 与 _run_react 复用。

        消除原 300 行重复校验代码,避免 P1-1 类 messages 序列漂移 bug。
        校验顺序: llm_visible → allowed_tools(增强D) → JSON parse → schema → PolicyHook。
        任一失败立即返回 rejected/retry outcome(含已构造好的 messages + event data),
        调用方负责 emit/yield + extend messages。

        返回 dict(ValidationOutcome):
            kind: "accepted" | "rejected_retry" | "rejected_fatal"
            tool_name, tool_call_id, arguments, tool_call
            event_data: dict(供 emit/yield,含 rejected/reason/retry/schema_invalid)
            messages: list[dict](已构造好的 assistant+tool message 对,调用方 extend)
            needs_hook_deny_ratchet: bool(Tier-B hook DENY 需 record_ratchet)
        accepted 时 arguments 已解析,调用方加入 pending_executions。

        增强 D：allowed_tools 硬性拦截 + 意图漂移检测。
        当 skill 锁定后 LLM 调用 allowed_tools 外的工具时，硬性拦截（不只
        是"LLM 看不到"）+ 3 级递进漂移检测（提示→强提示→解锁 skill）。
        """
        # Lazy import to avoid circular dependency (react.py defines tier constants)
        from cognitiveplane.control.engine.react import (
            TIER_A_TOOLS, TIER_B_TOOLS, MAX_TIER_A_RETRIES,
        )

        tool_name = tool_call.get("function", {}).get("name", "")
        tool_call_id = tool_call.get("id", "")
        arguments_str = tool_call.get("function", {}).get("arguments", "{}")

        # 1. llm_visible 检查
        if not self._tools.is_llm_visible(tool_name):
            reason = f"Tool {tool_name} is not available in the current phase."
            return {
                "kind": "rejected_fatal",
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "arguments": {},
                "tool_call": tool_call,
                "event_data": {
                    "tool": tool_name, "arguments": {},
                    "rejected": True, "reason": reason,
                },
                "messages": [
                    {"role": "assistant", "content": None, "tool_calls": [tool_call], "reasoning_content": reasoning_content},
                    {"role": "tool", "tool_call_id": tool_call_id,
                     "content": json.dumps({"rejected": True, "reason": reason})},
                ],
            }

        # 增强 D：allowed_tools 硬性拦截 + 意图漂移检测
        # 当前 skill 锁定后，LLM 调用 allowed_tools 外的工具时：
        #   1. 先尝试"工具驱动 skill 自动切换" — 找到包含该工具的 skill 则切换并放行
        #      （让工具调用意图驱动路由，而非靠关键词匹配猜意图 — 真正智能的 skill 路由）
        #   2. 找不到包含该工具的 skill → 走 3 级漂移检测（提示→强提示→解锁）
        # 基础工具层豁免：always_available 工具（知识检索/只读/询问）不受 skill 白名单限制
        if (allowed_tools and tool_name not in allowed_tools
                and not self._tools.is_always_available(tool_name)):
            # 尝试工具驱动 skill 切换
            switched = False
            if self._skill_registry is not None and session is not None:
                for s in self._skill_registry._skills:
                    if tool_name in s.allowed_tools:
                        # 找到包含该工具的 skill,自动切换
                        old_name = session.get("locked_skill", "?")
                        session["locked_skill"] = s.name
                        session.pop("drift_attempts", None)
                        notes_list = session.setdefault("notes", [])
                        notes_list.append(f"🔄 工具驱动 skill 切换:{old_name}→{s.name}({tool_name})")
                        if len(notes_list) > 8:
                            session["notes"] = notes_list[-8:]
                        # 更新 allowed_tools 为新 skill 的,放行后续校验
                        allowed_tools = s.allowed_tools
                        switched = True
                        logger.info("[react] skill auto-switch %s→%s by tool=%s",
                                    old_name, s.name, tool_name)
                        break
            if not switched:
                # 没找到包含该工具的 skill,走 3 级漂移检测
                drift_note: str | None = None
                if session is not None:
                    drifts = session.setdefault("drift_attempts", [])
                    drifts.append({"tool": tool_name})
                    drift_count = len(drifts)
                    if drift_count >= 3:
                        # 解锁 skill,下轮重选
                        session.pop("locked_skill", None)
                        session.pop("drift_attempts", None)
                        drift_note = (f"🚨 意图漂移 {drift_count} 次：已解锁 skill，"
                                      f"下轮重新选择。最后尝试 {tool_name}（不在当前 skill 工具集内）")
                    elif drift_count >= 2:
                        drift_note = (f"⚠️ 意图漂移 {drift_count} 次：当前 skill 不含 {tool_name}，"
                                      f"可用工具 {list(allowed_tools)[:5]}。如需切换场景请明确说明")
                    if drift_note:
                        notes_list = session.setdefault("notes", [])
                        notes_list.append(drift_note)
                        if len(notes_list) > 8:
                            session["notes"] = notes_list[-8:]
                reason = (f"Tool {tool_name} 不在当前 skill 的可用工具集内。"
                          f"可用: {', '.join(allowed_tools[:5])}")
                return {
                    "kind": "rejected_fatal",
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "arguments": {},
                    "tool_call": tool_call,
                    "event_data": {
                        "tool": tool_name, "arguments": {},
                        "rejected": True, "reason": reason,
                    },
                    "messages": [
                        {"role": "assistant", "content": None, "tool_calls": [tool_call], "reasoning_content": reasoning_content},
                        {"role": "tool", "tool_call_id": tool_call_id,
                         "content": json.dumps({"rejected": True, "reason": reason})},
                    ],
                }

        # 2. JSON parse
        try:
            arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
            parse_error: str | None = None
        except json.JSONDecodeError as e:
            arguments = {}
            parse_error = f"Invalid JSON arguments: {e}. Raw: {arguments_str[:200]}"

        if parse_error is not None:
            return self.build_rejection(
                tool_call, tool_name, tool_call_id, arguments,
                parse_error, tier_a_failures, tools_used,
                is_schema=False,
                arguments_preview={"raw": arguments_str[:200]} if isinstance(arguments_str, str) else {},
                session_id=session_id,
                reasoning_content=reasoning_content,
            )

        # 成功解析 — reset Tier-A 失败计数
        if tool_name in TIER_A_TOOLS:
            tier_a_failures[tool_name] = 0

        # 3. schema 校验 + ToolFailureReflector 智能修复
        schema_error = self.check_schema(tool_name, arguments)
        if schema_error is not None:
            reflection = self._failure_reflector(
                tool_name, schema_error, arguments,
                user_input="",
            )
            if reflection.severity == "auto_fixed" and reflection.fixed_arguments:
                # 重校验修复后的参数
                recheck = self.check_schema(tool_name, reflection.fixed_arguments)
                if recheck is None:
                    arguments = reflection.fixed_arguments
                    # 修复成功 — 跳过 rejection，直接进入 PolicyHook
                else:
                    # 修复后仍不通过 — 用 actionable hint 替代原始错误
                    schema_error = reflection.hint or schema_error
                    return self.build_rejection(
                        tool_call, tool_name, tool_call_id, arguments,
                        schema_error, tier_a_failures, tools_used,
                        is_schema=True,
                        arguments_preview=self._ratchet.preview_arguments(arguments),
                        session_id=session_id,
                        reasoning_content=reasoning_content,
                    )
            elif reflection.severity == "actionable":
                # 用 reflector 的精确 hint 替代原始 jsonschema 错误
                schema_error = reflection.hint
                return self.build_rejection(
                    tool_call, tool_name, tool_call_id, arguments,
                    schema_error, tier_a_failures, tools_used,
                    is_schema=True,
                    arguments_preview=self._ratchet.preview_arguments(arguments),
                    session_id=session_id,
                    reasoning_content=reasoning_content,
                )
            else:
                # fatal — 走现有逻辑
                return self.build_rejection(
                    tool_call, tool_name, tool_call_id, arguments,
                    schema_error, tier_a_failures, tools_used,
                    is_schema=True,
                    arguments_preview=self._ratchet.preview_arguments(arguments),
                    session_id=session_id,
                    reasoning_content=reasoning_content,
                )

        # 4. PolicyHook
        hook_result = await self._hook_runner.run_before(tool_name, arguments, context, session_id=session_id)
        if hook_result.decision == HookDecision.DENY:
            reason = hook_result.reason
            needs_ratchet = tool_name in TIER_B_TOOLS
            if needs_ratchet:
                self._ratchet.record(
                    tool_name=tool_name,
                    trigger=TRIGGER_REASK_REJECT,
                    failure_pattern=f"PolicyHook DENY: {reason}",
                    arguments_preview=self._ratchet.preview_arguments(arguments),
                    session_id=session_id,
                )
            return {
                "kind": "rejected_fatal",
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "arguments": arguments,
                "tool_call": tool_call,
                "event_data": {
                    "tool": tool_name, "arguments": arguments,
                    "rejected": True, "reason": reason,
                },
                "messages": [
                    {"role": "assistant", "content": None, "tool_calls": [tool_call], "reasoning_content": reasoning_content},
                    {"role": "tool", "tool_call_id": tool_call_id,
                     "content": json.dumps({"rejected": True, "reason": reason})},
                ],
            }

        # 全部通过 — accepted
        return {
            "kind": "accepted",
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "arguments": arguments,
            "tool_call": tool_call,
            "event_data": {"tool": tool_name, "arguments": arguments},
            "messages": [],
        }

    def build_rejection(
        self,
        tool_call: dict,
        tool_name: str,
        tool_call_id: str,
        arguments: dict,
        error_msg: str,
        tier_a_failures: dict[str, int],
        tools_used: list[str],
        is_schema: bool,
        arguments_preview: dict,
        session_id: str,
        reasoning_content: str | None = None,
    ) -> dict:
        """P1-2 fix: 构造 parse/schema 失败的 rejection outcome。

        Was ReActEngine._build_validation_rejection.

        Tier-A 失败 < MAX_TIER_A_RETRIES → rejected_retry(LLM 下轮重试)
        Tier-B 或 Tier-A 耗尽 → rejected_fatal + record_ratchet + tools_used
        """
        # Lazy import to avoid circular dependency
        from cognitiveplane.control.engine.react import (
            TIER_A_TOOLS, MAX_TIER_A_RETRIES,
        )

        is_tier_a = tool_name in TIER_A_TOOLS
        failures_so_far = tier_a_failures.get(tool_name, 0)
        schema_flag = {"schema_invalid": True} if is_schema else {}

        if is_tier_a and failures_so_far < MAX_TIER_A_RETRIES:
            tier_a_failures[tool_name] = failures_so_far + 1
            return {
                "kind": "rejected_retry",
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "arguments": arguments,
                "tool_call": tool_call,
                "event_data": {
                    "tool": tool_name, "arguments": arguments,
                    "rejected": True, "reason": error_msg, "retry": True,
                    **schema_flag,
                },
                "messages": [
                    {"role": "assistant", "content": None, "tool_calls": [tool_call], "reasoning_content": reasoning_content},
                    {"role": "tool", "tool_call_id": tool_call_id,
                     "content": json.dumps({"error": error_msg, "hint": (
                         "Arguments failed JSON Schema validation. Please retry with valid arguments."
                         if is_schema else
                         "Please retry with valid JSON arguments matching the tool schema."
                     )})},
                ],
            }

        # Tier-B 或 Tier-A 耗尽 → fatal
        tier_a_failures[tool_name] = 0
        tools_used.append(tool_name)
        self._ratchet.record(
            tool_name=tool_name,
            trigger=TRIGGER_SCHEMA_FAIL if is_tier_a else TRIGGER_REASK_REJECT,
            failure_pattern=error_msg,
            arguments_preview=arguments_preview,
            session_id=session_id,
        )
        return {
            "kind": "rejected_fatal",
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "arguments": arguments,
            "tool_call": tool_call,
            "event_data": {
                "tool": tool_name, "arguments": arguments,
                "rejected": True, "reason": error_msg, "retry": False,
                **schema_flag,
            },
            "messages": [
                {"role": "assistant", "content": None, "tool_calls": [tool_call], "reasoning_content": reasoning_content},
                {"role": "tool", "tool_call_id": tool_call_id,
                 "content": json.dumps({"error": error_msg, "fatal": True, "hint": (
                     "Schema-invalid arguments rejected. Use a different approach or tool."
                     if is_schema else
                     "Tool call rejected. Use a different approach or tool."
                 )})},
            ],
        }

    def check_schema(self, tool_name: str, arguments: dict) -> str | None:
        """Plan §3.5 原则 5: pre-validate arguments against tool schema.

        Was ReActEngine._check_schema.

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


class ToolExecutor:
    """Execute tools with timeout guard + after-tool/output guardrails.

    Was ReActEngine._execute_with_timeout + _args_fingerprint
    + _run_after_tool_hooks + _check_output_guardrail.
    """

    def __init__(
        self,
        tools: ToolRegistry,
        ratchet: RatchetRecorder,
        after_tool_hooks: list[AfterToolHook] | None = None,
        output_guardrail: OutputGuardrail | None = None,
    ) -> None:
        self._tools = tools
        self._ratchet = ratchet
        self._after_tool_hooks = after_tool_hooks or []
        self._output_guardrail = output_guardrail

    async def execute_with_timeout(
        self, tool_name: str, arguments: dict, session: dict | None = None
    ) -> tuple[ToolResult, bool]:
        """Execute tool with §4.2.2 timeout guard.

        Was ReActEngine._execute_with_timeout.

        Returns (result, timed_out). On timeout, returns a synthetic ToolResult
        with error so the ReAct loop continues — the LLM sees the timeout as a
        tool failure observation and decides next step autonomously.

        增强 C：工具去重（Tier-A + analyze_image 缓存）。
        同参数同工具重复调用直接返回缓存结果，避免重复视觉 API 调用。
        CTO Briefing §8.4 明确提到"工具结果缓存"为缺失项。
        缓存策略：
        - 只缓存 Tier-A 工具（信息获取类，幂等）+ analyze_image
        - Tier-B 工具（design_workflow/launch_workflow/escalate）不缓存
        - 缓存 TTL = session 生命周期
        """
        # Lazy import to avoid circular dependency
        from cognitiveplane.control.engine.react import TIER_A_TOOLS
        from cognitiveplane.control.engine.session_notes import TOOL_TIMEOUT_SECONDS

        # 增强 C：工具去重（Tier-A + analyze_image 缓存）
        cacheable = tool_name in TIER_A_TOOLS or tool_name == "analyze_image"
        fp: str | None = None
        if cacheable and session is not None:
            fp = self.args_fingerprint(arguments)
            cache_key = f"{tool_name}:{fp}"
            cache = session.get("tool_cache", {})
            if cache_key in cache:
                return cache[cache_key], False

        tool = self._tools.get_tool(tool_name)
        if tool is None:
            return ToolResult(
                error=f"Tool '{tool_name}' not found",
                error_type="state",
            ), False

        runtime_kwargs: dict[str, Any] = {}
        if session is not None:
            session_id = session.get("session_id")
            if session_id and "session_id" not in arguments:
                runtime_kwargs["session_id"] = session_id
            runtime_event_callback = session.get("_runtime_event_callback")
            if runtime_event_callback is not None:
                runtime_kwargs["event_callback"] = runtime_event_callback
            parent_agent_id = session.get("_parent_agent_id")
            if parent_agent_id is not None:
                runtime_kwargs["parent_agent_id"] = parent_agent_id

        if runtime_kwargs:
            sig = inspect.signature(tool.execute)
            accepts_var_kwargs = any(
                param.kind == inspect.Parameter.VAR_KEYWORD
                for param in sig.parameters.values()
            )
            if not accepts_var_kwargs:
                # 工具不接受 **kwargs — 只传显式声明的参数
                runtime_kwargs = {
                    key: value
                    for key, value in runtime_kwargs.items()
                    if key in sig.parameters
                }
            else:
                # 工具接受 **kwargs — 仍需过滤运行时上下文参数，避免把它们
                # 传给不接受该参数的 MCP handler（如 echo server）。
                # 只注入工具 execute 方法显式声明的参数（非 **kwargs）。
                explicit_params = {
                    name
                    for name, param in sig.parameters.items()
                    if param.kind != inspect.Parameter.VAR_KEYWORD
                }
                runtime_kwargs = {
                    key: value
                    for key, value in runtime_kwargs.items()
                    if key in explicit_params
                }

        try:
            # request_confirmation 是阻塞等待用户输入的工具，不应被通用
            # TOOL_TIMEOUT_SECONDS(180s) 强杀。用户可能要想 5 分钟才点选项。
            # 工具内部用自己的 timeout_seconds 参数控制超时（默认 300s）。
            # 修复前 BUG：180s 超时强杀 → LLM 收到超时错误 → 反复问同一问题
            # → 用户点的选项被丢弃 → 体验灾难（同一问题被问 4 次）。
            if tool_name == "request_confirmation":
                result = await tool.execute(**arguments, **runtime_kwargs)
            else:
                result = await asyncio.wait_for(
                    tool.execute(**arguments, **runtime_kwargs),
                    timeout=TOOL_TIMEOUT_SECONDS,
                )
        except asyncio.TimeoutError:
            return ToolResult(
                error=f"Tool '{tool_name}' timed out after {TOOL_TIMEOUT_SECONDS}s",
                error_type="tool_timeout",
            ), True

        # 成功时缓存（Tier-A + analyze_image）
        if cacheable and session is not None and fp is not None and not result.error:
            cache = session.setdefault("tool_cache", {})
            cache[f"{tool_name}:{fp}"] = result

        return result, False

    @staticmethod
    def args_fingerprint(arguments: dict) -> str:
        """生成参数指纹（增强 B 卡住检测 + 增强 C 去重共用基础设施）。

        Was ReActEngine._args_fingerprint.

        对 image_b64 / image_ref 等大参数做短 md5 hash，避免 dict 序列化爆炸。
        用于判断"相同工具相同参数"——卡住检测（连续失败）和去重（命中缓存）。
        """
        stable_parts: list[str] = []
        for k in sorted(arguments.keys()):
            v = arguments[k]
            sv = str(v)
            # 大参数（>100 字符）做 md5 短 hash
            if len(sv) > 100:
                sv = hashlib.md5(sv.encode()).hexdigest()[:8]
            stable_parts.append(f"{k}={sv}")
        return hashlib.md5("|".join(stable_parts).encode()).hexdigest()[:8]

    async def run_after_tool_hooks(
        self, tool_name: str, arguments: dict, result: ToolResult, session_id: str = "default"
    ) -> ToolResult:
        """Run afterTool hooks (Guardrails) on tool results.

        Was ReActEngine._run_after_tool_hooks.

        First REJECT wins. On REJECT, the original tool result is replaced
        with the guardrail's replacement content (so LLM sees the rejection
        feedback, not the dangerous content). PASS/WARN keeps original result.

        Source: 2026 三层护栏架构 — after_tool guardrail 层
        """
        if not self._after_tool_hooks:
            return result

        for hook in self._after_tool_hooks:
            try:
                gr = await hook.after_execute(
                    tool_name=tool_name,
                    arguments=arguments,
                    result=result,
                    session_id=session_id,
                )
            except Exception as e:
                # 护栏异常不应阻断主流程 — 记录后继续
                logger.warning("[guardrail] after_tool hook %s raised: %s", type(hook).__name__, e)
                continue
            if gr.action == GuardrailAction.REJECT:
                # 替换为护栏提示 — LLM 会看到 reject 反馈而非危险内容
                replaced = ToolResult(
                    output={"guardrail_rejected": True, "reason": gr.reason},
                    error=gr.replacement or gr.reason,
                    error_type="guardrail_reject",
                )
                logger.info(
                    "[guardrail] AfterTool REJECT tool=%s rules=%s — result replaced",
                    tool_name, gr.triggered_rules,
                )
                return replaced
        return result

    async def check_output_guardrail(
        self, reply: str, tools_used: list[str], session_id: str = "default"
    ) -> str:
        """Run output guardrail on final LLM reply.

        Was ReActEngine._check_output_guardrail.

        Returns either the original reply (PASS) or the replacement (REJECT).
        Source: 2026 三层护栏架构 — output guardrail 层
        """
        if not self._output_guardrail:
            return reply
        try:
            gr = await self._output_guardrail.check_output(
                reply=reply, tools_used=tools_used, session_id=session_id,
            )
        except Exception as e:
            logger.warning("[guardrail] output guardrail raised: %s", e)
            return reply
        if gr.action == GuardrailAction.REJECT and gr.replacement:
            logger.info(
                "[guardrail] Output REJECT session=%s rules=%s — reply replaced",
                session_id, gr.triggered_rules,
            )
            return gr.replacement
        return reply
