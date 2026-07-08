"""Workflow endpoint handlers — POST /workflow/events, POST /workflow/status,
GET /workflow/stream/{session_id}.

Extracted from chat.py create_chat_router.

Source: 7-plane redesign spec §7 FastAPI.
"""

import asyncio
import json
import time
from typing import Any
from pydantic import BaseModel
from fastapi.responses import StreamingResponse

from cognitiveplane.interaction.api._helpers import logger
from cognitiveplane.interaction.workflow_events import WorkflowEvent


class WorkflowEventRequest(BaseModel):
    """L2 worker 推送的工作流节点事件载荷。"""
    session_id: str
    workflow_id: str
    event_type: str  # node_start | node_end | node_error | workflow_started | workflow_completed | workflow_failed | workflow_paused | workflow_resumed
    node_id: str | None = None
    node_status: str | None = None
    node_data: dict[str, Any] | None = None
    error: str | None = None


class QueryWorkflowStatusRequest(BaseModel):
    """LLM 查询 workflow 状态的请求(供 LLM 工具调用)。"""
    workflow_id: str


async def handle_receive_workflow_event(
    request: WorkflowEventRequest,
    *,
    workflow_event_bus,
):
    """L2→L1 节点事件接收端点。POST /api/v1/chat/workflow/events.

    L2 dag_runner_workflow 在节点 start/end/error 时 POST 此端点,
    L1 按 session_id 路由到 WorkflowEventBus,广播给前端 SSE 订阅者。
    这是 L2→L1 事件回传通道的入口。
    """
    event = WorkflowEvent(
        session_id=request.session_id,
        workflow_id=request.workflow_id,
        event_type=request.event_type,
        node_id=request.node_id,
        node_status=request.node_status,
        node_data=request.node_data,
        error=request.error,
    )
    await workflow_event_bus.publish(event)
    logger.info(
        "[WF_EVENT] session=%s wf=%s type=%s node=%s status=%s",
        request.session_id, request.workflow_id, request.event_type,
        request.node_id, request.node_status,
    )
    return {"ok": True}


async def handle_query_workflow_status(
    request: QueryWorkflowStatusRequest,
    *,
    workflow_event_bus,
):
    """查询 workflow 当前状态(供 LLM / 前端 polling fallback)。
    POST /api/v1/chat/workflow/status.
    """
    status = workflow_event_bus.get_workflow_status(request.workflow_id)
    if status is None:
        return {"ok": False, "error": "workflow not found in event bus"}
    return {"ok": True, "status": status}


async def handle_workflow_stream(
    session_id: str,
    *,
    workflow_event_bus,
):
    """SSE 端点 — 前端订阅工作流节点事件。
    GET /api/v1/chat/workflow/stream/{session_id}.

    前端用 EventSource 长连接订阅此端点:
        const es = new EventSource('/api/v1/chat/workflow/stream/sess_xxx');

    连接时立即收到历史事件(最近 50 条),之后实时收到节点 start/end/error。
    前端收到事件后渲染节点进度卡片,让用户看到工作流执行过程。

    与 /chat/stream 的差异:
      - /chat/stream: 请求-响应模式,LLM 推理完毕后关闭
      - /workflow/stream/{session_id}: 长连接,持续推送工作流节点事件
    """
    async def event_generator():
        q, history = workflow_event_bus.subscribe(session_id)
        try:
            # 1. 先推送历史事件(让新连接的前端看到已发生的进度)
            for evt in history:
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            # 2. 持续推送实时事件
            while True:
                try:
                    # 30s 超时发心跳,避免代理/浏览器断开空闲连接
                    evt = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # SSE 心跳
                    yield f": ping {int(time.time())}\n\n"
        except asyncio.CancelledError:
            logger.debug("sse stream cancelled")
        finally:
            workflow_event_bus.unsubscribe(session_id, q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx 不缓冲
        },
    )
