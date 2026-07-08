"""Streaming endpoint handlers — POST /stream (SSE) and WS /ws.

Extracted from chat.py create_chat_router. Each handler is a module-level
async function receiving its dependencies as explicit parameters.

Source: 7-plane redesign spec §7 FastAPI + plan §2.3.
"""

import json
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from cognitiveplane.interaction.api.chat import ChatRequest
from cognitiveplane.interaction.api._helpers import (
    logger,
    _inject_session_image_refs,
    _get_or_create_session,
    _trigger_evaluation,
    _EvalProxy,
    persist_session,
)
from cognitiveplane.shared.dto.context import ContextSnapshot
from cognitiveplane.shared.enums import EventType, NoveltyLevel
from cognitiveplane.shared.types import CaseId
from datetime import datetime, timezone


async def handle_chat_stream(
    request: ChatRequest,
    *,
    session_manager,
    engine,
    router,
    evaluator,
    deps,
    hooks,
    image_store,
    event_log,
    after_tool_hooks,
    output_guardrail,
    skill_registry,
    approval_store,
    context_compactor,
):
    """流式聊天端点 — SSE (Server-Sent Events)。POST /api/v1/chat/stream.

    返回 text/event-stream，逐事件推送：
      - thinking:    ReAct 新迭代
      - tool_call:   工具调用（含 rejected/retry 信息）
      - tool_result: 工具返回
      - token:       最终轮 LLM 流式 token（打字机效果）
      - final:       流式结束（含完整 reply + tools_used + workflow_ids）
      - error:       异常

    前端用 fetch + ReadableStream 消费（EventSource 不支持 POST）。
    与 /chat/ 的差异：最终回复逐 token 流式输出，工具调用实时推送，
    用户能看到 LLM 思考过程，减少等待焦虑。
    """
    from cognitiveplane.control.react import ReActEngine

    session = _get_or_create_session(session_manager, request.operator_id, request.session_id, request.case_id)
    logger.info("[STREAM] session=%s user=%r case=%s",
                session.session_id.value, request.message, request.case_id)

    context = ContextSnapshot(
        case_id=CaseId(value=request.case_id or "unknown"),
        event_type=EventType.WORKFLOW_ENTERED,
        workflow_state={},
        case_data={},
        measurements=[],
        memory_match_confidence=0.5,
        knowledge_coverage=0.5,
        event_novelty=NoveltyLevel.PARTIAL,
        validation_critical_count=0,
        timestamp=datetime.now(timezone.utc),
    )

    session_id_str = str(session.session_id.value)
    user_msg = _inject_session_image_refs(request.message, session_id_str, router)

    async def event_generator():
        """SSE 事件生成器 — 每请求独立 ReActEngine，避免并发缓存污染。

        流结束后在 finally 块追加对话到 session.messages，供下一轮作为历史。
        客户端断开时 generator 被取消，finally 仍执行（只追加已收到的内容）。
        """
        # 每请求创建独立 ReActEngine — 避免并发请求共享 AnalyzeImageTool._result_cache
        # 但复用 engine._tools（ToolRegistry）—— 否则 Label Studio 标注工具
        # (list_datasets/create_job 等) 不会注册到 stream_engine 的 ToolRegistry,
        # 导致 LLM 调用时报 "Tool list_datasets is not available in the current phase".
        # app.py lifespan 只把标注工具注册到 engine._tools, 不注册到每请求新建的 TR.
        # approval_store 共享进程级实例 — 让 /chat/approve 端点能 resolve
        # context_compactor 共享 — 长对话历史压缩
        stream_engine = ReActEngine(
            deps, hooks=hooks, image_store=image_store, event_log=event_log,
            after_tool_hooks=after_tool_hooks,
            output_guardrail=output_guardrail,
            skill_registry=skill_registry,
            approval_store=approval_store,
            context_compactor=context_compactor,
            tool_registry=engine._tools,
        )
        final_reply = ""
        final_reasoning: str | None = None
        tools_used_list: list[str] = []
        workflow_ids_list: list[str] = []
        try:
            async for event in stream_engine.run_stream(
                user_input=user_msg,
                context=context,
                session={
                    "session_id": session_id_str,
                    "history": session.messages,
                },
            ):
                if event["event"] == "final":
                    final_reply = event["data"].get("reply", "")
                    tools_used_list = event["data"].get("tools_used", [])
                    workflow_ids_list = event["data"].get("workflow_ids", [])
                    final_reasoning = event["data"].get("reasoning_content")
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            err_event = {"event": "error", "data": {"message": f"stream error: {e}"}}
            yield f"data: {json.dumps(err_event, ensure_ascii=False)}\n\n"
        finally:
            # 流结束后追加对话到 session.messages，供下一轮作为历史
            # 共享 persist_session 函数, 与 WS /ws 路径行为一致
            persist_session(session_manager, session_id_str, user_msg, final_reply, final_reasoning)
            logger.info("[STREAM] session=%s tools=%s wf_ids=%s reply_len=%d",
                        session_id_str, tools_used_list, workflow_ids_list, len(final_reply))
            # Phase 5: 流式响应结束后异步评估
            _trigger_evaluation(
                evaluator=evaluator,
                user_input=user_msg,
                response=_EvalProxy(
                    text_reply=final_reply,
                    tools_used=tools_used_list,
                ),
                session_id=session_id_str,
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲，确保 token 实时推送
        },
    )


async def handle_websocket_chat(
    websocket: WebSocket,
    *,
    deps,
    hooks,
    skill_registry,
    image_store,
    after_tool_hooks,
    output_guardrail,
    engine,
    session_manager,
    image_sessions,
    router,
) -> None:
    """WebSocket 聊天端点 — WS /api/v1/chat/ws.

    Phase 3 子项目 D: AgentLoop 双 task。
    """
    from cognitiveplane.control.agent_loop import AgentLoop
    from cognitiveplane.control.event_log import EventLog

    await websocket.accept()

    event_log = EventLog(case_id=CaseId(value="app-shared"))

    async def send_json(data: dict) -> None:
        await websocket.send_json(data)

    # 治理一致: /ws 路径必须与 HTTP /api/v1/chat 用同一套 hooks (SafetyHook + PolicyHook).
    # 不能让同一工具调用因入口不同而绕过 ToolPolicy.
    # stream_mode=True: WS 客户端获得逐 token 流式输出（打字机效果）.
    loop = AgentLoop(
        deps=deps,
        event_log=event_log,
        send_json=send_json,
        hooks=hooks,
        skill_registry=skill_registry,
        image_store=image_store,
        after_tool_hooks=after_tool_hooks,
        output_guardrail=output_guardrail,
        stream_mode=True,
        tool_registry=engine._tools,
        session_manager=session_manager,
        image_sessions=image_sessions,
        router=router,
    )
    await loop.start()

    try:
        while True:
            data = await websocket.receive_json()
            await loop.put_message(data)
    except WebSocketDisconnect:
        logger.debug("websocket disconnected")
    finally:
        await loop.stop()
