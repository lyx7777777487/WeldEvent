# Phase 3 子项目 D — AgentLoop + WebSocket 双向通信 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 WebSocket 双向通信 (3.1) + AgentLoop 双 task 架构 (3.2) + 反馈闭环认知平面侧 (3.5), 让用户在 ReAct 跑的过程中能发 feedback / interrupt, feedback 持久化到 Memory 并在下一轮 ReAct 通过 §5.1 worldview injection 注入 system prompt。

**Architecture:** 新建 `AgentLoop` 类包 `ReActEngine` (零侵入 ReActEngine.run())。WebSocket handler 改调 AgentLoop, AgentLoop 启动 receive_task (feedback consumer) + react_task (主循环) 双 task。feedback 通过 asyncio.Queue (即时信号) + Memory (持久化) 双通道; 实际注入 LLM 由 §5.1 `_fetch_memory_hits` (现有) 完成。interrupt 用 asyncio.CancelledError 取消整个 run()。

**Tech Stack:** Python 3.11 asyncio, FastAPI WebSocket, pydantic BaseModel, 现有 ReActEngine / MemoryWritePort / EventLog。

**Spec:** `docs/superpowers/specs/2026-06-25-phase3-d-agentloop-websocket-design.md`

---

## File Structure

| 文件 | 改动 | 职责 |
|---|---|---|
| `cognitiveplane/control/agent_loop.py` | 新建 | AgentLoop 类 + FeedbackMessage / InterruptMessage schema + FeedbackSummary |
| `cognitiveplane/interaction/api/chat.py` | 改 `websocket_chat` (line 163-205) | 改调 AgentLoop, 不直接调 ReActEngine |
| `cognitiveplane/tests/test_control/test_agent_loop.py` | 新建 | AgentLoop 单元测试 (双 task 生命周期 / feedback 写 Memory / interrupt cancel / queue drain) |
| `cognitiveplane/tests/test_interaction/test_websocket_feedback.py` | 新建 | WebSocket E2E (feedback → Memory.write → 下一轮 ReAct system prompt 含 correction; interrupt 取消) |

不改: `control/react.py` (ReActEngine 零侵入) / `shared/ports/memory.py` / `control/event_log.py` / `memory/adapters/port_adapters.py`。

---

## Task 1: FeedbackMessage + InterruptMessage schema

**Files:**
- Create: `cognitiveplane/control/agent_loop.py`
- Test: `cognitiveplane/tests/test_control/test_agent_loop.py`

- [ ] **Step 1: 写失败测试 — schema 实例化 + type 字段**

