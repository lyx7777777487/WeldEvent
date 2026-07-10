"""Chat endpoint handlers — POST / and POST /upload and GET /sessions/{id}/trajectory.

Extracted from chat.py create_chat_router. Each handler is a module-level
async function receiving its dependencies as explicit parameters; chat.py
registers them via ``@router.post(...)`` thin wrappers inside
``create_chat_router``.

Source: 7-plane redesign spec §7 FastAPI + plan §2.3 + §A.3.
"""

from fastapi import UploadFile, HTTPException
from fastapi.responses import JSONResponse
from typing import Optional

from cognitiveplane.interaction.api.chat import ChatRequest, ChatResponse
from cognitiveplane.interaction.api._helpers import (
    logger,
    _inject_session_image_refs,
    _get_or_create_session,
    _trigger_evaluation,
    build_runtime_session,
    persist_runtime_session_state,
)
from cognitiveplane.shared.dto.context import ContextSnapshot
from cognitiveplane.shared.enums import EventType, NoveltyLevel
from cognitiveplane.shared.types import CaseId
from datetime import datetime, timezone


async def handle_chat(
    request: ChatRequest,
    *,
    session_manager,
    engine,
    router,
    evaluator,
) -> ChatResponse:
    """JSON 纯文本聊天 — POST /api/v1/chat/."""
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

    runtime_session = build_runtime_session(session)
    response = await engine.run(
        user_input=_inject_session_image_refs(
            request.message, session.session_id.value, router,
        ),
        context=context,
        session=runtime_session,
    )
    persist_runtime_session_state(session, runtime_session)

    # 追加本轮对话到 session.messages，供下一轮作为历史
    session.messages.append({"role": "user", "content": request.message})
    if response.text_reply:
        assistant_msg = {"role": "assistant", "content": response.text_reply}
        if getattr(response, "reasoning_content", None):
            assistant_msg["reasoning_content"] = response.reasoning_content
        session.messages.append(assistant_msg)

    logger.info("[CHAT] session=%s tools=%s wf_ids=%s reply_len=%d error=%s",
                session.session_id.value,
                response.tools_used,
                getattr(response, "workflow_ids", []),
                len(response.text_reply or ""),
                response.error)
    logger.info("[CHAT] reply=\n%s", response.text_reply)

    _trigger_evaluation(
        evaluator=evaluator,
        user_input=request.message,
        response=response,
        session_id=str(session.session_id.value),
    )

    return ChatResponse(
        reply=response.text_reply,
        session_id=str(session.session_id.value),
        tools_used=response.tools_used,
        tier=response.tier_used.value,
        error=response.error,
        workflow_ids=getattr(response, "workflow_ids", []),
    )


async def handle_chat_with_files(
    message: str,
    operator_id: str,
    case_id: Optional[str],
    session_id: Optional[str],
    files: list[UploadFile],
    *,
    session_manager,
    engine,
    router,
    file_handler,
    image_sessions,
    evaluator,
) -> ChatResponse:
    """multipart/form-data 文件上传聊天 — POST /api/v1/chat/upload.

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
        runtime_session = build_runtime_session(session)
        response = await engine.run(
            user_input=_inject_session_image_refs(
                message, str(session.session_id.value), router,
            ),
            context=context,
            session=runtime_session,
        )
        persist_runtime_session_state(session, runtime_session)
        session.messages.append({"role": "user", "content": message})
        if response.text_reply:
            assistant_msg = {"role": "assistant", "content": response.text_reply}
            if getattr(response, "reasoning_content", None):
                assistant_msg["reasoning_content"] = response.reasoning_content
            session.messages.append(assistant_msg)
        logger.info("[UPLOAD] session=%s tools=%s wf_ids=%s reply_len=%d error=%s",
                    session.session_id.value,
                    response.tools_used,
                    getattr(response, "workflow_ids", []),
                    len(response.text_reply or ""),
                    response.error)
        logger.info("[UPLOAD] reply=\n%s", response.text_reply)
        _trigger_evaluation(
            evaluator=evaluator,
            user_input=message,
            response=response,
            session_id=str(session.session_id.value),
        )
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
        assistant_msg = {"role": "assistant", "content": response.reply}
        if getattr(response, "reasoning_content", None):
            assistant_msg["reasoning_content"] = response.reasoning_content
        session.messages.append(assistant_msg)
    logger.info("[UPLOAD] session=%s tools=%s wf_ids=%s reply_len=%d error=%s",
                session.session_id.value,
                response.tools_used,
                getattr(response, "workflow_ids", []),
                len(response.reply or ""),
                response.error)
    logger.info("[UPLOAD] reply=\n%s", response.reply)

    _trigger_evaluation(
        evaluator=evaluator,
        user_input=message,
        response=response,
        session_id=str(session.session_id.value),
    )
    return response


async def _handle_file_upload_via_react(
    message: str,
    session,
    files: list[UploadFile],
    file_handler,
    engine,
    image_sessions,
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

    runtime_session = build_runtime_session(session)
    response = await engine.run(
        user_input=user_message,
        context=context,
        session=runtime_session,
    )
    persist_runtime_session_state(session, runtime_session)

    return ChatResponse(
        reply=response.text_reply,
        session_id=session_id_str,
        tools_used=response.tools_used,
        tier=response.tier_used.value,
        error=response.error,
        workflow_ids=getattr(response, "workflow_ids", []),
    )


async def handle_trajectory(
    session_id: str,
    *,
    trajectory_store,
) -> JSONResponse:
    """导出 LLM 推理轨迹 — GET /api/v1/chat/sessions/{session_id}/trajectory.

    P0-3: 借鉴 trae-agent AgentExecution 导出，返回 per-step 推理轨迹 JSON。
    """
    recorder = trajectory_store.get(session_id)
    if recorder is None:
        raise HTTPException(status_code=404, detail=f"No trajectory found for session {session_id}")
    return JSONResponse(content=recorder._to_dict())
