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
        if self._react_task is not None and not self._react_task.done():
            await self._react_task
        self._react_task = asyncio.create_task(self._run_react(message))

    async def _run_react(self, message: dict[str, Any]) -> None:
        """react_task 主体 — 构造 ReActEngine + 跑 run() + 推 final 事件."""
        from cognitiveplane.control.react import ReActEngine
        from cognitiveplane.control.hooks import SafetyHook
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
        """测试用 — 等 react_task 被创建并完成."""
        deadline = asyncio.get_event_loop().time() + timeout
        while self._react_task is None:
            if asyncio.get_event_loop().time() >= deadline:
                raise asyncio.TimeoutError("react_task not started within timeout")
            await asyncio.sleep(0.01)
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError("react_task started but no time left to await")
        await asyncio.wait_for(asyncio.shield(self._react_task), timeout=remaining)

    async def _handle_feedback(self, message: dict[str, Any]) -> None:
        """Task 4 实现."""
        raise NotImplementedError

    async def _handle_interrupt(self, message: dict[str, Any]) -> None:
        """Task 5 实现."""
        raise NotImplementedError