Create `cognitiveplane/tests/test_control/test_agent_loop.py`:

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd cognitiveplane && python -m pytest tests/test_control/test_agent_loop.py -v`
Expected: FAIL with `ImportError: cannot import name 'FeedbackMessage' from 'cognitiveplane.control.agent_loop'`

- [ ] **Step 3: 写最小实现 — schema 定义**

Create `cognitiveplane/control/agent_loop.py`:

```python
"""AgentLoop — Phase 3 子项目 D 双 task + WebSocket 双向.

Spec: docs/superpowers/specs/2026-06-25-phase3-d-agentloop-websocket-design.md

职责:
  - 管理 receive_task (feedback consumer) + react_task (主循环) 生命周期
  - receive_task 收 WebSocket 消息 → 分发 chat/feedback/interrupt
  - react_task 跑 ReActEngine.run() (一次性, 不打断)
  - feedback → Memory.write + asyncio.Queue (即时信号)
  - interrupt → react_task.cancel()
  - 下一轮 ReAct 通过 §5.1 _fetch_memory_hits (现有) 读到 correction

零侵入: 不改 ReActEngine.run() 接口。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FeedbackMessage(BaseModel):
    """用户反馈消息 — 针对当前 ReAct 的修正."""

    type: Literal["feedback"] = "feedback"
    correction: str = Field(min_length=1)
    session_id: str
    target_tool_call_id: str | None = None
    category: Literal[
        "wrong_result", "wrong_tool", "missing_info", "other"
    ] = "other"


class InterruptMessage(BaseModel):
    """用户中断消息 — 取消当前 ReAct run()."""

    type: Literal["interrupt"] = "interrupt"
    session_id: str
    reason: str | None = None


class FeedbackSummary(BaseModel):
    """Feedback 的轻量摘要 — 用于 asyncio.Queue 传递 (不传完整 BaseModel)."""

    correction: str
    memory_id: str
    category: str
    target_tool_call_id: str | None = None

    @classmethod
    def from_message(cls, msg: FeedbackMessage, memory_id: str) -> "FeedbackSummary":
        return cls(
            correction=msg.correction,
            memory_id=memory_id,
            category=msg.category,
            target_tool_call_id=msg.target_tool_call_id,
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd cognitiveplane && python -m pytest tests/test_control/test_agent_loop.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add cognitiveplane/control/agent_loop.py cognitiveplane/tests/test_control/test_agent_loop.py
git commit -m "feat(agent-loop): add Feedback/Interrupt message schemas"
```

---

## Task 2: AgentLoop skeleton + 生命周期管理

**Files:**
- Modify: `cognitiveplane/control/agent_loop.py`
- Test: `cognitiveplane/tests/test_control/test_agent_loop.py`

- [ ] **Step 1: 写失败测试 — AgentLoop 构造 + start/stop**

Append to `cognitiveplane/tests/test_control/test_agent_loop.py`:

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd cognitiveplane && python -m pytest tests/test_control/test_agent_loop.py -v`
Expected: FAIL with `ImportError: cannot import name 'AgentLoop'`

- [ ] **Step 3: 实现 AgentLoop skeleton**

Append to `cognitiveplane/control/agent_loop.py`:

```python
import asyncio
import logging
from typing import Any, Awaitable, Callable

from cognitiveplane.control.deps import CognitiveDependencies
from cognitiveplane.control.event_log import BrainEventType, EventLog

logger = logging.getLogger(__name__)

SendJsonFn = Callable[[dict[str, Any]], Awaitable[None]]


class AgentLoop:
    """管理 receive_task + react_task 双 task 生命周期.

    使用:
        loop = AgentLoop(deps=..., event_log=..., send_json=ws.send_json)
        await loop.start()
        # ... connection lifetime ...
        await loop.stop()

    receive_task 循环 await loop._drain_receive() — 子类/测试可注入消息源.
    默认实现从 self._incoming: asyncio.Queue 取消息 (生产者由 WebSocket handler put).
    """

    def __init__(
        self,
        deps: CognitiveDependencies,
        event_log: EventLog,
        send_json: SendJsonFn,
    ) -> None:
        self._deps = deps
        self._event_log = event_log
        self._send_json = send_json
        self._incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._feedback_queue: asyncio.Queue[FeedbackSummary] = asyncio.Queue(maxsize=10)
        self._receive_task: asyncio.Task | None = None
        self._react_task: asyncio.Task | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """启动 receive_task. react_task 按需启动 (收到 chat 消息时)."""
        if self._running:
            return
        self._running = True
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def stop(self) -> None:
        """停止 receive_task + 取消 react_task. Idempotent."""
        if not self._running:
            return
        self._running = False
        if self._react_task is not None and not self._react_task.done():
            self._react_task.cancel()
        if self._receive_task is not None and not self._receive_task.done():
            self._receive_task.cancel()
        # await tasks to clean up CancelledError
        for task in (self._react_task, self._receive_task):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._react_task = None
        self._receive_task = None

    async def put_message(self, message: dict[str, Any]) -> None:
        """WebSocket handler 调此方法 put 消息 — receive_loop 消费."""
        await self._incoming.put(message)

    async def _receive_loop(self) -> None:
        """receive_task 主体 — 循环取消息分发."""
        try:
            while self._running:
                message = await self._incoming.get()
                try:
                    await self._dispatch(message)
                except Exception as e:
                    logger.warning("AgentLoop dispatch error: %s", e)
                    await self._send_json({"type": "error", "error": f"dispatch: {e}"})
        except asyncio.CancelledError:
            pass

    async def _dispatch(self, message: dict[str, Any]) -> None:
        """根据 type 字段分发. Task 3/4/5 实现."""
        raise NotImplementedError("dispatch implemented in Task 3-5")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd cognitiveplane && python -m pytest tests/test_control/test_agent_loop.py -v`
Expected: 6 passed (4 from Task 1 + 2 new)

- [ ] **Step 5: Commit**

```bash
git add cognitiveplane/control/agent_loop.py cognitiveplane/tests/test_control/test_agent_loop.py
git commit -m "feat(agent-loop): skeleton + lifecycle (start/stop receive_task)"
```

---

## Task 3: handle_chat — 启动 react_task 跑 ReActEngine

**Files:**
- Modify: `cognitiveplane/control/agent_loop.py`
- Test: `cognitiveplane/tests/test_control/test_agent_loop.py`

- [ ] **Step 1: 写失败测试 — chat 消息触发 react_task**

Append to `cognitiveplane/tests/test_control/test_agent_loop.py`:

```python
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
        send_json=lambda data: sent.append(data),
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
        send_json=lambda data: sent.append(data),
    )
    await loop.start()

    await loop.put_message({
        "message": "你好",
        "session_id": "sess-1",
    })
    await loop.wait_for_react_idle(timeout=2.0)

    assert any(m.get("type") == "final" for m in sent)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd cognitiveplane && python -m pytest tests/test_control/test_agent_loop.py::test_chat_message_starts_react_task -v`
Expected: FAIL with `NotImplementedError: dispatch implemented in Task 3-5` 或 `AttributeError: 'AgentLoop' object has no attribute 'wait_for_react_idle'`

- [ ] **Step 3: 实现 _dispatch + handle_chat + wait_for_react_idle**

Edit `cognitiveplane/control/agent_loop.py` — 替换 `_dispatch` 的 NotImplementedError stub 并添加方法:

```python
    async def _dispatch(self, message: dict[str, Any]) -> None:
        """根据 type 字段分发 chat/feedback/interrupt."""
        msg_type = message.get("type", "chat")
        if msg_type == "chat":
            await self._handle_chat(message)
        elif msg_type == "feedback":
            await self._handle_feedback(message)
        elif msg_type == "interrupt":
            await self._handle_interrupt(message)
        else:
            await self._send_json({
                "type": "error",
                "error": f"unknown message type: {msg_type}",
            })

    async def _handle_chat(self, message: dict[str, Any]) -> None:
        """启动 react_task 跑 ReActEngine.run(). 同一时刻只有一个 react_task."""
        # 等上一个 react_task 结束
        if self._react_task is not None and not self._react_task.done():
            await self._react_task
        self._react_task = asyncio.create_task(self._run_react(message))

    async def _run_react(self, message: dict[str, Any]) -> None:
        """react_task 主体 — 构造 ReActEngine + 跑 run() + 推 final 事件."""
        from cognitiveplane.control.react import ReActEngine
        from cognitiveplane.control.hooks import SafetyHook
        from cognitiveplane.interaction.session import SessionManager
        from cognitiveplane.shared.dto_context import ContextSnapshot
        from cognitiveplane.shared.enums import EventType, NoveltyLevel
        from cognitiveplane.shared.types import CaseId
        from datetime import datetime, timezone

        try:
            engine = ReActEngine(
                self._deps,
                hooks=[SafetyHook()],
                event_callback=self._on_react_event,
                event_log=self._event_log,
            )
            context = ContextSnapshot(
                case_id=CaseId(value=message.get("case_id") or "unknown"),
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
            # 排空 feedback_queue — 下一轮 ReAct 前记录待处理 feedback (§5.1 会从 Memory 读)
            await self._drain_feedback_queue()

            response = await engine.run(
                user_input=message.get("message", ""),
                context=context,
                session={"session_id": message.get("session_id", "default")},
            )
            await self._send_json({
                "type": "final",
                "reply": response.text_reply,
                "session_id": message.get("session_id", "default"),
                "tools_used": response.tools_used,
                "tier": response.tier_used.value,
                "error": response.error,
            })
        except asyncio.CancelledError:
            # interrupt 触发的 cancel — 不发 final, 由 _handle_interrupt 发 interrupted
            raise
        except Exception as e:
            logger.exception("react_task failed")
            await self._send_json({"type": "error", "error": str(e)})

    async def _on_react_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """ReActEngine event_callback → 转发到 WebSocket."""
        await self._send_json({"type": event_type, **payload})

    async def _drain_feedback_queue(self) -> None:
        """react_task 开始前排空 feedback_queue — 记 EventLog (实际注入由 §5.1 完成)."""
        pending: list[FeedbackSummary] = []
        while not self._feedback_queue.empty():
            try:
                pending.append(self._feedback_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if pending and self._event_log is not None:
            self._event_log.emit(
                BrainEventType.TOOL_RESULT,
                source="agent_loop",
                data={
                    "event": "feedback_pending_before_react",
                    "count": len(pending),
                    "memory_ids": [p.memory_id for p in pending],
                },
            )

    async def wait_for_react_idle(self, timeout: float = 5.0) -> None:
        """测试用 — 等 react_task 完成."""
        if self._react_task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._react_task), timeout=timeout)
        except asyncio.TimeoutError:
            raise
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd cognitiveplane && python -m pytest tests/test_control/test_agent_loop.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add cognitiveplane/control/agent_loop.py cognitiveplane/tests/test_control/test_agent_loop.py
git commit -m "feat(agent-loop): handle_chat starts react_task, forward ReAct events"
```

---

## Task 4: handle_feedback — Memory.write + queue

**Files:**
- Modify: `cognitiveplane/control/agent_loop.py`
- Test: `cognitiveplane/tests/test_control/test_agent_loop.py`

- [ ] **Step 1: 写失败测试 — feedback 写 Memory + 推 queue**

Append to `cognitiveplane/tests/test_control/test_agent_loop.py`:

```python
@pytest.mark.asyncio
async def test_feedback_message_writes_memory_and_queues():
    """type=feedback → Memory.write + feedback_queue.put + 发 feedback_received."""
    deps, repo = _make_deps()
    loop = AgentLoop(
        deps=deps,
        event_log=EventLog(case_id=CaseId(value="test")),
        send_json=lambda data: None,
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

    # 收到 feedback_received 回执
    # (此测试 send_json 是 no-op, 用单独测试验证回执)

    await loop.stop()


@pytest.mark.asyncio
async def test_feedback_message_sends_received_ack():
    """feedback → send_json({type:feedback_received, memory_id:...})."""
    deps, _ = _make_deps()
    sent: list[dict] = []
    loop = AgentLoop(
        deps=deps,
        event_log=EventLog(case_id=CaseId(value="test")),
        send_json=lambda data: sent.append(data),
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
        send_json=lambda data: sent.append(data),
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd cognitiveplane && python -m pytest tests/test_control/test_agent_loop.py -v -k feedback`
Expected: 3 FAIL (feedback 未实现)

- [ ] **Step 3: 实现 _handle_feedback**

Edit `cognitiveplane/control/agent_loop.py` — 在 `_handle_chat` 后加:

```python
    async def _handle_feedback(self, message: dict[str, Any]) -> None:
        """feedback → Memory.write + feedback_queue.put + 发 feedback_received."""
        try:
            msg = FeedbackMessage(**message)
        except Exception as e:
            await self._send_json({
                "type": "feedback_rejected",
                "reason": f"invalid feedback message: {e}",
            })
            return

        memory_id_str = await self._persist_feedback(msg)
        if memory_id_str is None:
            return  # _persist_feedback 已发 feedback_rejected

        summary = FeedbackSummary.from_message(msg, memory_id=memory_id_str)
        try:
            self._feedback_queue.put_nowait(summary)
        except asyncio.QueueFull:
            # 丢最旧 — EventLog 记 overflow
            try:
                self._feedback_queue.get_nowait()
                self._feedback_queue.put_nowait(summary)
            except asyncio.QueueEmpty:
                pass
            if self._event_log is not None:
                self._event_log.emit(
                    BrainEventType.TOOL_RESULT,
                    source="agent_loop",
                    data={"event": "feedback_queue_overflow"},
                )

        await self._send_json({
            "type": "feedback_received",
            "memory_id": memory_id_str,
            "category": msg.category,
        })

    async def _persist_feedback(self, msg: FeedbackMessage) -> str | None:
        """写 Memory. 失败返回 None + 发 feedback_rejected."""
        from cognitiveplane.shared.dto_memory import MemoryContent
        from cognitiveplane.shared.enums import MemoryType, PromotionStatus
        from cognitiveplane.shared.ports.memory import MemoryWriteInput
        from cognitiveplane.shared.types import DecisionId
        from uuid import uuid4

        write_port = self._deps.memory.write
        if write_port is None:
            await self._send_json({
                "type": "feedback_rejected",
                "reason": "memory write port unavailable",
            })
            return None

        try:
            content = MemoryContent(
                summary=msg.correction,
                details={
                    "category": msg.category,
                    "target_tool_call_id": msg.target_tool_call_id,
                    "session_id": msg.session_id,
                    "source": "human_feedback",
                },
                feature_vector=[0.0],  # Phase 2: in-memory stub ignores vector
            )
            write_input = MemoryWriteInput(
                memory_type=MemoryType.OPERATOR_FEEDBACK,
                content=content,
                source_decision_id=DecisionId(value=uuid4()),
            )
            # MemoryWriteAdapter 默认 promotion_status=VALIDATED — 下一轮 §5.1 能搜到
            output = await write_port.write(write_input)
            return str(output.memory_id.value)
        except Exception as e:
            logger.exception("feedback persist failed")
            await self._send_json({
                "type": "feedback_rejected",
                "reason": str(e),
            })
            return None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd cognitiveplane && python -m pytest tests/test_control/test_agent_loop.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add cognitiveplane/control/agent_loop.py cognitiveplane/tests/test_control/test_agent_loop.py
git commit -m "feat(agent-loop): handle_feedback writes Memory + queues summary"
```

---

## Task 5: handle_interrupt — cancel react_task

**Files:**
- Modify: `cognitiveplane/control/agent_loop.py`
- Test: `cognitiveplane/tests/test_control/test_agent_loop.py`

- [ ] **Step 1: 写失败测试 — interrupt 取消 react_task**

Append to `cognitiveplane/tests/test_control/test_agent_loop.py`:

```python
@pytest.mark.asyncio
async def test_interrupt_message_cancels_react_task():
    """type=interrupt → react_task.cancel() + 发 interrupted."""
    deps, _ = _make_deps()

    # 用一个慢 LLM — 让 react_task 跑的过程中能被 cancel
    class SlowLLM(MockLLMProvider):
        @property
        def supports_function_calling(self) -> bool:
            return False
        @property
        def supports_json_mode(self) -> bool:
            return False
        async def complete(self, request):
            await asyncio.sleep(2.0)  # 慢, 给 interrupt 时间
            return await super().complete(request)
    deps.capability.llm_provider = SlowLLM()

    sent: list[dict] = []
    loop = AgentLoop(
        deps=deps,
        event_log=EventLog(case_id=CaseId(value="test")),
        send_json=lambda data: sent.append(data),
    )
    await loop.start()

    # 启动 chat
    await loop.put_message({
        "type": "chat",
        "message": "你好",
        "session_id": "sess-1",
    })
    await asyncio.sleep(0.1)  # 让 react_task 启动

    # 发 interrupt
    await loop.put_message({
        "type": "interrupt",
        "session_id": "sess-1",
        "reason": "停, 我看错了",
    })
    await asyncio.sleep(0.2)  # 让 cancel 生效

    interrupted = [m for m in sent if m.get("type") == "interrupted"]
    assert len(interrupted) == 1, f"expected 1 interrupted, got {sent}"
    assert interrupted[0]["reason"] == "停, 我看错了"

    # react_task 已取消
    assert loop._react_task is None or loop._react_task.done()

    await loop.stop()


@pytest.mark.asyncio
async def test_interrupt_without_active_react_task_is_noop():
    """无 react_task 时 interrupt → 发 interrupted 但不崩."""
    deps, _ = _make_deps()
    sent: list[dict] = []
    loop = AgentLoop(
        deps=deps,
        event_log=EventLog(case_id=CaseId(value="test")),
        send_json=lambda data: sent.append(data),
    )
    await loop.start()

    await loop.put_message({
        "type": "interrupt",
        "session_id": "sess-1",
    })
    await asyncio.sleep(0.1)

    # 无 react_task → 不发 interrupted (无东西可中断)
    assert not any(m.get("type") == "interrupted" for m in sent)

    await loop.stop()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd cognitiveplane && python -m pytest tests/test_control/test_agent_loop.py -v -k interrupt`
Expected: 2 FAIL (interrupt 未实现)

- [ ] **Step 3: 实现 _handle_interrupt**

Edit `cognitiveplane/control/agent_loop.py` — 在 `_handle_feedback` 后加:

```python
    async def _handle_interrupt(self, message: dict[str, Any]) -> None:
        """interrupt → 取消 react_task + 发 interrupted."""
        try:
            msg = InterruptMessage(**message)
        except Exception as e:
            await self._send_json({
                "type": "error",
                "error": f"invalid interrupt message: {e}",
            })
            return

        if self._react_task is None or self._react_task.done():
            # 无活跃 react_task — 静默 (测试 test_interrupt_without_active_react_task_is_noop)
            return

        self._react_task.cancel()
        try:
            await self._react_task
        except asyncio.CancelledError:
            pass
        self._react_task = None

        await self._send_json({
            "type": "interrupted",
            "reason": msg.reason or "user requested",
        })
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd cognitiveplane && python -m pytest tests/test_control/test_agent_loop.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add cognitiveplane/control/agent_loop.py cognitiveplane/tests/test_control/test_agent_loop.py
git commit -m "feat(agent-loop): handle_interrupt cancels react_task"
```

---

## Task 6: WebSocket handler 改调 AgentLoop

**Files:**
- Modify: `cognitiveplane/interaction/api/chat.py:163-205`
- Test: `cognitiveplane/tests/test_interaction/test_websocket_feedback.py` (新建)

- [ ] **Step 1: 写 E2E 失败测试 — WebSocket 双向通**

Create `cognitiveplane/tests/test_interaction/test_websocket_feedback.py`:

```python
"""E2E test — WebSocket 双向 + feedback + interrupt.

