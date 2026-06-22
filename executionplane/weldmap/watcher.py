"""State Watcher — WeldMap 变更订阅机制。

Source: Complete_architecture_V1.docx §2.3

Agent B 不需要轮询 WeldMap 检查数据是否就绪。
只需订阅一条路径模式，当 Agent A 写入该路径时自动通知。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from .models import WorkflowId, WeldMapPath


@dataclass
class Subscription:
    """一个订阅记录。"""
    subscriber_id: str                # 订阅方 Agent 标识
    workflow_id: WorkflowId
    path_pattern: str                 # 路径模式，支持前缀匹配: 'mask/', 'annotations/'
    callback: Callable[[str, Any], Awaitable[None]]  # (path, new_value) -> None
    active: bool = True


class StateWatcher(ABC):
    """State Watcher 抽象 — 管理 WeldMap 路径变更订阅。

    实现可以基于:
      - asyncio.Event / Condition (同进程 InMemory)
      - NATS JetStream (跨进程/分布式)
      - Redis PubSub (跨进程)
    """

    @abstractmethod
    async def subscribe(self, subscription: Subscription) -> None:
        """注册一个路径订阅。"""
        ...

    @abstractmethod
    async def unsubscribe(self, subscriber_id: str, workflow_id: WorkflowId) -> None:
        """取消某 Agent 对某 Workflow 的所有订阅。"""
        ...

    @abstractmethod
    async def notify_write(
        self, workflow_id: WorkflowId, path: WeldMapPath, value: Any
    ) -> int:
        """通知某路径被写入。返回触发的订阅回调数量。

        由 WeldMapClient.write_path() 在写入成功后自动调用。
        """
        ...
