"""FastAPI chat router — POST /api/v1/chat and WebSocket /ws.

支持两种上传模式:
  - JSON: 纯文本聊天（无文件）
  - multipart/form-data: 带文件上传（工业场景大文件）

Plan §2.3 双轨:
  - 消息层: thumbnail data URL 直接进 LLM content (LLM 直接看图)
  - 工具层: LLM 调 analyze_image(image_id=...) 拿原图 (只传引用)

Plan §A.3:
  - 上传图统一生成 Thumbnail 注入消息层 (成本闸门)
  - 原图保留在 ImageStore (内存，阶段 3+ 切 MinIO)

Source: 7-plane redesign spec §7 FastAPI + plan §2.3 + §A.3.
"""

from fastapi import APIRouter, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Any, Optional

from cognitiveplane.control.deps import CognitiveDependencies


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


def create_chat_router(deps: CognitiveDependencies) -> APIRouter:
    """Create FastAPI router for chat endpoints."""
    from cognitiveplane.control.react import ReActEngine
    from cognitiveplane.control.hooks import SafetyHook, PolicyHook
    from cognitiveplane.governance.tool_policy import ToolPolicy
    from cognitiveplane.interaction.session import SessionManager
    from cognitiveplane.interaction.file_handler import FileHandler
    from cognitiveplane.interaction.image_store import ImageStore
    from cognitiveplane.interaction.api.image_session import ImageSessionRegistry
    from cognitiveplane.shared.dto_context import ContextSnapshot
    from cognitiveplane.shared.enums import EventType, NoveltyLevel
    from cognitiveplane.shared.types import CaseId
    from datetime import datetime, timezone

    router = APIRouter(prefix="/api/v1/chat", tags=["chat"])
    session_manager = SessionManager()
    tool_policy = ToolPolicy()
    hooks = [SafetyHook(), PolicyHook(tool_policy)]
    # plan §A.3: ImageStore 由 app._build_dependencies 注入，chat router 和 ToolRegistry 共享
    # 注意: ImageStore 定义了 __len__，空 store 在布尔上下文是 falsy，必须用 is None 判断
    image_store = deps.capability.image_store if deps.capability.image_store is not None else ImageStore()
    image_sessions = ImageSessionRegistry(image_store)
    engine = ReActEngine(deps, hooks=hooks)
    file_handler = FileHandler(llm_provider=deps.capability.llm_provider, image_store=image_store)

    # ── JSON 纯文本聊天 ──

    @router.post("/", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        session = _get_or_create_session(session_manager, request.operator_id, request.session_id, request.case_id)

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
            session={"session_id": str(session.session_id.value)},
        )

        return ChatResponse(
            reply=response.text_reply,
            session_id=str(session.session_id.value),
            tools_used=response.tools_used,
            tier=response.tier_used.value,
            error=response.error,
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
                session={"session_id": str(session.session_id.value)},
            )
            return ChatResponse(
                reply=response.text_reply,
                session_id=str(session.session_id.value),
                tools_used=response.tools_used,
                tier=response.tier_used.value,
                error=response.error,
            )

        # 有文件：解析文件，构建多模态消息，走 ReAct 引擎
        return await _handle_file_upload_via_react(
            message=message,
            session=session,
            files=files,
            file_handler=file_handler,
            engine=engine,
            image_sessions=image_sessions,
        )

    # ── WebSocket ──

    @router.websocket("/ws")
    async def websocket_chat(websocket: WebSocket) -> None:
        await websocket.accept()

        async def stream_event(event_type: str, payload: dict) -> None:
            await websocket.send_json({"type": event_type, **payload})

        ws_engine = ReActEngine(deps, hooks=hooks, event_callback=stream_event)

        try:
            while True:
                data = await websocket.receive_json()
                request = ChatRequest(**data)
                session = session_manager.create_session(request.operator_id, request.case_id)

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

                response = await ws_engine.run(
                    user_input=request.message,
                    context=context,
                    session={"session_id": str(session.session_id.value)},
                )

                await websocket.send_json(ChatResponse(
                    reply=response.text_reply,
                    session_id=str(session.session_id.value),
                    tools_used=response.tools_used,
                    tier=response.tier_used.value,
                    error=response.error,
                ).model_dump())
        except WebSocketDisconnect:
            pass

    return router


def _get_or_create_session(session_manager, operator_id, session_id, case_id):
    """获取或创建会话。"""
    session = None
    if session_id:
        session = session_manager.get_session(operator_id, session_id)
    if session is None:
        session = session_manager.create_session(operator_id, case_id)
    return session


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
    from cognitiveplane.shared.dto_context import ContextSnapshot
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
    result = await file_handler.process_uploaded_files(uploaded_files, message)

    # 3. 注册 image_ids 到 session — analyze_image 通过 image_id 取原图
    session_id_str = str(session.session_id.value)
    image_ids = result.image_ids
    image_sessions.register(session_id_str, image_ids)

    # 4. 构建用户消息 (plan §2.3 双轨: 消息层 thumbnail + 工具层 image_id)
    # ReActEngine 按主 LLM 能力降级 (plan line 3166):
    #   - 多模态主 LLM: 完整 content_parts (text + image_url thumbnails)
    #   - 文本主 LLM (DeepSeek): 引擎过滤 image_url, 只留 text + image_id 引用
    # 工具层始终走 analyze_image(image_id=...) 拿原图送 vision_complete
    text_parts = [message]
    if image_ids:
        id_list = ", ".join(image_ids)
        text_parts.append(
            f"\n\n[系统提示：用户上传了 {len(image_ids)} 张焊缝图片。"
            f"图片 image_id 清单（按上传顺序）：{id_list}。"
            f"调用 analyze_image(image_id=\"<id>\", question=\"...\") 分析图片。"
            f"工具会取原图送给视觉模型分析。]"
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
        session={"session_id": session_id_str},
    )

    return ChatResponse(
        reply=response.text_reply,
        session_id=session_id_str,
        tools_used=response.tools_used,
        tier=response.tier_used.value,
        error=response.error,
    )