Spec §11.4 验收 3.1 + 3.2 + 3.5 (认知平面侧).
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from cognitiveplane.app import create_app
from cognitiveplane.app import _build_dependencies


@pytest.fixture
def app_with_mock_llm(monkeypatch):
    """构造带 MockLLMProvider 的 app — 不依赖真实 DeepSeek/Volc."""
    # 直接用 _build_dependencies 但替换 llm_provider
    # 通过 monkeypatch cognitiveplane.app.get_llm 返回 MockLLMProvider
    from cognitiveplane.capability.mock import MockLLMProvider
    import cognitiveplane.app as app_module
    monkeypatch.setattr(app_module, "get_llm", lambda: MockLLMProvider(default_response="测试答复"))
    app = create_app()
    return app


def test_websocket_chat_streams_events(app_with_mock_llm):
    """3.1 server→client — chat 消息收到 thinking/final 流式事件."""
    client = TestClient(app_with_mock_llm)
    with client.websocket_connect("/api/v1/chat/ws") as ws:
        ws.send_json({
            "type": "chat",
            "message": "你好",
            "session_id": "sess-e2e",
        })
        # 收消息直到 final
        events: list[dict] = []
        for _ in range(20):
            data = ws.receive_json()
            events.append(data)
            if data.get("type") == "final":
                break
        assert any(e.get("type") == "final" for e in events), f"no final in {events}"


