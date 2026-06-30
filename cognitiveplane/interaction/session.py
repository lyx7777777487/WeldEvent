"""Session management — per-operator state isolation.

Source: 7-plane redesign spec §7. Fix P1-8: operator-isolated sessions.
Fix P2-9: session cleanup on SESSION_CLOSED/PUBLISHED.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from cognitiveplane.shared.enums import SessionStatus
from cognitiveplane.shared.types import SessionId


@dataclass
class Session:
    """A single conversation session."""
    session_id: SessionId
    operator_id: str
    case_id: str | None = None
    mode: str | None = None
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = field(default_factory=dict)
    messages: list[dict] = field(default_factory=list)


class SessionManager:
    """Per-operator session management with isolation.

    Structure: dict[operator_id, dict[session_id, Session]]
    Ensures operators cannot access each other's sessions (P1-8 fix).
    P2-9 fix: removes sessions on SESSION_CLOSED/PUBLISHED via cleanup_on_event().
    """

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Session]] = {}

    def create_session(self, operator_id: str, case_id: str | None = None) -> Session:
        """Create a new session for an operator."""
        session = Session(
            session_id=SessionId(value=uuid4()),
            operator_id=operator_id,
            case_id=case_id,
        )
        if operator_id not in self._sessions:
            self._sessions[operator_id] = {}
        self._sessions[operator_id][str(session.session_id.value)] = session
        return session

    def get_or_create_session(
        self,
        operator_id: str,
        session_id: str | None,
        case_id: str | None = None,
    ) -> Session:
        """按 session_id 获取会话；不存在则用该 session_id 创建（复用前端 id）。

        前端 localStorage 维护会话列表，每条会话有稳定 id（如 'sess_1719xxx'）。
        后端需复用该 id，否则每次请求都创建新 UUID，历史对话无法累积。
        L1 重启后内存清空，前端传的 id 在后端查不到时也用同一 id 重建，
        保证重启后前端会话 id 不变。
        """
        if session_id:
            existing = self._sessions.get(operator_id, {}).get(session_id)
            if existing is not None:
                return existing
            # 用前端传的 session_id 创建（而非随机 UUID），保证跨轮次稳定
            session = Session(
                session_id=SessionId(value=session_id),
                operator_id=operator_id,
                case_id=case_id,
            )
            if operator_id not in self._sessions:
                self._sessions[operator_id] = {}
            self._sessions[operator_id][session_id] = session
            return session
        # 没传 session_id → 创建全新会话（随机 UUID）
        return self.create_session(operator_id, case_id)

    def get_session(self, operator_id: str, session_id: str) -> Session | None:
        """Get a session by operator and session ID."""
        return self._sessions.get(operator_id, {}).get(session_id)

    def get_active_session(self, operator_id: str) -> Session | None:
        """Get the most recently updated session for an operator."""
        operator_sessions = self._sessions.get(operator_id, {})
        if not operator_sessions:
            return None
        active = [s for s in operator_sessions.values() if s.status == SessionStatus.ACTIVE]
        if not active:
            return None
        return max(active, key=lambda s: s.updated_at)

    def list_sessions(self, operator_id: str) -> list[Session]:
        """List all sessions for an operator."""
        return list(self._sessions.get(operator_id, {}).values())

    def close_session(self, operator_id: str, session_id: str) -> bool:
        """Close and remove a session."""
        if operator_id in self._sessions and session_id in self._sessions[operator_id]:
            self._sessions[operator_id][session_id].status = SessionStatus.COMPLETED
            del self._sessions[operator_id][session_id]
            return True
        return False

    def cleanup_on_event(self, event_type: str, operator_id: str, session_id: str | None = None) -> None:
        """P2-9 fix: Clean up sessions on terminal events.

        Removes completed sessions when SESSION_CLOSED or PUBLISHED events occur.
        """
        terminal_events = {"SESSION_CLOSED", "PUBLISHED"}
        if event_type not in terminal_events:
            return

        if session_id:
            self.close_session(operator_id, session_id)
        else:
            # Remove all completed sessions for the operator
            if operator_id in self._sessions:
                self._sessions[operator_id] = {
                    sid: s for sid, s in self._sessions[operator_id].items()
                    if s.status == SessionStatus.ACTIVE
                }
