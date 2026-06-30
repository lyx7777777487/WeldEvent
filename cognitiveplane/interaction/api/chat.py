"""FastAPI chat router — POST /api/v1/chat and WebSocket /ws.

支持两种上传模式:
  - JSON: 纯文本聊天（无文件）
  - multipart/form-data: 带文件上传（工业场景大文件）

Plan §2.3 双轨:
  - 消息层: thumbnail data URL 直接进 LLM content (LLM 直接看图)
  - 工具层: LLM 调 analyze_image(image_ref=...) 拿原图 (只传引用)

Plan §A.3:
  - 上传图统一生成 Thumbnail 注入消息层 (成本闸门)
  - 原图保留在 ImageStore (内存，阶段 3+ 切 MinIO)

Source: 7-plane redesign spec §7 FastAPI + plan §2.3 + §A.3.
"""

from fastapi import APIRouter, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Any, Optional
import json
import logging

from cognitiveplane.control.deps import CognitiveDependencies

logger = logging.getLogger("chat_api")
# 确保 INFO 级别日志能输出到 stdout（uvicorn 默认只配自己的 logger）
if not logger.handlers:
    import sys
    h = logging.StreamHandler(sys.stdout)
    h.setLevel(logging.INFO)
    h.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # 避免重复输出


class ChatRequest(BaseModel):
    """Incoming chat message (JSON mode — no files)."""
    message: str
    operator_id: str = "operator-001"
    case_id: str | None = None
    session_id: str | None = None


class ChatResponse(BaseModel):
    """Outgoing chat response."""
    reply: str
    session_id: str
    tools_used: list[str] = []
    tier: str = "react_function_calling"
    error: str | None = None
    # P3-5 fix: 结构化回传 launch_workflow 产出的 workflow_id，
    # 避免消费者从 LLM 回复文本正则提取（脆弱）。
    workflow_ids: list[str] = []