def test_websocket_feedback_writes_memory(app_with_mock_llm):
    """3.5 feedback → feedback_received 回执."""
    client = TestClient(app_with_mock_llm)
    with client.websocket_connect("/api/v1/chat/ws") as ws:
        ws.send_json({
            "type": "feedback",
            "correction": "缺陷位置标错了",
            "session_id": "sess-e2e",
            "category": "wrong_result",
        })
        events: list[dict] = []
        for _ in range(10):
            data = ws.receive_json()
            events.append(data)
            if data.get("type") == "feedback_received":
                break
        acks = [e for e in events if e.get("type") == "feedback_received"]
        assert len(acks) == 1, f"expected feedback_received, got {events}"
        assert "memory_id" in acks[0]


def test_websocket_interrupt_cancels_react(app_with_mock_llm, monkeypatch):
    """3.1 interrupt → interrupted 回执."""
    # 用慢 LLM 让 react_task 可被中断
    from cognitiveplane.capability.mock import MockLLMProvider
    import cognitiveplane.app as app_module

    class SlowLLM(MockLLMProvider):
        @property
        def supports_function_calling(self) -> bool:
            return False
        @property
        def supports_json_mode(self) -> bool:
            return False
        async def complete(self, request):
            await asyncio.sleep(2.0)
            return await super().complete(request)

    monkeypatch.setattr(app_module, "get_llm", lambda: SlowLLM())
    app = create_app()
    client = TestClient(app)

    with client.websocket_connect("/api/v1/chat/ws") as ws:
        ws.send_json({"type": "chat", "message": "你好", "session_id": "sess-e2e"})
        # 等一会儿让 react_task 启动
        import time
        time.sleep(0.2)
        ws.send_json({"type": "interrupt", "session_id": "sess-e2e", "reason": "停"})

        events: list[dict] = []
        for _ in range(20):
            data = ws.receive_json()
            events.append(data)
            if data.get("type") == "interrupted":
                break
        interrupted = [e for e in events if e.get("type") == "interrupted"]
        assert len(interrupted) == 1, f"expected interrupted, got {events}"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd cognitiveplane && python -m pytest tests/test_interaction/test_websocket_feedback.py -v`
Expected: FAIL — 当前 WebSocket handler 不识别 type 字段, 当 chat 处理 feedback/interrupt

- [ ] **Step 3: 改 WebSocket handler 用 AgentLoop**

Edit `cognitiveplane/interaction/api/chat.py` — 替换 `websocket_chat` 函数 (line 163-205):

```python
    @router.websocket("/ws")
    async def websocket_chat(websocket: WebSocket) -> None:
        await websocket.accept()

        from cognitiveplane.control.agent_loop import AgentLoop
        from cognitiveplane.control.event_log import EventLog
        from cognitiveplane.shared.types import CaseId

        event_log = EventLog(case_id=CaseId(value="app-shared"))

        async def send_json(data: dict) -> None:
            await websocket.send_json(data)

        loop = AgentLoop(
            deps=deps,
            event_log=event_log,
            send_json=send_json,
        )
        await loop.start()

        try:
            while True:
                data = await websocket.receive_json()
                await loop.put_message(data)
        except WebSocketDisconnect:
            pass
        finally:
            await loop.stop()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd cognitiveplane && python -m pytest tests/test_interaction/test_websocket_feedback.py -v`
Expected: 3 passed

如失败, 常见原因:
- `create_app` 签名变化 → 检查 `cognitiveplane/app.py` 的 `create_app()` 是否需要参数
- `get_llm` 不是 module 级函数 → 用实际 llm 获取入口
- WebSocket 路径不是 `/api/v1/chat/ws` → 检查 router prefix

- [ ] **Step 5: Commit**

```bash
git add cognitiveplane/interaction/api/chat.py cognitiveplane/tests/test_interaction/test_websocket_feedback.py
git commit -m "feat(interaction): WebSocket handler uses AgentLoop for bidirectional"
```

---

## Task 7: 反馈闭环 E2E — feedback → 下一轮 ReAct system prompt 含 correction

**Files:**
- Test: `cognitiveplane/tests/test_interaction/test_websocket_feedback.py`

- [ ] **Step 1: 写失败测试 — feedback 注入下一轮 ReAct**

Append to `cognitiveplane/tests/test_interaction/test_websocket_feedback.py`:

```python
def test_feedback_injected_into_next_react_system_prompt(app_with_mock_llm):
    """3.5 — feedback → Memory.write → 下一轮 ReAct 的 §5.1 worldview injection 命中 correction.

    验收 §11.4 3.5: 用户改 1 张图的标注 → ... → 下一轮 ReAct LLM 能感知.
    本测试只验证认知平面侧 — correction 文本能被 Memory.search 搜到并注入 system prompt.
    """
    from cognitiveplane.capability.mock import MockLLMProvider
    import cognitiveplane.app as app_module

    # 用一个能记录 messages 的 LLM — 验证 system prompt 含 correction
    captured_messages: list[list[dict]] = []

    class CapturingLLM(MockLLMProvider):
        @property
        def supports_function_calling(self) -> bool:
            return False
        @property
        def supports_json_mode(self) -> bool:
            return False
        async def complete(self, request):
            captured_messages.append(request.messages)
            return await super().complete(request)

    app_with_mock_llm.dependency_overrides = {}  # reset
    monkeypatch_target = app_module.get_llm
    # 直接重建 app 用 CapturingLLM
    import cognitiveplane.app as app_module2
    original_get_llm = app_module2.get_llm
    app_module2.get_llm = lambda: CapturingLLM(default_response="答复")
    try:
        app = create_app()
        client = TestClient(app)

        with client.websocket_connect("/api/v1/chat/ws") as ws:
            # 1. 先发 feedback
            ws.send_json({
                "type": "feedback",
                "correction": "焊缝根部气孔位置标错了, 应该在 2 号位置",
                "session_id": "sess-closed-loop",
                "category": "wrong_result",
            })
            for _ in range(10):
                if ws.receive_json().get("type") == "feedback_received":
                    break

            # 2. 再发 chat — 下一轮 ReAct 应通过 §5.1 读到 correction
            ws.send_json({
                "type": "chat",
                "message": "再看一下这张图",
                "session_id": "sess-closed-loop",
            })
            for _ in range(20):
                data = ws.receive_json()
                if data.get("type") == "final":
                    break
    finally:
        app_module2.get_llm = original_get_llm

    # 验证 — 第二轮 ReAct 的 system prompt 含 correction
    assert len(captured_messages) >= 1, f"LLM not called: {captured_messages}"
    # 找最后一个 ReAct 调用的 system prompt (messages[0] 是 system)
    last_messages = captured_messages[-1]
    system_msg = next((m for m in last_messages if m.get("role") == "system"), None)
    assert system_msg is not None, f"no system message in {last_messages}"
    assert "焊缝根部气孔位置标错了" in system_msg["content"], (
        f"correction not injected into system prompt: {system_msg['content'][:500]}"
    )
