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
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from cognitiveplane.control.deps import CognitiveDependencies
from cognitiveplane.control.event_log import BrainEventType, EventLog
from cognitiveplane.control.hooks import BeforeToolHook, SafetyHook
from cognitiveplane.control.skills import SkillRegistry

if TYPE_CHECKING:
    from cognitiveplane.interaction.image_store import ImageStore
    from cognitiveplane.governance.guardrails import (
        AfterToolHook,
        OutputGuardrail,
    )
    from cognitiveplane.control.registry.tool_registry import ToolRegistry

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

    Governance: hooks 参数控制 ReActEngine 的 BeforeToolHook 链. 默认只有 SafetyHook,
    生产入口 (chat.py /ws) 必须传入 [SafetyHook(), PolicyHook(tool_policy)] 与 HTTP 路径
    保持治理一致 — 不能让同一工具调用因入口不同而绕过 ToolPolicy.
    """

    def __init__(
        self,
        deps: CognitiveDependencies,
        event_log: EventLog,
        send_json: SendJsonFn,
        hooks: list[BeforeToolHook] | None = None,
        skill_registry: SkillRegistry | None = None,
        image_store: "ImageStore | None" = None,
        after_tool_hooks: "list[AfterToolHook] | None" = None,
        output_guardrail: "OutputGuardrail | None" = None,
        stream_mode: bool = False,
        tool_registry: "ToolRegistry | None" = None,
        session_manager: Any = None,
        image_sessions: Any = None,
        router: Any = None,
    ) -> None:
        self._deps = deps
        self._event_log = event_log
        self._send_json = send_json
        self._skill_registry = skill_registry
        self._hooks: list[BeforeToolHook] = hooks if hooks is not None else [SafetyHook()]
        self._image_store = image_store
        self._after_tool_hooks = after_tool_hooks
        self._output_guardrail = output_guardrail
        self._stream_mode = stream_mode
        # 复用外部 ToolRegistry — 否则内部新建的 TR 不会有 Label Studio 标注工具
        # (list_datasets/create_job 等), LLM 调用时报 "not available in current phase".
        # app.py lifespan 只把标注工具注册到 chat_router.tool_registry.
        self._tool_registry = tool_registry
        # session 持久化 + 图片引用注入 — 与 HTTP /stream 路径保持一致。
        # 不传则每轮临时 session,LLM 跨轮失忆(忘记已上传图片/上文)。
        self._session_manager = session_manager
        self._image_sessions = image_sessions
        self._router = router
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
                    logger.debug("agent_loop cleanup error", exc_info=True)
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
            logger.debug("agent_loop cleanup error", exc_info=True)

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
        """启动 react_task 跑 ReActEngine. 同一时刻只有一个 react_task."""
        if self._react_task is not None and not self._react_task.done():
            await self._react_task
        if self._stream_mode:
            self._react_task = asyncio.create_task(self._run_react_stream(message))
        else:
            self._react_task = asyncio.create_task(self._run_react(message))

    async def _run_react(self, message: dict[str, Any]) -> None:
        """react_task 主体 — 构造 ReActEngine + 跑 run() + 推 final 事件."""
        from cognitiveplane.control.react import ReActEngine
        from cognitiveplane.shared.dto.context import ContextSnapshot
        from cognitiveplane.shared.enums import EventType, NoveltyLevel
        from cognitiveplane.shared.types import CaseId
        from datetime import datetime, timezone

        try:
            engine = ReActEngine(
                self._deps,
                hooks=self._hooks,
                event_callback=self._on_react_event,
                event_log=self._event_log,
                skill_registry=self._skill_registry,
                image_store=self._image_store,
                after_tool_hooks=self._after_tool_hooks,
                output_guardrail=self._output_guardrail,
                tool_registry=self._tool_registry,
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

    async def _run_react_stream(self, message: dict[str, Any]) -> None:
        """react_task 主体 (stream 模式) — 构造 ReActEngine + run_stream() + 逐事件推送."""
        from cognitiveplane.control.react import ReActEngine
        from cognitiveplane.shared.dto.context import ContextSnapshot
        from cognitiveplane.shared.enums import EventType, NoveltyLevel
        from cognitiveplane.shared.types import CaseId
        from datetime import datetime, timezone

        session_id = message.get("session_id", "default")
        # 与 HTTP /stream 路径对齐: 注入 session image_refs + 持久化 history。
        # 不做这步则 LLM 跨轮失忆 — 忘记已上传图片、上文决策。
        user_msg, session_history = self._prepare_session_context(message, session_id)

        try:
            engine = ReActEngine(
                self._deps,
                hooks=self._hooks,
                event_callback=self._on_react_event,
                event_log=self._event_log,
                skill_registry=self._skill_registry,
                image_store=self._image_store,
                after_tool_hooks=self._after_tool_hooks,
                output_guardrail=self._output_guardrail,
                tool_registry=self._tool_registry,
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

            final_reply = ""
            final_reasoning: str | None = None
            tools_used_list: list[str] = []
            workflow_ids_list: list[str] = []
            async for event in engine.run_stream(
                user_input=user_msg,
                context=context,
                session={"session_id": session_id, "history": session_history},
            ):
                # 捕获 final 事件用于持久化(不修改原 event)
                if event.get("event") == "final":
                    data = event.get("data", {})
                    final_reply = data.get("reply", "")
                    final_reasoning = data.get("reasoning_content")
                    tools_used_list = data.get("tools_used", [])
                    workflow_ids_list = data.get("workflow_ids", [])
                await self._send_json(event)
            # 流结束后持久化到 session_manager — 下一轮 LLM 才能读到本轮对话
            self._persist_session(session_id, user_msg, final_reply, final_reasoning)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("react_task stream failed")
            await self._send_json({"event": "error", "data": {"message": str(e)}})

    def _prepare_session_context(
        self, message: dict[str, Any], session_id: str
    ) -> tuple[str, list[dict]]:
        """与 HTTP /stream 对齐: 注入 image_refs + 取 session.history.

        委托给共享函数 prepare_session_context, 消除 /stream 和 /ws 路径的重复实现.

        Returns:
            (user_msg_with_image_refs, session_history)
        """
        from cognitiveplane.interaction.api._helpers import prepare_session_context
        return prepare_session_context(
            message, session_id, self._session_manager, self._router,
        )

    def _persist_session(
        self,
        session_id: str,
        user_msg: str,
        final_reply: str,
        final_reasoning: str | None,
    ) -> None:
        """流结束后把本轮 user/assistant 消息追加到 session.messages — 与 HTTP /stream 一致.

        委托给共享函数 persist_session, 消除 /stream 和 /ws 路径的重复实现.
        """
        from cognitiveplane.interaction.api._helpers import persist_session
        persist_session(
            self._session_manager, session_id, user_msg, final_reply, final_reasoning,
        )

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
            try:
                self._feedback_queue.get_nowait()
                self._feedback_queue.put_nowait(summary)
            except asyncio.QueueEmpty:
                logger.debug("agent_loop cleanup error", exc_info=True)
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
        from cognitiveplane.shared.dto.memory import MemoryContent
        from cognitiveplane.shared.enums import MemoryType
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
                feature_vector=[0.0],
            )
            write_input = MemoryWriteInput(
                memory_type=MemoryType.OPERATOR_FEEDBACK,
                content=content,
                source_decision_id=DecisionId(value=uuid4()),
            )
            output = await write_port.write(write_input)
            return str(output.memory_id.value)
        except Exception as e:
            logger.exception("feedback persist failed")
            await self._send_json({
                "type": "feedback_rejected",
                "reason": str(e),
            })
            return None

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
            return

        self._react_task.cancel()
        try:
            await self._react_task
        except asyncio.CancelledError:
            logger.debug("agent_loop cleanup error", exc_info=True)
        self._react_task = None

        await self._send_json({
            "type": "interrupted",
            "reason": msg.reason or "user requested",
        })
