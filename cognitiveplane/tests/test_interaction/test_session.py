"""Session manager — per-operator isolation tests (spec §7, P1-8/P2-9)."""

from __future__ import annotations

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
