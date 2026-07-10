"""RequestConfirmationTool — request human confirmation (Agent→Human).

Source: 7-plane redesign spec §7 lines 1414-1456.
Writes a confirmation request to the WeldMap negotiation domain via
CognitiveGatewayWritePort. The operator responds through the
notification interface (FastAPI WebSocket or polling).

修订（2026-07-06）：复用 ApprovalStore 阻塞机制，让工具调用会**等待
用户在弹窗里点选项**后再返回 ToolResult。这样 LLM 能拿到用户的选择
继续 ReAct，而不是盲猜或反复问。

设计：
  - execute() 内部创建 ApprovalRequest（带 approval_id）
  - await req.event.wait() 阻塞，直到前端 /chat/approve 端点 resolve
  - 用户在弹窗里点的选项值作为 decision 传回（不再限于 approved/rejected）
  - ToolResult.output 包含 user_selection 字段供 LLM 引用

notification store 已在重构中移除，前端通过 SSE approval_request 事件
接收确认请求，不再依赖 notifications/store.py。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from cognitiveplane.control.tools import BrainTool, ToolResult

if TYPE_CHECKING:
    from cognitiveplane.control.engine.approval import ApprovalStore
    from cognitiveplane.gateway.ports import CognitiveGatewayWritePort

logger = logging.getLogger(__name__)

# 默认超时 — 用户 5 分钟不响应则超时取消（与 APPROVAL_TIMEOUT_SECONDS 对齐）
_DEFAULT_TIMEOUT_SECONDS = 300.0


class RequestConfirmationTool(BrainTool):
    """Request confirmation from a human operator.

    Writes to WeldMap negotiation domain so the notification subsystem
    can push the question to the operator.

    修订版：内部用 ApprovalStore 阻塞等待用户响应，工具返回时
    ToolResult.output["user_selection"] 携带用户选择的具体选项值。
    """

    phase = 3

    def __init__(
        self,
        gateway_write: CognitiveGatewayWritePort | None = None,
        approval_store: "ApprovalStore | None" = None,
    ) -> None:
        self._gateway = gateway_write
        self._approval_store = approval_store

    @property
    def name(self) -> str:
        return "request_confirmation"

    @property
    def description(self) -> str:
        return (
            "Request confirmation from a human operator. "
            "Pops up a dialog with the question and option buttons. "
            "**Blocks until the user clicks an option** — your next iteration "
            "will see the user's selection in tool result 'user_selection' field. "
            "Use this when you lack business parameters (label set / annotator name / "
            "dataset choice) or need a decision direction from the user."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Question to ask the operator",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of option choices for the operator — **REQUIRED**. "
                        "Each option becomes a clickable button. "
                        "Provide 2-4 most likely options; the last can be "
                        "'我来指定' as a fallback."
                    ),
                },
                "urgency": {
                    "type": "string",
                    "enum": ["routine", "urgent", "critical"],
                    "description": "Urgency level of the confirmation request",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Timeout in seconds for operator response (default 300)",
                },
            },
            "required": ["question", "options"],
        }

    async def execute(
        self,
        question: str = "",
        options: list | None = None,
        urgency: str = "routine",
        timeout_seconds: int | None = None,
        event_callback: Any | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        """执行确认请求 — 弹窗等待用户选择。

        Args:
            question: 要问用户的问题
            options: 选项列表（2-4个），每个变成一个可点击按钮
            urgency: 紧急程度
            timeout_seconds: 超时秒数（默认300）
            event_callback: 由 ToolExecutor 注入的运行时事件回调，
                用于向前端推送 approval_request 事件让用户看到弹窗。
        """
        opts = options or []
        timeout_val = timeout_seconds if timeout_seconds is not None else _DEFAULT_TIMEOUT_SECONDS

        # 1) 创建 ApprovalRequest 让前端 /chat/approve 端点能 resolve
        approval_id: str | None = None
        req = None
        if self._approval_store is not None:
            approval_id = f"rc-{uuid4().hex[:12]}"
            summary = question
            if opts:
                summary = f"{question} 选项: {', '.join(opts[:4])}"
            req = self._approval_store.create(
                approval_id=approval_id,
                session_id="",  # request_confirmation 不绑定特定 session
                iteration=0,
                tool_name="request_confirmation",
                arguments={
                    "question": question,
                    "options": opts,
                    "urgency": urgency,
                },
                summary=summary,
            )
            # 通过 event_callback 向前端推送 approval_request 事件，
            # 让前端展示问题 + 选项按钮的确认卡片。
            # 没有 event_callback 时（非流式 / 测试环境），仅依赖 approval_store 阻塞。
            if event_callback is not None:
                try:
                    await event_callback("approval_request", {
                        "approval_id": approval_id,
                        "tool": "request_confirmation",
                        "summary": summary,
                        "iteration": 0,
                        "options": opts,
                        "question": question,
                    })
                except Exception:
                    logger.debug("request_confirmation event emit failed", exc_info=True)

        # 2) 同时写 WeldMap（保留原 gateway 行为，向后兼容）
        if self._gateway is not None:
            from cognitiveplane.interaction.signals.instruction import LiveInstruction
            from cognitiveplane.shared.enums import InstructionType, InstructionStatus, InterventionGranularity
            from cognitiveplane.shared.types import InstructionId

            instruction = LiveInstruction(
                instruction_id=InstructionId(value=str(uuid4())),
                instruction_type=InstructionType.MARK_FOR_REVIEW,
                status=InstructionStatus.ACTIVE,
                granularity=InterventionGranularity.CASE,
                payload={
                    "question": question,
                    "options": opts,
                    "urgency": urgency,
                    "timeout_seconds": timeout_val,
                },
                reason=question,
                issued_by="brain",
            )
            try:
                await self._gateway.publish_instruction(instruction)
            except Exception as e:
                logger.warning("gateway publish_instruction failed: %s", e)

        # 3) 阻塞等待用户响应（核心）
        if approval_id is None or self._approval_store is None or req is None:
            # 没装 ApprovalStore — 退化为非阻塞模式
            return ToolResult(output={
                "status": "confirmation_requested",
                "question": question,
                "options": opts,
                "urgency": urgency,
                "timeout_seconds": timeout_val,
                "message": "Confirmation request sent (non-blocking mode — approval_store not configured).",
                "warning": "approval_store not configured — tool returns immediately, LLM won't receive user's selection.",
            })

        # 阻塞等待 /chat/approve 端点 resolve
        try:
            await asyncio.wait_for(req.event.wait(), timeout=timeout_val)
        except asyncio.TimeoutError:
            self._approval_store.remove(approval_id)
            return ToolResult(output={
                "status": "timeout",
                "question": question,
                "options": opts,
                "message": f"用户在 {timeout_val}s 内未响应，请重新提问或换个方式。",
            })

        # 用户已响应，取出 decision（前端会把选项值作为 decision 传回）
        user_selection = req.decision or ""
        feedback = req.feedback or ""
        self._approval_store.remove(approval_id)

        return ToolResult(output={
            "status": "answered",
            "question": question,
            "options": opts,
            "user_selection": user_selection,
            "feedback": feedback,
            "message": f"用户选择：{user_selection}" + (f"（补充说明：{feedback}）" if feedback else ""),
        })
