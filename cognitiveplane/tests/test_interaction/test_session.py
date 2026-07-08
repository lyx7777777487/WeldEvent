"""Session manager — per-operator isolation tests (spec §7, P1-8/P2-9)."""

from __future__ import annotations

import pytest

from cognitiveplane.interaction.session import Session, SessionManager


def test_create_session_returns_session_with_operator():
    mgr = SessionManager()
    s = mgr.create_session("op-1")
    assert s.operator_id == "op-1"
    assert s.status.value == "active"


def test_operator_isolation_operator_cannot_see_other_sessions():
    mgr = SessionManager()
    s1 = mgr.create_session("op-1")
    s2 = mgr.create_session("op-2")
    assert mgr.get_session("op-1", str(s1.session_id.value)) is s1
    assert mgr.get_session("op-1", str(s2.session_id.value)) is None
    assert mgr.get_session("op-2", str(s2.session_id.value)) is s2


def test_close_session_removes_from_manager():
    mgr = SessionManager()
    s = mgr.create_session("op-1")
    sid = str(s.session_id.value)
    assert mgr.close_session("op-1", sid) is True
    assert mgr.get_session("op-1", sid) is None


def test_close_session_wrong_operator_fails():
    mgr = SessionManager()
    s = mgr.create_session("op-1")
    assert mgr.close_session("op-2", str(s.session_id.value)) is False


def test_get_active_session_returns_latest():
    mgr = SessionManager()
    mgr.create_session("op-1")
    latest = mgr.create_session("op-1")
    active = mgr.get_active_session("op-1")
    assert active is latest


def test_list_sessions_scoped_to_operator():
    mgr = SessionManager()
    mgr.create_session("op-1")
    mgr.create_session("op-1")
    mgr.create_session("op-2")
    assert len(mgr.list_sessions("op-1")) == 2
    assert len(mgr.list_sessions("op-2")) == 1
    assert len(mgr.list_sessions("op-3")) == 0


def test_cleanup_on_session_closed_event():
    mgr = SessionManager()
    s = mgr.create_session("op-1")
    sid = str(s.session_id.value)
    mgr.cleanup_on_event("SESSION_CLOSED", "op-1", sid)
    assert mgr.get_session("op-1", sid) is None


def test_cleanup_on_published_event():
    mgr = SessionManager()
    s = mgr.create_session("op-1")
    sid = str(s.session_id.value)
    mgr.cleanup_on_event("PUBLISHED", "op-1", sid)
    assert mgr.get_session("op-1", sid) is None


def test_cleanup_ignores_non_terminal_event():
    mgr = SessionManager()
    s = mgr.create_session("op-1")
    mgr.cleanup_on_event("REASONING", "op-1", str(s.session_id.value))
    assert mgr.get_active_session("op-1") is not None


# C4: TTL-based cleanup tests


def test_cleanup_expired_removes_idle_sessions():
    """超 TTL 未活跃的 session 应被清理。"""
    from datetime import timedelta
    from cognitiveplane.interaction.session import Session, SessionStatus
    from cognitiveplane.shared.types import SessionId

    mgr = SessionManager(ttl_minutes=30)
    # 创建两个 session
    s1 = mgr.create_session("op-1")
    s2 = mgr.create_session("op-1")
    # 手动把 s1 的 updated_at 调到 1 小时前（超 TTL）
    old_time = s1.updated_at - timedelta(hours=1)
    s1.updated_at = old_time
    # s2 保持 now（活跃）

    removed = mgr.cleanup_expired()

    assert removed == 1
    assert mgr.get_session("op-1", str(s1.session_id.value)) is None
    assert mgr.get_session("op-1", str(s2.session_id.value)) is s2


def test_cleanup_expired_keeps_active_sessions():
    """活跃 session（updated_at 在 TTL 内）不应被清理。"""
    mgr = SessionManager(ttl_minutes=30)
    s1 = mgr.create_session("op-1")
    s2 = mgr.create_session("op-2")

    removed = mgr.cleanup_expired()

    assert removed == 0
    assert mgr.get_session("op-1", str(s1.session_id.value)) is s1
    assert mgr.get_session("op-2", str(s2.session_id.value)) is s2


def test_get_session_touches_updated_at():
    """命中 session 时刷新 updated_at（活跃心跳），避免被 TTL 误回收。"""
    from datetime import timedelta
    mgr = SessionManager(ttl_minutes=30)
    s = mgr.create_session("op-1")
    sid = str(s.session_id.value)
    old_time = s.updated_at - timedelta(minutes=29)  # 接近 TTL 边界
    s.updated_at = old_time

    # get_session 应刷新 updated_at
    fetched = mgr.get_session("op-1", sid)
    assert fetched is s
    assert s.updated_at > old_time  # 被刷新了

    # 此时清理不应删它（因为刚被访问）
    removed = mgr.cleanup_expired()
    assert removed == 0
    assert mgr.get_session("op-1", sid) is s


def test_cleanup_expired_removes_empty_operator_dict():
    """operator 下所有 session 被清理后，空的 operator dict 也应被回收。"""
    from datetime import timedelta
    mgr = SessionManager(ttl_minutes=30)
    s = mgr.create_session("op-1")
    s.updated_at -= timedelta(hours=1)

    removed = mgr.cleanup_expired()

    assert removed == 1
    assert "op-1" not in mgr._sessions  # 空 operator dict 已回收


@pytest.mark.asyncio
async def test_start_and_stop_cleanup_task():
    """TTL 清理后台 task 可幂等启动/停止。"""
    mgr = SessionManager(ttl_minutes=30, cleanup_interval_seconds=60)
    # 幂等启动
    await mgr.start_cleanup_task()
    assert mgr._cleanup_task is not None
    assert not mgr._cleanup_task.done()
    # 重复启动不创建新 task
    first_task = mgr._cleanup_task
    await mgr.start_cleanup_task()
    assert mgr._cleanup_task is first_task

    # 停止
    await mgr.stop_cleanup_task()
    assert mgr._cleanup_task is None
    # 幂等停止
    await mgr.stop_cleanup_task()

