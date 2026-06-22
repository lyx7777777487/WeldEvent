"""FastAPI chat router — POST /api/v1/chat and WebSocket /ws.

支持两种上传模式:
  - JSON: 纯文本聊天（无文件）
  - multipart/form-data: 带文件上传（工业场景大文件）

Source: 7-plane redesign spec §7 FastAPI.
"""

from fastapi import APIRouter, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Optional

from cognitiveplane.control.deps import CognitiveDependencies


# 临时图片缓存: session_id -> list[base64 data URL]
# analyze_image tool 通过 PENDING:session_id:index 引用
_pending_images: dict[str, list[str]] = {}


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
    from cognitiveplane.shared.dto_context import ContextSnapshot
    from cognitiveplane.shared.enums import EventType, NoveltyLevel
    from cognitiveplane.shared.types import CaseId
    from datetime import datetime, timezone

    router = APIRouter(prefix="/api/v1/chat", tags=["chat"])
    session_manager = SessionManager()
    tool_policy = ToolPolicy()
    hooks = [SafetyHook(), PolicyHook(tool_policy)]
    engine = ReActEngine(deps, hooks=hooks)
    file_handler = FileHandler(llm_provider=deps.capability.llm_provider)

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

        文件上传走 ReAct 引擎：图片通过 analyze_image tool 分析，
        文档通过 search_standards/search_process 等工具辅助分析。
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

        # 有文件：解析文件，构建增强消息，走 ReAct 引擎
        return await _handle_file_upload_via_react(
            message=message,
            session=session,
            files=files,
            file_handler=file_handler,
            engine=engine,
        )

    # ── WebSocket ──

    @router.websocket("/ws")
    async def websocket_chat(websocket: WebSocket) -> None:
        await websocket.accept()
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

                response = await engine.run(
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
) -> ChatResponse:
    """处理 multipart 上传的文件 — 通过 ReAct 引擎分析。

    图片: 将 base64 data URL 存入临时缓存，ReAct 引擎通过 analyze_image tool 读取。
    文档: 将文本内容附加到消息中，ReAct 引擎会调用 search_standards 等工具辅助分析。
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

    # 2. 解析文件
    result = await file_handler.process_uploaded_files(uploaded_files, message)

    # 3. 将图片数据存入临时缓存，供 analyze_image tool 读取
    _pending_images[str(session.session_id.value)] = result.image_urls

    # 4. 构建增强消息 — 让 ReAct 引擎决定调用哪些工具
    enhanced_parts = [message]

    if result.image_urls:
        image_count = len(result.image_urls)
        pending_ref = f"PENDING:{session.session_id.value}:0"
        enhanced_parts.append(
            f"\n\n[系统提示：用户上传了 {image_count} 张焊缝图片。"
            f"请立即调用 analyze_image 工具分析第1张图片。"
            f"image_data 参数使用 '{pending_ref}' 格式引用。]"
        )
        if image_count > 1:
            enhanced_parts.append(
                f"（共 {image_count} 张图片，分析完第1张后可继续分析其他图片，"
                f"索引从0开始：PENDING:{session.session_id.value}:0, "
                f"PENDING:{session.session_id.value}:1, ...）"
            )

    if result.text_context.strip():
        enhanced_parts.append(
            f"\n\n[附带的文档内容：]\n{result.text_context[:4000]}"
        )

    if result.summary:
        enhanced_parts.append(f"\n\n[已处理文件: {result.summary}]")

    enhanced_message = "".join(enhanced_parts)

    # 5. 构建 context 并走 ReAct 引擎
    context = ContextSnapshot(
        case_id=CaseId(value="file-upload"),
        event_type=EventType.WORKFLOW_ENTERED,
        workflow_state={},
        case_data={"uploaded_files": result.summary},
        measurements=[],
        memory_match_confidence=0.5,
        knowledge_coverage=0.5,
        event_novelty=NoveltyLevel.PARTIAL,
        validation_critical_count=0,
        timestamp=datetime.now(timezone.utc),
    )

    response = await engine.run(
        user_input=enhanced_message,
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
