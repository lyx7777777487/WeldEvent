"""Approval & cancel endpoint handlers — POST /approve and POST /cancel.

Extracted from chat.py create_chat_router.

Source: 7-plane redesign spec §7 FastAPI.
"""

from pydantic import BaseModel

from cognitiveplane.interaction.api._helpers import logger


class CancelRequest(BaseModel):
    workflow_id: str


class ApproveRequest(BaseModel):
    """架构级 approval gate 的用户决策请求。

    前端收到 SSE approval_request 事件后展示确认 UI，用户点击确认/拒绝时
    调此端点 resolve 对应的 ApprovalRequest，唤醒阻塞中的 ReAct loop。

    decision 字段兼容两种语义：
    - "approved" / "rejected" — APPROVAL_REQUIRED_TOOLS 拦截场景的固定决策
    - 任意选项值（如 "气孔" / "张工"）— request_confirmation 工具的多选项
      弹窗场景，用户点哪个选项就把选项值作为 decision 传回
    """
    approval_id: str
    decision: str
    feedback: str | None = None


async def handle_cancel_workflow(
    request: CancelRequest,
    *,
    deps,
):
    """取消 Temporal workflow 执行。POST /api/v1/chat/cancel.

    前端 stopExecution() 调用此端点终止正在运行的 workflow。
    终止信号发送到 Temporal，workflow 收到后执行清理并退出。
    """
    connector = deps.bridge.event_connector if deps.bridge else None
    if connector is None:
        return {"ok": False, "error": "bridge not available"}
    success = await connector.cancel_workflow(request.workflow_id)
    return {"ok": success}


async def handle_approve_tool(
    request: ApproveRequest,
    *,
    approval_store,
):
    """架构级 human-in-the-loop 确认端点。POST /api/v1/chat/approve.

    ReAct loop 在执行 APPROVAL_REQUIRED_TOOLS（如 launch_workflow）前会
    emit approval_request 事件并阻塞等待。前端收到后展示方案摘要 + 确认按钮，
    用户决定后 POST 此端点 resolve。

    - approved: ReAct loop 恢复并执行该工具
    - rejected: ReAct loop 跳过该工具，回填拒绝结果给 LLM 让其调整方案
    - 任意选项值（如 "气孔"）: request_confirmation 弹窗的多选项场景，
      用户点的选项值作为 decision 传回，唤醒阻塞中的工具调用，LLM 在
      ToolResult.output["user_selection"] 拿到该值继续 ReAct
    """
    # 不再限制 decision 必须是 approved/rejected — request_confirmation
    # 工具的多选项弹窗会把具体选项值（如"气孔"）作为 decision 传回。
    # ApprovalStore.resolve() 接受任意字符串。
    ok = approval_store.resolve(request.approval_id, request.decision, request.feedback)
    if not ok:
        return {"ok": False, "error": "approval_id not found or already resolved"}
    logger.info("[APPROVE] %s → %s feedback=%r",
                request.approval_id, request.decision, request.feedback)
    return {"ok": True, "decision": request.decision}
