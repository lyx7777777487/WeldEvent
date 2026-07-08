"""WorkflowEventBus — L2→L1 节点事件回传 + SSE 多订阅者广播。

设计目标：
  1. L2 worker 执行 Temporal workflow 时,在节点 start/end/error/fail 时
     调 L1 的 POST /api/v1/workflow/events 推送事件
  2. L1 收到事件后按 session_id 路由到对应订阅者(SSE 流)
  3. 前端用 EventSource 长连接订阅 /api/v1/workflow/stream/{session_id},
     实时收到节点事件渲染进度卡片

支持多订阅者：同一 session 可有多个 SSE 连接(多标签页),事件广播给全部。
解耦设计：L2 不感知前端,只 push 到 L1;L1 不感知 L2,只按 session 路由。

Source: 用户需求"过程逐步展示+每步可介入"+ Anthropic "course-correct early"
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

logger = logging.getLogger("workflow_events")

PublishHook = Callable[["WorkflowEvent"], Awaitable[None]]


@dataclass
class WorkflowEvent:
    """单个工作流节点事件 — L2→L1→前端 的统一载荷。"""
    session_id: str
    workflow_id: str
    event_type: str  # "node_start" | "node_end" | "node_error" | "workflow_started" | "workflow_completed" | "workflow_failed" | "workflow_paused" | "workflow_resumed"
    node_id: str | None = None
    node_status: str | None = None  # "ok" | "marginal" | "ng" | "error"
    node_data: dict[str, Any] | None = None
    error: str | None = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "workflow_id": self.workflow_id,
            "event_type": self.event_type,
            "node_id": self.node_id,
            "node_status": self.node_status,
            "node_data": self.node_data,
            "error": self.error,
            "timestamp": self.timestamp,
        }


class WorkflowEventBus:
    """进程级 WorkflowEvent 广播总线 — session_id → 多订阅者 Queue。

    生命周期：
      - 前端 EventSource 连接 /api/v1/workflow/stream/{session_id} 时 subscribe()
      - L2 调 POST /api/v1/workflow/events 时 publish()
      - 前端断开时 unsubscribe()

    设计：
      - 每个 session 一个 list[asyncio.Queue],支持多订阅者广播
      - Queue 设 maxsize=256,满了丢弃最旧事件(防止慢消费者阻塞 L2 emit)
      - subscribe 返回 AsyncIterator,供 SSE 端点 yield
      - 订阅者断开时自动清理 Queue,session 无订阅者时清理整个 list
    """

    def __init__(self) -> None:
        # session_id → list[asyncio.Queue]（多订阅者）
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        # 历史事件缓存 — 新订阅者连接时立即收到最近 N 条事件(避免错过已发生的)
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._history_limit = 50
        # 最近事件状态(workflow_id → 最新 status)— 供 LLM query_workflow_status 用
        self._workflow_status: dict[str, dict[str, Any]] = {}
        # publish hooks — WorkflowObserver 订阅关键事件用
        self._publish_hooks: list[PublishHook] = []

    def add_publish_hook(self, hook: PublishHook) -> None:
        """注册 publish hook — WorkflowObserver 用此订阅 workflow 事件.

        hook 在 publish() 末尾被 await 调用,异常被吞掉(不影响广播).
        """
        self._publish_hooks.append(hook)

    def remove_publish_hook(self, hook: PublishHook) -> None:
        """卸载 publish hook."""
        try:
            self._publish_hooks.remove(hook)
        except ValueError:
            pass

    async def publish(self, event: WorkflowEvent) -> None:
        """L2 emit 事件时调此方法 — 广播给所有订阅者 + 存历史。"""
        evt_dict = event.to_dict()
        # 存历史(供新订阅者回看)
        hist = self._history.setdefault(event.session_id, [])
        hist.append(evt_dict)
        if len(hist) > self._history_limit:
            del hist[: len(hist) - self._history_limit]
        # 更新 workflow 状态缓存(供 LLM query)
        wf_status = self._workflow_status.setdefault(event.workflow_id, {
            "workflow_id": event.workflow_id,
            "session_id": event.session_id,
            "status": "RUNNING",
            "nodes": {},
        })
        if event.event_type == "workflow_started":
            wf_status["status"] = "RUNNING"
        elif event.event_type == "workflow_completed":
            wf_status["status"] = "COMPLETED"
        elif event.event_type == "workflow_failed":
            wf_status["status"] = "FAILED"
        elif event.event_type == "workflow_paused":
            wf_status["status"] = "PAUSED"
        elif event.event_type == "workflow_resumed":
            wf_status["status"] = "RUNNING"
        elif event.node_id and event.event_type in ("node_start", "node_end", "node_error"):
            wf_status["nodes"][event.node_id] = {
                "status": event.node_status or ("running" if event.event_type == "node_start" else "unknown"),
                "data": event.node_data,
                "error": event.error,
                "event_type": event.event_type,
            }
        # 广播给订阅者
        subs = self._subscribers.get(event.session_id, [])
        for q in subs:
            try:
                q.put_nowait(evt_dict)
            except asyncio.QueueFull:
                # 慢消费者 — 丢最旧事件腾位置
                try:
                    q.get_nowait()
                    q.put_nowait(evt_dict)
                except Exception:
                    logger.warning("workflow_event_bus: queue full, dropping oldest for session=%s", event.session_id)

        # 通知 publish hooks (WorkflowObserver) — 异步,异常吞掉不影响广播
        for hook in self._publish_hooks:
            try:
                await hook(event)
            except Exception:
                logger.debug("publish hook failed", exc_info=True)

    def subscribe(self, session_id: str) -> tuple[asyncio.Queue, list[dict[str, Any]]]:
        """前端 SSE 连接时调此方法 — 返回 (Queue, 历史事件)。

        历史事件立即返回,让新连接的前端看到已发生的节点进度。
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.setdefault(session_id, []).append(q)
        hist = list(self._history.get(session_id, []))
        return q, hist

    def unsubscribe(self, session_id: str, q: asyncio.Queue) -> None:
        """前端 SSE 断开时调此方法 — 清理 Queue。"""
        subs = self._subscribers.get(session_id)
        if subs is None:
            return
        try:
            subs.remove(q)
        except ValueError:
            logger.debug("unsubscribe ValueError, ignored")
        if not subs:
            self._subscribers.pop(session_id, None)

    def get_workflow_status(self, workflow_id: str) -> dict[str, Any] | None:
        """LLM query_workflow_status 工具调此方法 — 返回最新缓存的 workflow 状态。"""
        return self._workflow_status.get(workflow_id)

    def list_session_workflows(self, session_id: str) -> list[str]:
        """列出 session 内所有 workflow_id — 供 LLM 查询当前 session 的工作流。"""
        return [
            wf_id for wf_id, st in self._workflow_status.items()
            if st.get("session_id") == session_id
        ]

    def get_session_workflow_summary(self, session_id: str) -> list[dict[str, Any]]:
        """获取 session 内所有 workflow 的状态摘要 — 供 ReActEngine 注入 system prompt.

        返回格式:
            [
                {
                    "workflow_id": "wf-xxx",
                    "status": "COMPLETED" | "RUNNING" | "PAUSED" | "FAILED",
                    "nodes": {
                        "iqa_node": {"status": "ok", "data": {...}, "event_type": "node_end"},
                        ...
                    }
                },
                ...
            ]
        """
        result: list[dict[str, Any]] = []
        for wf_id in self.list_session_workflows(session_id):
            status = self._workflow_status.get(wf_id)
            if status is not None:
                result.append(status)
        return result


# ── 进程级单例 ──────────────────────────────────────────────────────
# chat.py create_chat_router() 和 L2 emit 端点共享同一实例
_global_bus: WorkflowEventBus | None = None


def get_workflow_event_bus() -> WorkflowEventBus:
    """获取进程级 WorkflowEventBus 单例。"""
    global _global_bus
    if _global_bus is None:
        _global_bus = WorkflowEventBus()
    return _global_bus


__all__ = ["WorkflowEvent", "WorkflowEventBus", "get_workflow_event_bus"]
