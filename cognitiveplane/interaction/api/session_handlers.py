"""Session management endpoint handlers — GET /sessions and
GET /sessions/{session_id}/export.

Extracted from chat.py create_chat_router.

Source: 7-plane redesign spec §7 FastAPI.
"""


async def handle_list_sessions(
    *,
    session_manager,
):
    """列出所有 session（调试用，导出对话历史）.
    GET /api/v1/chat/sessions.
    """
    sessions = []
    for sid, session in session_manager._sessions.items():
        sessions.append({
            "session_id": sid,
            "message_count": len(session.messages),
            "created_at": getattr(session, "created_at", None),
            "last_active": getattr(session, "last_active", None),
            "preview": (session.messages[-1].get("content", "")[:80]
                        if session.messages else ""),
        })
    return {"sessions": sessions, "total": len(sessions)}


async def handle_export_session(
    session_id: str,
    *,
    session_manager,
):
    """导出单个 session 的完整对话历史（调试用）.
    GET /api/v1/chat/sessions/{session_id}/export.
    """
    session = session_manager._sessions.get(session_id)
    if session is None:
        return {"error": f"session {session_id} not found", "available": list(session_manager._sessions.keys())}
    return {
        "session_id": session_id,
        "message_count": len(session.messages),
        "messages": session.messages,
    }
