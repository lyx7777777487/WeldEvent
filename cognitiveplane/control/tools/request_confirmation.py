"""RequestConfirmationTool — request human confirmation (Agent→Human).

Source: 7-plane redesign spec §7 lines 1414-1456.
Writes a confirmation request to the WeldMap negotiation domain via
CognitiveGatewayWritePort. The operator responds through the
notification interface (FastAPI WebSocket or polling).

修订（2026-07-06）：复用 ApprovalStore 阻塞机制，让工具调用会**等待
用户在弹窗里点选项**后再返回 ToolResult。这样 LLM 能拿到用户的选择
继续 ReAct，而不是盲猜或反复问。

设计：
  - execute() 内部创建 ApprovalRequest + Notification（带 approval_id）
  - await req.event.wait() 阻塞，直到前端 /chat/approve 端点 resolve
  - 用户在弹窗里点的选项值作为 decision 传回（不再限于 approved/rejected）
  - ToolResult.output 包含 user_selection 字段供 LLM 引用
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import uuid4

from cognitiveplane.control.tools import BrainTool, ToolResult

if TYPE_CHECKING:
    from cognitiveplane.control.react import ApprovalStore
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

    async def execute(self, **kwargs) -> ToolResult:
        question = kwargs["question"]
        options = kwargs.get("options", []) or []
        urgency = kwargs.get("urgency", "routine")
        timeout_seconds = kwargs.get("timeout_seconds", 300)

        # 1) Publish to notification store for real-time push (前端弹窗)
        from cognitiveplane.interaction.notifications.store import (
            get_notification_store,
            Notification,
            NotificationType,
        )
        from datetime import timedelta, timezone, datetime

        store = get_notification_store()

        # 2) 创建 ApprovalRequest 让前端 /chat/approve 端点能 resolve
        approval_id: str | None = None
        if self._approval_store is not None:
            import time
            approval_id = f"rc-{uuid4().hex[:12]}"
            # summary 给前端展示用（人类可读）
            summary = question
            if options:
                summary = f"{question} 选项: {', '.join(options[:4])}"
            req = self._approval_store.create(
                approval_id=approval_id,
                session_id="",  # request_confirmation 不绑定特定 session
                iteration=0,
                tool_name="request_confirmation",
                arguments={
                    "question": question,
                    "options": options,
                    "urgency": urgency,
                },
                summary=summary,
            )

        # 3) 创建 Notification（携带 approval_id 让前端走 approveFromNotification 回调）
        notification = Notification(
            notification_type=NotificationType.CONFIRMATION_REQUEST,
            title="需要您的选择",
            message=question,
            payload={
                "question": question,
                "options": options,
                "urgency": urgency,
                "timeout_seconds": timeout_seconds,
                "approval_id": approval_id,  # 让前端走 /chat/approve
            },
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds),
        )
        await store.add(notification)

        # 4) 同时写 WeldMap（保留原 gateway 行为，向后兼容）
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
                    "options": options,
                    "urgency": urgency,
                    "timeout_seconds": timeout_seconds,
                },
                reason=question,
                issued_by="brain",
            )
            try:
                await self._gateway.publish_instruction(instruction)
            except Exception as e:
                logger.warning("gateway publish_instruction failed: %s", e)

        # 5) 阻塞等待用户响应（核心改动）
        if approval_id is None or self._approval_store is None:
            # 没装 ApprovalStore — 退化为非阻塞模式
            return ToolResult(output={
                "status": "confirmation_requested",
                "question": question,
                "options": options,
                "urgency": urgency,
                "timeout_seconds": timeout_seconds,
                "message": "Confirmation request sent (non-blocking mode — approval_store not configured).",
                "notification_id": str(notification.notification_id),
                "warning": "approval_store not configured — tool returns immediately, LLM won't receive user's selection.",
            })

        # 阻塞等待 /chat/approve 端点 resolve
        try:
            import asyncio
            await asyncio.wait_for(req.event.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            self._approval_store.remove(approval_id)
            return ToolResult(output={
                "status": "timeout",
                "question": question,
                "options": options,
                "message": f"用户在 {timeout_seconds}s 内未响应，请重新提问或换个方式。",
                "notification_id": str(notification.notification_id),
            })

        # 用户已响应，取出 decision（前端会把选项值作为 decision 传回）
        user_selection = req.decision or ""
        feedback = req.feedback or ""
        self._approval_store.remove(approval_id)

        # 把 notification 标记为 resolved
        try:
            await store.update_status(
                notification.notification_id,
                __import__("cognitiveplane.interaction.notifications.store",
                           fromlist=["NotificationStatus"]).NotificationStatus.RESOLVED,
            )
        except Exception:
            logger.debug("update notification status failed", exc_info=True)

        return ToolResult(output={
            "status": "answered",
            "question": question,
            "options": options,
            "user_selection": user_selection,
            "feedback": feedback,
            "message": f"用户选择：{user_selection}" + (f"（补充说明：{feedback}）" if feedback else ""),
            "notification_id": str(notification.notification_id),
        })
