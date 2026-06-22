"""ReActEngine — Reasoning+Acting loop with 3-tier fallback.

Source: 7-plane redesign spec §7 Interaction — ReAct.

Tier 1: Full ReAct with Function Calling (LLM available)
Tier 2: Structured output intent analysis + rule-based routing
Tier 3: Semantic embedding + rule engine (no LLM)
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from cognitiveplane.control.deps import CognitiveDependencies
from cognitiveplane.control.hooks import BeforeToolHook, HookDecision, HookResult
from cognitiveplane.control.tool_registry import ToolRegistry
from cognitiveplane.control.tools import ToolResult
from cognitiveplane.shared.dto_context import ContextSnapshot


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
    ) -> None:
        self._deps = deps
        self._tools = tool_registry or ToolRegistry(deps)
        self._hooks = hooks or []
        self._max_iterations = max_iterations
        self._tools_used: list[str] = []

    def _select_tier(self) -> InteractionTier:
        """Choose interaction tier based on LLM availability."""
        if self._deps.capability.llm_provider is not None:
            return InteractionTier.REACT_FUNCTION_CALLING
        return InteractionTier.EMBEDDING_RULES

    async def run(
        self,
        user_input: str,
        context: ContextSnapshot,
        session: dict[str, Any] | None = None,
    ) -> InteractionResponse:
        """Run the ReAct loop with automatic tier selection."""
        self._tools_used = []
        tier = self._select_tier()

        if tier == InteractionTier.REACT_FUNCTION_CALLING:
            return await self._run_react(user_input, context, session or {}, tier)
        elif tier == InteractionTier.STRUCTURED_OUTPUT:
            return await self._run_structured(user_input, context, session or {})
        else:
            return await self._run_embedding_rules(user_input, context, session or {})

    async def _run_react(
        self,
        user_input: str,
        context: ContextSnapshot,
        session: dict[str, Any],
        tier: InteractionTier,
    ) -> InteractionResponse:
        """Tier 1: Full ReAct with Function Calling."""
        llm = self._deps.capability.llm_provider
        if llm is None:
            return await self._run_embedding_rules(user_input, context, session)

        messages = self._build_system_prompt(context, session)
        messages.append({"role": "user", "content": user_input})

        tool_rounds = 0
        for i in range(self._max_iterations):
            try:
                response = await llm.complete(
                    self._make_llm_request(messages)
                )
            except Exception as e:
                return InteractionResponse(
                    text_reply=f"LLM error: {e}",
                    tools_used=self._tools_used,
                    tier_used=tier,
                    error=str(e),
                )

            tool_calls = response.tool_calls
            if not tool_calls:
                return InteractionResponse(
                    text_reply=response.content,
                    tools_used=self._tools_used,
                    tier_used=tier,
                )

            # 强制终止：超过3轮工具调用后，不再传工具定义，迫使LLM直接回答
            tool_rounds += 1
            force_final = tool_rounds >= 3

            for tool_call in tool_calls:
                tool_name = tool_call.get("function", {}).get("name", "")
                arguments_str = tool_call.get("function", {}).get("arguments", "{}")
                try:
                    arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
                except json.JSONDecodeError:
                    arguments = {}

                # Run hooks — first DENY wins
                hook_result = await self._run_hooks(tool_name, arguments, context)
                if hook_result.decision == HookDecision.DENY:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", ""),
                        "content": json.dumps({
                            "rejected": True,
                            "reason": hook_result.reason,
                        }),
                    })
                    continue

                # Execute tool
                result = await self._tools.execute(tool_name, arguments)
                self._tools_used.append(tool_name)

                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", ""),
                    "content": json.dumps(result.to_json()),
                })

            # 超过3轮工具调用，强制LLM给出最终答案
            if force_final:
                messages.append({
                    "role": "user",
                    "content": (
                        "你已经收集了足够的信息，请现在直接给出最终答案，不要再调用任何工具。"
                        "根据已有工具结果综合回答用户的问题。"
                    ),
                })
                try:
                    final_response = await llm.complete(
                        self._make_llm_request_no_tools(messages)
                    )
                    return InteractionResponse(
                        text_reply=final_response.content,
                        tools_used=self._tools_used,
                        tier_used=tier,
                    )
                except Exception:
                    return InteractionResponse(
                        text_reply="已收集信息但生成最终答案时出错，请重新提问。",
                        tools_used=self._tools_used,
                        tier_used=tier,
                    )

        return InteractionResponse(
            text_reply="Max iterations reached. Please refine your request.",
            tools_used=self._tools_used,
            tier_used=tier,
        )

    async def _run_hooks(
        self, tool_name: str, arguments: dict, context: ContextSnapshot
    ) -> HookResult:
        """Run all beforeTool hooks in order. First DENY wins."""
        for hook in self._hooks:
            result = await hook.before_execute(tool_name, arguments, context)
            if result.decision == HookDecision.DENY:
                return result
        return HookResult(decision=HookDecision.ALLOW)

    async def _run_structured(
        self,
        user_input: str,
        context: ContextSnapshot,
        session: dict[str, Any],
    ) -> InteractionResponse:
        """Tier 2: Structured output intent analysis + rule-based routing."""
        llm = self._deps.capability.llm_provider
        if llm is None:
            return await self._run_embedding_rules(user_input, context, session)

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
        tool_map = {
            "knowledge_query": "search_standards",
            "workflow_design": "design_workflow",
            "intervention": "adjust_parameter",
            "monitoring": "read_weldmap",
        }
        tool_name = tool_map.get(intent)
        if tool_name and self._tools.get_tool(tool_name):
            result = await self._tools.execute(tool_name, {"query": user_input})
            self._tools_used.append(tool_name)
            return InteractionResponse(
                text_reply=json.dumps(result.to_json(), ensure_ascii=False),
                tools_used=self._tools_used,
                tier_used=InteractionTier.STRUCTURED_OUTPUT,
            )

        return InteractionResponse(
            text_reply=llm_content or user_input,
            tools_used=self._tools_used,
            tier_used=InteractionTier.STRUCTURED_OUTPUT,
        )

    async def _run_embedding_rules(
        self,
        user_input: str,
        context: ContextSnapshot,
        session: dict[str, Any],
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
            "adjust_parameter": ["调整", "修改", "干预", "adjust", "intervene"],
            "escalate": ["升级", "人工", "escalate", "human"],
            "archive_memory": ["保存", "记忆", "存档", "save", "memory"],
            "explain_decision": ["解释", "为什么", "explain", "why"],
            "web_search": ["搜索", "查询", "最新", "网上", "search", "web", "latest"],
        }

        best_match = None
        best_score = 0
        for tool_name, keywords in keyword_map.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > best_score and self._tools.get_tool(tool_name):
                best_score = score
                best_match = tool_name

        if best_match:
            result = await self._tools.execute(best_match, {"query": user_input})
            self._tools_used.append(best_match)
            return InteractionResponse(
                text_reply=json.dumps(result.to_json(), ensure_ascii=False),
                tools_used=self._tools_used,
                tier_used=InteractionTier.EMBEDDING_RULES,
            )

        return InteractionResponse(
            text_reply="无法识别操作意图。请尝试更具体的描述，例如：查询标准参数、设计检测方案、查看产线进度。",
            tools_used=self._tools_used,
            tier_used=InteractionTier.EMBEDDING_RULES,
        )

    def _build_system_prompt(
        self, context: ContextSnapshot, session: dict[str, Any]
    ) -> list[dict[str, str]]:
        """Build system prompt with context and available tools."""
        tools_desc = "\n".join(
            f"- {name}: {self._tools.get_tool(name).description}"
            for name in self._tools.list_tools()
            if self._tools.get_tool(name) is not None
        )
        return [
            {
                "role": "system",
                "content": (
                    "你是 WeldEvent 工业质检Agent系统的决策引擎。你可以使用以下工具来完成任务：\n\n"
                    f"{tools_desc}\n\n"
                    "规则：\n"
                    "1. 每次只调用1-2个最相关的工具\n"
                    "2. 根据工具结果综合分析后，立即给出最终答案\n"
                    "3. 不要重复调用相同工具\n"
                    "4. 如果工具返回了足够信息，直接回答用户问题，不要再调用其他工具\n"
                    "5. 安全相关的参数修改需要理由\n"
                    "6. 最多调用3轮工具，然后必须给出最终答案\n"
                ),
            }
        ]

    def _make_llm_request(self, messages: list[dict]) -> Any:
        """Build LLMRequest from messages."""
        from cognitiveplane.capability.provider import LLMRequest
        tool_defs = self._tools.get_llm_tool_definitions()
        return LLMRequest(
            messages=messages,
            tools=tool_defs if tool_defs else None,
            caller="react_engine",
        )

    def _make_llm_request_no_tools(self, messages: list[dict]) -> Any:
        """Build LLMRequest without tools — forces text-only response."""
        from cognitiveplane.capability.provider import LLMRequest
        return LLMRequest(
            messages=messages,
            tools=None,
            caller="react_engine_final",
        )
