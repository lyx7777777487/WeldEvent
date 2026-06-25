"""Test AgentLoop — Phase 3 子项目 D 双 task + WebSocket 双向.

Spec: docs/superpowers/specs/2026-06-25-phase3-d-agentloop-websocket-design.md
"""
from __future__ import annotations

import pytest

from cognitiveplane.control.agent_loop import (
    FeedbackMessage,
    InterruptMessage,
    FeedbackSummary,
)


def test_feedback_message_schema_minimal():
    """FeedbackMessage 必填字段 + 默认 category."""
    msg = FeedbackMessage(
        correction="缺陷位置标错了, 应该在焊缝根部",
        session_id="sess-1",
    )
    assert msg.correction == "缺陷位置标错了, 应该在焊缝根部"
    assert msg.session_id == "sess-1"
    assert msg.type == "feedback"
    assert msg.category == "other"
    assert msg.target_tool_call_id is None


def test_feedback_message_schema_full():
    """FeedbackMessage 全字段."""
    msg = FeedbackMessage(
        correction="用错了工具",
        session_id="sess-1",
        target_tool_call_id="call_abc",
        category="wrong_tool",
    )
    assert msg.target_tool_call_id == "call_abc"
    assert msg.category == "wrong_tool"


def test_interrupt_message_schema():
    """InterruptMessage 必填 + 可选 reason."""
    msg = InterruptMessage(session_id="sess-1")
    assert msg.type == "interrupt"
    assert msg.reason is None

    msg2 = InterruptMessage(session_id="sess-1", reason="停, 我看错了")
    assert msg2.reason == "停, 我看错了"


def test_feedback_summary_from_message():
    """FeedbackSummary 由 FeedbackMessage 派生 — 用于 queue 传递."""
    msg = FeedbackMessage(
        correction="修正内容",
        session_id="sess-1",
        category="wrong_result",
    )
    summary = FeedbackSummary.from_message(msg, memory_id="mem-xyz")
    assert summary.correction == "修正内容"
    assert summary.memory_id == "mem-xyz"
    assert summary.category == "wrong_result"


import asyncio

from cognitiveplane.control.agent_loop import AgentLoop
from cognitiveplane.control.deps import CognitiveDependencies
from cognitiveplane.control.event_log import EventLog
from cognitiveplane.memory.adapters.port_adapters import (
    MemorySearchAdapter,
    MemoryWriteAdapter,
)
from cognitiveplane.memory.repositories.in_memory import InMemoryMemoryRepository
from cognitiveplane.shared.types import CaseId


def _make_deps() -> tuple[CognitiveDependencies, InMemoryMemoryRepository]:
    """构造带 InMemoryMemoryRepository 的 deps — feedback 写入用."""
    from cognitiveplane.control.deps import (
        CapabilityDeps, ControlDeps, GatewayDeps, GovernanceDeps,
        KnowledgeDeps, MemoryDeps,
    )
    repo = InMemoryMemoryRepository()
    deps = CognitiveDependencies(
        capability=CapabilityDeps(),
        control=ControlDeps(),
        knowledge=KnowledgeDeps(),
        memory=MemoryDeps(
            search=MemorySearchAdapter(repo),
            write=MemoryWriteAdapter(repo),
        ),
        gateway=GatewayDeps(),
        governance=GovernanceDeps(),
    )
    return deps, repo


@pytest.mark.asyncio
async def test_agentloop_construct_and_start():
    """AgentLoop 构造 + start() 启动 receive_task, stop() 取消."""
    deps, _ = _make_deps()
    event_log = EventLog(case_id=CaseId(value="test"))

    loop = AgentLoop(
        deps=deps,
        event_log=event_log,
        send_json=lambda data: None,  # no-op for unit test
    )
    assert loop.is_running is False

    await loop.start()
    assert loop.is_running is True

    await loop.stop()
    assert loop.is_running is False


@pytest.mark.asyncio
async def test_agentloop_stop_is_idempotent():
    """stop() 多次调用不抛异常."""
    deps, _ = _make_deps()
    loop = AgentLoop(
        deps=deps,
        event_log=EventLog(case_id=CaseId(value="test")),
        send_json=lambda data: None,
    )
    await loop.start()
    await loop.stop()
    await loop.stop()  # idempotent
