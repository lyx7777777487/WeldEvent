"""WorkflowObserver — boundary-pinning §3 AgentLoop 三件套之"长路径 Temporal 观察者".

Spec: docs/superpowers/specs/2026-06-25-phase3-boundary-pinning-design.md §3

职责:
  - 订阅 WorkflowEventBus 的 publish hook,收到 workflow 关键事件时:
    1. 通过 NotificationStore 推送给前端 WebSocket (实时通知)
    2. 事件已自动缓存在 WorkflowEventBus (ReActEngine 下一轮从 system prompt 读取)

设计原则 (不混乱):
  - 只观察,不主动启动 ReAct — 避免 LLM 无限循环
  - 让用户/前端决定是否触发下一轮推理
  - ReActEngine 通过 workflow_event_bus.get_session_workflow_summary() 读状态
    注入 system prompt (见 react.py _build_system_prompt)

关键事件类型:
  - workflow_completed: 工作流执行完成
  - workflow_failed: 工作流执行失败
  - workflow_paused: 工作流暂停等待人工决策
  - workflow_resumed: 工作流从暂停恢复执行 (info 级,不推送通知)
  - node_*: 节点级事件 (debug 级,不推送通知,仅更新缓存)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from cognitiveplane.interaction.notifications.store import (
        Notification,
        NotificationStore,
        NotificationType,
    )
    from cognitiveplane.interaction.workflow_events import WorkflowEvent, WorkflowEventBus


# 关键事件 — 收到时推送通知给前端
_KEY_EVENTS = frozenset({
    "workflow_completed",
    "workflow_failed",
    "workflow_paused",
})


class WorkflowObserver:
    """长路径 Temporal 观察者 — boundary-pinning §3 第三件套.

    订阅 WorkflowEventBus 的 publish hook,收到 workflow 关键事件时
    通过 NotificationStore 推送给前端.

    生命周期:
      - app.py lifespan 启动时 await observer.start()
      - lifespan 关闭时 await observer.stop()
      - start() 注册 publish hook 到 WorkflowEventBus
      - stop() 卸载 publish hook

    使用:
        observer = WorkflowObserver(event_bus, notification_store)
        await observer.start()
        # ... workflow 事件自动推送给前端 ...
        await observer.stop()
    """

    def __init__(
        self,
        event_bus: "WorkflowEventBus",
        notification_store: "NotificationStore",
    ) -> None:
        self._event_bus = event_bus
        self._notification_store = notification_store
        self._started = False

    async def start(self) -> None:
        """注册 publish hook 到 WorkflowEventBus."""
        if self._started:
            return
        self._event_bus.add_publish_hook(self._on_event)
        self._started = True
        logger.info("[WorkflowObserver] started — observing workflow events")

    async def stop(self) -> None:
        """卸载 publish hook."""
        if not self._started:
            return
        self._event_bus.remove_publish_hook(self._on_event)
        self._started = False
        logger.info("[WorkflowObserver] stopped")

    async def _on_event(self, event: "WorkflowEvent") -> None:
        """收到 workflow 事件时处理 — 只对关键事件推送通知."""
        if event.event_type not in _KEY_EVENTS:
            return

        # 延迟 import 避免循环依赖
        from cognitiveplane.interaction.notifications.store import (
            Notification,
            NotificationType,
        )

        # 构造通知标题和消息
        title, message = self._build_notification(event)

        notification = Notification(
            notification_type=NotificationType.WORKFLOW_UPDATE,
            title=title,
            message=message,
            payload={
                "workflow_id": event.workflow_id,
                "session_id": event.session_id,
                "event_type": event.event_type,
                "node_data": event.node_data,
                "error": event.error,
            },
            expires_at=None,
            operator_id=event.session_id,
        )
        try:
            await self._notification_store.add(notification)
        except Exception:
            logger.debug("WorkflowObserver notification add failed", exc_info=True)

    def _build_notification(
        self, event: "WorkflowEvent"
    ) -> tuple[str, str]:
        """根据事件类型构造通知标题和消息."""
        wf_id = event.workflow_id
        if event.event_type == "workflow_completed":
            return (
                "工作流已完成",
                f"工作流 {wf_id} 已执行完成。你可以在对话中询问 AI 分析执行结果。",
            )
        if event.event_type == "workflow_failed":
            err = event.error or "未知错误"
            return (
                "工作流执行失败",
                f"工作流 {wf_id} 执行失败: {err}。你可以在对话中询问 AI 如何处理。",
            )
        if event.event_type == "workflow_paused":
            return (
                "工作流已暂停",
                f"工作流 {wf_id} 已暂停,等待人工决策。请在执行面板查看详情并处理。",
            )
        # 兜底 (不应该走到这里,因为 _KEY_EVENTS 已过滤)
        return (
            "工作流状态更新",
            f"工作流 {wf_id} 状态更新: {event.event_type}",
        )
