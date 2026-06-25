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


def _make_send_json(sink: list[dict]):
    """返回 async send_json — 把消息 append 到 sink. (Python 无 async lambda, 用工厂.)"""
    async def _send(data: dict) -> None:
        sink.append(data)
    return _send


async def _noop_send_json(data: dict) -> None:
    """async no-op send_json."""
    return None


@pytest.mark.asyncio
async def test_agentloop_construct_and_start():
    """AgentLoop 构造 + start() 启动 receive_task, stop() 取消."""
    deps, _ = _make_deps()
    event_log = EventLog(case_id=CaseId(value="test"))

    loop = AgentLoop(
        deps=deps,
        event_log=event_log,
        send_json=_noop_send_json,
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
        send_json=_noop_send_json,
    )
    await loop.start()
    await loop.stop()
    await loop.stop()  # idempotent


from cognitiveplane.capability.mock import MockLLMProvider
from cognitiveplane.control.deps import CapabilityDeps
from cognitiveplane.shared.dto_context import ContextSnapshot
from cognitiveplane.shared.enums import EventType, NoveltyLevel
from datetime import datetime, timezone


def _make_context() -> ContextSnapshot:
    return ContextSnapshot(
        case_id=CaseId(value="test"),
        event_type=EventType.WORKFLOW_ENTERED,
        workflow_state={},
        case_data={},
        measurements=[],
        memory_match_confidence=0.0,
        knowledge_coverage=0.0,
        event_novelty=NoveltyLevel.UNKNOWN,
        validation_critical_count=0,
        timestamp=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_chat_message_starts_react_task():
    """type=chat 消息 → 启动 react_task → 跑完返回 ChatResponse."""
    deps, _ = _make_deps()
    # 用 MockLLMProvider 让 ReActEngine 走 Tier-3 (无 FC, 无 JSON mode... 实际走 default)
    deps.capability.llm_provider = MockLLMProvider(default_response="测试答复")

    sent: list[dict] = []
    loop = AgentLoop(
        deps=deps,
        event_log=EventLog(case_id=CaseId(value="test")),
        send_json=_make_send_json(sent),
    )
    await loop.start()

    await loop.put_message({
        "type": "chat",
        "message": "你好",
        "session_id": "sess-1",
    })
    # 等 react_task 完成
    await loop.wait_for_react_idle(timeout=2.0)

    assert any(m.get("type") == "final" for m in sent), f"no final event in {sent}"
    assert loop._react_task is None or loop._react_task.done()


@pytest.mark.asyncio
async def test_chat_message_without_type_treated_as_chat():
    """无 type 字段 → 当 chat 处理 (向后兼容)."""
    deps, _ = _make_deps()
    deps.capability.llm_provider = MockLLMProvider(default_response="答复")

    sent: list[dict] = []
    loop = AgentLoop(
        deps=deps,
        event_log=EventLog(case_id=CaseId(value="test")),
        send_json=_make_send_json(sent),
    )
    await loop.start()

    await loop.put_message({
        "message": "你好",
        "session_id": "sess-1",
    })
    await loop.wait_for_react_idle(timeout=2.0)

    assert any(m.get("type") == "final" for m in sent)


@pytest.mark.asyncio
async def test_feedback_message_writes_memory_and_queues():
    """type=feedback → Memory.write + feedback_queue.put + 发 feedback_received."""
    deps, repo = _make_deps()
    loop = AgentLoop(
        deps=deps,
        event_log=EventLog(case_id=CaseId(value="test")),
        send_json=_noop_send_json,
    )
    await loop.start()

    await loop.put_message({
        "type": "feedback",
        "correction": "缺陷位置标错了",
        "session_id": "sess-1",
        "category": "wrong_result",
    })
    # 给 receive_task 时间处理
    await asyncio.sleep(0.1)

    # Memory.write 被调用 — repo 里有 1 条记录
    assert len(repo._store) == 1, f"expected 1 memory record, got {len(repo._store)}"
    record = next(iter(repo._store.values()))
    assert record.memory_type.value == "OPERATOR_FEEDBACK"
    assert "缺陷位置标错了" in record.content.summary

    # feedback_queue 有 1 条 (待下一轮 ReAct 排空)
    assert not loop._feedback_queue.empty()

    await loop.stop()


@pytest.mark.asyncio
async def test_feedback_message_sends_received_ack():
    """feedback → send_json({type:feedback_received, memory_id:...})."""
    deps, _ = _make_deps()
    sent: list[dict] = []
    loop = AgentLoop(
        deps=deps,
        event_log=EventLog(case_id=CaseId(value="test")),
        send_json=_make_send_json(sent),
    )
    await loop.start()

    await loop.put_message({
        "type": "feedback",
        "correction": "修正",
        "session_id": "sess-1",
    })
    await asyncio.sleep(0.1)

    acks = [m for m in sent if m.get("type") == "feedback_received"]
    assert len(acks) == 1, f"expected 1 ack, got {sent}"
    assert "memory_id" in acks[0]

    await loop.stop()


@pytest.mark.asyncio
async def test_feedback_message_rejected_when_memory_write_fails():
    """Memory.write 失败 → 发 feedback_rejected, 不崩 AgentLoop."""
    deps, _ = _make_deps()
    # 替换 write port 为总是失败的 stub
    class FailingWrite:
        async def write(self, input_data):
            raise RuntimeError("memory down")
    deps.memory.write = FailingWrite()  # type: ignore[assignment]

    sent: list[dict] = []
    loop = AgentLoop(
        deps=deps,
        event_log=EventLog(case_id=CaseId(value="test")),
        send_json=_make_send_json(sent),
    )
    await loop.start()

    await loop.put_message({
        "type": "feedback",
        "correction": "修正",
        "session_id": "sess-1",
    })
    await asyncio.sleep(0.1)

    rejected = [m for m in sent if m.get("type") == "feedback_rejected"]
    assert len(rejected) == 1
    assert "memory down" in rejected[0].get("reason", "")

    await loop.stop()