```

- [ ] **Step 2: 运行测试确认失败/通过**

Run: `cd cognitiveplane && python -m pytest tests/test_interaction/test_websocket_feedback.py::test_feedback_injected_into_next_react_system_prompt -v`

Expected: 可能 PASS (§5.1 已实现) — 因为 feedback 写 Memory with VALIDATED status, InMemoryMemoryRepository.search 默认 status_filter=[PROMOTED, VALIDATED] 会命中. 如果 PASS, 跳到 Step 4.

如 FAIL, 常见原因:
- `_fetch_memory_hits` 的 `min_confidence=0.5` 过滤掉了 — InMemoryMemoryRepository.search 返回 similarity_score=1.0, 不过滤
- §5.1 `_build_system_prompt` 异常吞掉了 → 检查 `control/react.py:753-800`
- Memory.write 的 promotion_status 不是 VALIDATED → 检查 `MemoryWriteAdapter` (默认 VALIDATED)

- [ ] **Step 3: 如失败, 调试修复**

如测试失败, 在 `cognitiveplane/control/react.py` 的 `_fetch_memory_hits` (line 800) 加临时 print 看 output.results 是否为空. 常见根因:

a) feedback 写入时 `source_decision_id` 是随机 uuid4, 但 `_fetch_memory_hits` 用 `case_features={"case_id": context.case_id.value}` 查 — InMemoryMemoryRepository.search 不按 case_features 过滤 (只按 status_filter), 所以不匹配 case_id 也能搜到. 应该 PASS.

b) 如果 §5.1 worldview section 因任何异常被吞 (line 827 `except Exception: return None, []`), 修正 — 但不要改 §5.1 逻辑, 而是确保 feedback 写入的 MemoryContent 格式正确.

- [ ] **Step 4: 运行全量 WebSocket 测试确认通过**

Run: `cd cognitiveplane && python -m pytest tests/test_interaction/test_websocket_feedback.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add cognitiveplane/tests/test_interaction/test_websocket_feedback.py
git commit -m "test(interaction): 3.5 feedback closed-loop — correction injected into next ReAct"
```

---

## Task 8: 全量回归 + Phase discipline

**Files:**
- Run: full test suite
- Run: `scripts/check_phase_discipline.py`

- [ ] **Step 1: 跑全量 AgentLoop 测试**

Run: `cd cognitiveplane && python -m pytest tests/test_control/test_agent_loop.py -v`
Expected: 13 passed

- [ ] **Step 2: 跑全量 WebSocket 测试**

Run: `cd cognitiveplane && python -m pytest tests/test_interaction/test_websocket_feedback.py -v`
Expected: 4 passed

- [ ] **Step 3: 跑全量 cognitiveplane 测试确保无回归**

Run: `cd cognitiveplane && python -m pytest -x`
Expected: PASS — 全量测试通过 (805 + 17 新 = 822 左右), 无回归

- [ ] **Step 4: 跑 phase discipline 检查**

Run: `cd /Users/liuyixuan/PycharmProjects/WeldEvent && python scripts/check_phase_discipline.py`
Expected: PASS with warnings (全部为既有历史债, 无新增)

如失败, 检查 diff 是否混入:
- `switch_role` / `request_image_detail` — Phase 5+
- `agent_loop.py` 本身应标 Phase 3 — 确认 PHASE_MAP 已包含

- [ ] **Step 5: Commit (如有 phase discipline 修复)**

```bash
git add -A
git commit -m "test(agent-loop): D 子项目全量回归通过 — 3.1/3.2/3.5 验收完成"
```

---

## 验收清单

| 验收项 | 测试 | 状态 |
|---|---|---|
| 3.1 WebSocket 双向 (server→client) | test_websocket_chat_streams_events | ☐ |
| 3.1 WebSocket 双向 (client→server feedback) | test_websocket_feedback_writes_memory | ☐ |
| 3.1 WebSocket 双向 (client→server interrupt) | test_websocket_interrupt_cancels_react | ☐ |
| 3.2 AgentLoop 双 task | test_agentloop_construct_and_start + test_chat_message_starts_react_task | ☐ |
| 3.2 feedback_queue 通信 | test_feedback_message_writes_memory_and_queues + test_feedback_injected_into_next_react_system_prompt | ☐ |
| 3.2 react_task cancel | test_interrupt_message_cancels_react_task | ☐ |
| 3.5 反馈闭环 (认知平面侧) | test_feedback_injected_into_next_react_system_prompt | ☐ |
| 不引入禁用项 | phase discipline PASS | ☐ |
| 全量无回归 | pytest -x PASS | ☐ |

---

## 偏离 spec 说明

- **Spec 2.2 说 "asyncio.Queue (即时信号) + Memory (持久化) 双通道"**: 实现里 queue 的角色是 EventLog 观测信号 (Task 3 `_drain_feedback_queue` 记 `feedback_pending_before_react`), 实际 LLM 注入仍由 §5.1 Memory.search 完成. queue 不在 run() 内部检查 — 与 spec Q1 一致 ("不在 run() 内部检查").
- **Task 7 测试可能直接 PASS**: §5.1 已实现 + InMemoryMemoryRepository.search 默认匹配 VALIDATED 状态, feedback 写入即注入. 如直接 PASS 不需要改任何代码, 测试本身就是验收证据.
- **`wait_for_react_idle` 是测试专用方法**: 生产代码不调, 但放在 AgentLoop 上而非测试 helper 是因为它需要访问 private `_react_task`.
