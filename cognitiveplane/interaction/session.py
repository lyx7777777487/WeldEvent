"""Session management — per-operator state isolation.

Source: 7-plane redesign spec §7. Fix P1-8: operator-isolated sessions.
Fix P2-9: session cleanup on SESSION_CLOSED/PUBLISHED.
Fix C4: TTL-based cleanup to prevent memory leaks — 后台 task 定期清理
        超时未活跃的 session，防止用户关闭浏览器后 session 永驻内存。
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from cognitiveplane.shared.enums import SessionStatus
from cognitiveplane.shared.types import SessionId

logger = logging.getLogger(__name__)


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
    C4 fix: TTL-based cleanup — 后台 asyncio task 定期清理超时未活跃的 session。
            每次 get/create 时刷新 updated_at（活跃心跳），超 TTL 未活跃则回收。
    """

    def __init__(
        self,
        ttl_minutes: int = 30,
        cleanup_interval_seconds: int = 300,
    ) -> None:
        self._sessions: dict[str, dict[str, Session]] = {}
        # C4: TTL 清理配置
        self._ttl: timedelta = timedelta(minutes=ttl_minutes)
        self._cleanup_interval: float = float(cleanup_interval_seconds)
        self._cleanup_task: asyncio.Task | None = None

    def create_session(self, operator_id: str, case_id: str | None = None) -> Session:
        """Create a new session for an operator."""
        session = Session(
            session_id=SessionId(value=str(uuid4())),
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
        """按 session_id 获取会话；不存在则用该 id 创建（复用前端 id）。

        前端 localStorage 维护会话列表，每条会话有稳定 id（如 'sess_1719xxx'）。
        后端需复用该 id，否则每次请求都创建新 UUID，历史对话无法累积。
        L1 重启后内存清空，前端传的 id 在后端查不到时也用同一 id 重建，
        保证重启后前端会话 id 不变。

        命中已有 session 时刷新 updated_at（活跃心跳），避免被 TTL 误回收。
        """
        if session_id:
            existing = self._sessions.get(operator_id, {}).get(session_id)
            if existing is not None:
                self._touch(existing)
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
        """Get a session by operator and session ID. 命中时刷新活跃心跳。"""
        session = self._sessions.get(operator_id, {}).get(session_id)
        if session is not None:
            self._touch(session)
        return session

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

    # ------------------------------------------------------------------
    # C4: TTL-based cleanup
    # ------------------------------------------------------------------

    @staticmethod
    def _touch(session: Session) -> None:
        """刷新 session 活跃心跳（updated_at）。"""
        session.updated_at = datetime.now(timezone.utc)

    def cleanup_expired(self) -> int:
        """清理超过 TTL 未活跃的 session。

        扫描所有 session，删除 updated_at 早于 (now - ttl) 的记录。
        空的 operator dict 也会被回收。

        Returns:
            被清理的 session 数量。
        """
        now = datetime.now(timezone.utc)
        cutoff = now - self._ttl
        removed = 0
        for operator_id in list(self._sessions.keys()):
            sessions = self._sessions[operator_id]
            for sid in list(sessions.keys()):
                s = sessions[sid]
                if s.updated_at < cutoff:
                    del sessions[sid]
                    removed += 1
                    logger.debug(
                        "Session expired (TTL): operator=%s session=%s "
                        "last_active=%s age=%s",
                        operator_id, sid, s.updated_at, now - s.updated_at,
                    )
            if not sessions:
                del self._sessions[operator_id]
        if removed:
            logger.info("SessionManager TTL cleanup: removed %d expired sessions", removed)
        return removed

    async def start_cleanup_task(self) -> None:
        """启动后台 TTL 清理 task（幂等）。

        在 app lifespan startup 时调用。若已在运行则不重复启动。
        task 内部捕获所有异常，不会因单轮失败而退出。
        """
        if self._cleanup_task is not None and not self._cleanup_task.done():
            return
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(), name="session-ttl-cleanup"
        )
        logger.info(
            "SessionManager TTL cleanup task started (ttl=%s interval=%ss)",
            self._ttl, self._cleanup_interval,
        )

    async def stop_cleanup_task(self) -> None:
        """停止后台 TTL 清理 task（幂等）。

        在 app lifespan shutdown 时调用，确保进程能优雅退出。
        """
        if self._cleanup_task is None:
            return
        self._cleanup_task.cancel()
        try:
            await self._cleanup_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("SessionManager cleanup task raised on shutdown")
        self._cleanup_task = None
        logger.info("SessionManager TTL cleanup task stopped")

    async def _cleanup_loop(self) -> None:
        """后台周期性清理循环。

        任何单轮异常都被捕获并记录，循环不会因异常退出，
        只有 cancel() 才能终止（由 stop_cleanup_task 触发）。
        """
        while True:
            try:
                await asyncio.sleep(self._cleanup_interval)
                self.cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("SessionManager cleanup loop error")
                continue