def create_chat_router(deps: CognitiveDependencies) -> APIRouter:
    """Create FastAPI router for chat endpoints."""
    from cognitiveplane.control.react import ReActEngine
    from cognitiveplane.control.hooks import SafetyHook, PolicyHook
    from cognitiveplane.control.event_log import EventLog
    from cognitiveplane.governance.tool_policy import ToolPolicy
    from cognitiveplane.interaction.session import SessionManager
    from cognitiveplane.interaction.file_handler import FileHandler
    from cognitiveplane.interaction.image_store import ImageStore
    from cognitiveplane.interaction.api.image_session import ImageSessionRegistry
    from cognitiveplane.shared.dto.context import ContextSnapshot
    from cognitiveplane.shared.enums import EventType, NoveltyLevel
    from cognitiveplane.shared.types import CaseId
    from datetime import datetime, timezone

    router = APIRouter(prefix="/api/v1/chat", tags=["chat"])
    session_manager = SessionManager()
    tool_policy = ToolPolicy()
    hooks = [SafetyHook(), PolicyHook(tool_policy)]
    # plan §A.3 + §7 contract: ImageStore is session-scoped state, NOT a Provider.
    # Constructed here and passed explicitly to ReActEngine → ToolRegistry → AnalyzeImageTool.
    image_store = ImageStore()
    image_sessions = ImageSessionRegistry(image_store)
    # plan §0.1 规则 1: 架构替 LLM 做的看不见的事 (worldview 注入) 必须记进 EventLog.
    # Phase 2 用 app 级共享 EventLog (case_id="app-shared"); Phase 3 AgentLoop 时改 per-session.
    event_log = EventLog(case_id=CaseId(value="app-shared"))
    engine = ReActEngine(
        deps, hooks=hooks, image_store=image_store, event_log=event_log,
    )
    file_handler = FileHandler(llm_provider=deps.capability.llm_provider, image_store=image_store)

    # 暴露 tool_registry 给 app.py，用于挂载 MCP server（协议化工具层）
    # router.engine 也暴露，供 app.py 做健康检查等
    router.tool_registry = engine._tools
    router.engine = engine
    router.image_store = image_store

    # ── JSON 纯文本聊天 ──

    @router.post("/", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        session = _get_or_create_session(session_manager, request.operator_id, request.session_id, request.case_id)
        logger.info("[CHAT] session=%s user=%r case=%s",
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

        response = await engine.run(
            user_input=request.message,
            context=context,
            session={
                "session_id": str(session.session_id.value),
                "history": session.messages,  # 传入历史对话，避免 LLM 失忆
            },
        )

        # 追加本轮对话到 session.messages，供下一轮作为历史
        session.messages.append({"role": "user", "content": request.message})
        if response.text_reply:
            session.messages.append({"role": "assistant", "content": response.text_reply})

        logger.info("[CHAT] session=%s tools=%s wf_ids=%s reply_len=%d error=%s",
                    session.session_id.value,
                    response.tools_used,
                    getattr(response, "workflow_ids", []),
                    len(response.text_reply or ""),
                    response.error)
        logger.info("[CHAT] reply=\n%s", response.text_reply)

        return ChatResponse(
            reply=response.text_reply,
            session_id=str(session.session_id.value),
            tools_used=response.tools_used,
            tier=response.tier_used.value,
            error=response.error,
            workflow_ids=getattr(response, "workflow_ids", []),
        )

    # ── multipart/form-data 文件上传聊天 ──

    @router.post("/upload", response_model=ChatResponse)
    async def chat_with_files(
        message: str = Form(...),
        operator_id: str = Form("operator-001"),
        case_id: Optional[str] = Form(None),
        session_id: Optional[str] = Form(None),
        files: list[UploadFile] = File(default=[]),
    ) -> ChatResponse:
        """带文件上传的聊天接口 — 使用 multipart/form-data，支持大文件。

        Plan §2.3 双轨:
          - Thumbnail 进消息层 (LLM 直接看)
          - image_id 给工具层 (LLM 调 analyze_image 拿原图)
        """
        session = _get_or_create_session(session_manager, operator_id, session_id, case_id)
        logger.info("[UPLOAD] session=%s user=%r case=%s files=%d",
                    session.session_id.value, message, case_id, len(files))

        if not files:
            # 没有文件，走普通聊天
            context = ContextSnapshot(
                case_id=CaseId(value=case_id or "unknown"),
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
            response = await engine.run(
                user_input=message,
                context=context,
                session={
                    "session_id": str(session.session_id.value),
                    "history": session.messages,
                },
            )
            session.messages.append({"role": "user", "content": message})
            if response.text_reply:
                session.messages.append({"role": "assistant", "content": response.text_reply})
            logger.info("[UPLOAD] session=%s tools=%s wf_ids=%s reply_len=%d error=%s",
                        session.session_id.value,
                        response.tools_used,
                        getattr(response, "workflow_ids", []),
                        len(response.text_reply or ""),
                        response.error)
            logger.info("[UPLOAD] reply=\n%s", response.text_reply)
            return ChatResponse(
                reply=response.text_reply,
                session_id=str(session.session_id.value),
                tools_used=response.tools_used,
                tier=response.tier_used.value,
                error=response.error,
                workflow_ids=getattr(response, "workflow_ids", []),
            )

        # 有文件：解析文件，构建多模态消息，走 ReAct 引擎
        response = await _handle_file_upload_via_react(
            message=message,
            session=session,
            files=files,
            file_handler=file_handler,
            engine=engine,
            image_sessions=image_sessions,
        )
        session.messages.append({"role": "user", "content": message})
        if response.reply:
            session.messages.append({"role": "assistant", "content": response.reply})
        logger.info("[UPLOAD] session=%s tools=%s wf_ids=%s reply_len=%d error=%s",
                    session.session_id.value,
                    response.tools_used,
                    getattr(response, "workflow_ids", []),
                    len(response.reply or ""),
                    response.error)
        logger.info("[UPLOAD] reply=\n%s", response.reply)
        return response

    # ── 流式聊天 (SSE) ──

    @router.post("/stream")
    async def chat_stream(request: ChatRequest):
        """流式聊天端点 — SSE (Server-Sent Events)。

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
        user_msg = request.message

        async def event_generator():
            """SSE 事件生成器 — 桥接 engine.run_stream()。

            流结束后在 finally 块追加对话到 session.messages，供下一轮作为历史。
            客户端断开时 generator 被取消，finally 仍执行（只追加已收到的内容）。
            """
            final_reply = ""
            tools_used_list: list[str] = []
            workflow_ids_list: list[str] = []
            try:
                async for event in engine.run_stream(
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
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as e:
                err_event = {"event": "error", "data": {"message": f"stream error: {e}"}}
                yield f"data: {json.dumps(err_event, ensure_ascii=False)}\n\n"
            finally:
                # 流结束后追加对话到 session.messages，供下一轮作为历史
                session.messages.append({"role": "user", "content": user_msg})
                if final_reply:
                    session.messages.append({"role": "assistant", "content": final_reply})
                logger.info("[STREAM] session=%s tools=%s wf_ids=%s reply_len=%d",
                            session_id_str, tools_used_list, workflow_ids_list, len(final_reply))

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲，确保 token 实时推送
            },
        )

    # ── WebSocket ── (Phase 3 子项目 D: AgentLoop 双 task)

    @router.websocket("/ws")
    async def websocket_chat(websocket: WebSocket) -> None:
        await websocket.accept()

        from cognitiveplane.control.agent_loop import AgentLoop
        from cognitiveplane.control.event_log import EventLog

        event_log = EventLog(case_id=CaseId(value="app-shared"))

        async def send_json(data: dict) -> None:
            await websocket.send_json(data)

        # 治理一致: /ws 路径必须与 HTTP /api/v1/chat 用同一套 hooks (SafetyHook + PolicyHook).
        # 不能让同一工具调用因入口不同而绕过 ToolPolicy.
        loop = AgentLoop(
            deps=deps,
            event_log=event_log,
            send_json=send_json,
            hooks=hooks,
        )
        await loop.start()

        try:
            while True:
                data = await websocket.receive_json()
                await loop.put_message(data)
        except WebSocketDisconnect:
            pass
        finally:
            await loop.stop()

    return router


def _get_or_create_session(session_manager, operator_id, session_id, case_id):
    """获取或创建会话（复用前端传的 session_id，保证历史对话累积）。"""
    return session_manager.get_or_create_session(operator_id, session_id, case_id)


async def _handle_file_upload_via_react(
    message: str,
    session,
    files: list[UploadFile],
    file_handler: "FileHandler",
    engine: "ReActEngine",
    image_sessions: "ImageSessionRegistry",
) -> ChatResponse:
    """处理 multipart 上传的文件 — 通过 ReAct 引擎分析。

    Plan §2.3 双轨:
      消息层 (LLM 直接看 thumbnail):
        content = [
          {"type": "text", "text": "<message> + [图片清单 + image_id 引用]"},
          {"type": "image_url", "image_url": {"url": "<thumbnail_1>"}},
          {"type": "image_url", "image_url": {"url": "<thumbnail_2>"}},
          ...
        ]

      工具层 (LLM 调 analyze_image):
        analyze_image(image_id="<uuid>") → 工具从 ImageStore 取原图送 vision_complete

    文档: 文本附加到消息 text 部分。
    """
    from cognitiveplane.interaction.file_handler import UploadedFile
    from cognitiveplane.shared.dto.context import ContextSnapshot
    from cognitiveplane.shared.enums import EventType, NoveltyLevel
    from cognitiveplane.shared.types import CaseId
    from datetime import datetime, timezone

    # 1. 将 UploadFile 转为 UploadedFile
    uploaded_files: list[UploadedFile] = []
    for f in files:
        raw_bytes = await f.read()
        uploaded_files.append(UploadedFile(
            filename=f.filename or "unknown",
            content_type=f.content_type or "application/octet-stream",
            size=len(raw_bytes),
            data=raw_bytes,
        ))

    # 2. 解析文件 (ImageStore 自动生成 thumbnail + 分配 image_id)
    # plan §2.3: image_id 格式 PENDING:{session_id}:{index} — 需先取 session_id
    session_id_str = str(session.session_id.value)
    result = await file_handler.process_uploaded_files(
        uploaded_files, message, session_id=session_id_str
    )

    # 3. 注册 image_ids 到 session — analyze_image 通过 image_id 取原图
    image_ids = result.image_ids
    image_sessions.register(session_id_str, image_ids)

    # 4. 构建用户消息 (plan §2.3 双轨: 消息层 thumbnail + 工具层 image_id)
    # ReActEngine 按主 LLM 能力降级 (plan line 3166):
    #   - 多模态主 LLM: 完整 content_parts (text + image_url thumbnails)
    #   - 文本主 LLM (DeepSeek): 引擎过滤 image_url, 只留 text + image_ref 引用
    # 工具层始终走 analyze_image(image_ref=...) 拿原图送 vision_complete
    text_parts = [message]
    if image_ids:
        id_list = ", ".join(image_ids)
        text_parts.append(
            f"\n\n[系统提示：用户上传了 {len(image_ids)} 张焊缝图片。"
            f"图片 image_ref 清单（按上传顺序）：{id_list}。"
            f"调用 analyze_image(image_ref=\"<ref>\", question=\"...\") 分析图片。"
            f"工具会取原图送给视觉模型分析。]"
            f"\n[重要] 当你调用 design_workflow 编排工作流时，必须把上述 image_ref 清单"
            f"完整传入 image_refs 参数（数组形式，如 [\"{image_ids[0]}\"]），"
            f"否则下游 L3 activity（如 IQA 图像质量评估）将无法获取图片导致执行失败。"
            f"即使用户只说\"分析这张图\"，也要把 image_refs 传给 design_workflow。"
        )
    if result.text_context.strip():
        text_parts.append(f"\n\n[附带的文档内容：]\n{result.text_context[:4000]}")
    if result.summary:
        text_parts.append(f"\n\n[已处理文件: {result.summary}]")

    text_payload = "".join(text_parts)

    # plan §2.3 + §A.3: 消息层注入 thumbnail (多模态 content_parts)
    # DeepSeek 主循环不接收 image_url — ReActEngine._adapt_user_input 会过滤
    user_message: str | list[dict]
    if result.thumbnails:
        user_message = [{"type": "text", "text": text_payload}]
        for thumb in result.thumbnails:
            user_message.append({
                "type": "image_url",
                "image_url": {"url": thumb},
            })
    else:
        user_message = text_payload

    # 5. 构建 context 并走 ReAct 引擎
    context = ContextSnapshot(
        case_id=CaseId(value="file-upload"),
        event_type=EventType.WORKFLOW_ENTERED,
        workflow_state={},
        case_data={"uploaded_files": result.summary, "image_ids": image_ids},
        measurements=[],
        memory_match_confidence=0.5,
        knowledge_coverage=0.5,
        event_novelty=NoveltyLevel.PARTIAL,
        validation_critical_count=0,
        timestamp=datetime.now(timezone.utc),
    )

    response = await engine.run(
        user_input=user_message,
        context=context,
        session={
            "session_id": session_id_str,
            "history": session.messages,
        },
    )

    return ChatResponse(
        reply=response.text_reply,
        session_id=session_id_str,
        tools_used=response.tools_used,
        tier=response.tier_used.value,
        error=response.error,
        workflow_ids=getattr(response, "workflow_ids", []),
    )
