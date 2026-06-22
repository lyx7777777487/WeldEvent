"""InMemory WeldMap Client — 开发/测试用的内存实现。

生产环境应替换为 Redis / MinIO+DB 等持久化实现。
"""

from datetime import datetime, timezone
from typing import Any

from .client import ReadResult, WriteResult, WeldMapClient, WeldMapPath, WorkflowId
from .models import (
    AnnotationsData,
    DecisionData,
    ImageQualityReport,
    MaskData,
    ValidationData,
    WeldMapEvent,
    WeldMapSnapshot,
)
from .watcher import StateWatcher


class InMemoryStateWatcher(StateWatcher):
    """InMemory State Watcher — 同进程内通知。"""
    
    def __init__(self) -> None:
        self._subscriptions: dict[str, list] = {}  # wf_id -> [Subscription]
    
    async def subscribe(self, subscription) -> None:
        key = f"{subscription.workflow_id}"
        if key not in self._subscriptions:
            self._subscriptions[key] = []
        self._subscriptions[key].append(subscription)
    
    async def unsubscribe(self, subscriber_id: str, workflow_id: WorkflowId) -> None:
        key = str(workflow_id)
        if key in self._subscriptions:
            self._subscriptions[key] = [
                s for s in self._subscriptions[key]
                if s.subscriber_id != subscriber_id
            ]
    
    async def notify_write(
        self, workflow_id: WorkflowId, path: WeldMapPath, value: Any
    ) -> int:
        key = str(workflow_id)
        notified = 0
        for sub in self._subscriptions.get(key, []):
            if not sub.active:
                continue
            # 前缀匹配
            if path.startswith(sub.path_pattern):
                await sub.callback(str(path), value)
                notified += 1
        return notified


class InMemoryWeldMapClient(WeldMapClient):
    """内存实现的 WeldMap 客户端 — 适合开发和单测。
    
    数据存储在 Python dict 中，进程重启后丢失。
    """

    def __init__(self, watcher: StateWatcher | None = None) -> None:
        self._stores: dict[str, WeldMapSnapshot] = {}
        self._watcher = watcher or InMemoryStateWatcher()

    async def initialize(self, workflow_id: WorkflowId) -> WeldMapSnapshot:
        snapshot = WeldMapSnapshot(workflow_id=str(workflow_id))
        self._stores[str(workflow_id)] = snapshot
        return snapshot

    async def read_snapshot(self, workflow_id: WorkflowId) -> WeldMapSnapshot | None:
        return self._stores.get(str(workflow_id))

    async def read_path(
        self, workflow_id: WorkflowId, path: WeldMapPath
    ) -> ReadResult:
        snapshot = self._stores.get(str(workflow_id))
        if snapshot is None:
            return ReadResult(found=False, path=str(path))
        
        # 路径映射到属性
        value = self._get_path_value(snapshot, str(path))
        version = snapshot.version
        
        return ReadResult(
            found=value is not None,
            value=value,
            path=str(path),
            version=version,
        )

    async def write_path(
        self,
        workflow_id: WorkflowId,
        path: WeldMapPath,
        value: Any,
        expected_version: int | None = None,
        source: str = "",
    ) -> WriteResult:
        key = str(workflow_id)
        snapshot = self._stores.get(key)
        
        if snapshot is None:
            # 自动初始化
            snapshot = await self.initialize(workflow_id)
        
        # CAS 检查
        if expected_version is not None and snapshot.version != expected_version:
            return WriteResult(
                success=False,
                path=str(path),
                version=snapshot.version,
                conflict=True,
            )
        
        # 写入
        old_value = self._get_path_value(snapshot, str(path))
        self._set_path_value(snapshot, str(path), value)
        snapshot.version += 1
        snapshot.updated_at = datetime.now(timezone.utc)
        
        # 记录事件
        event = WeldMapEvent(
            event_type=f"{path.replace('/', '_')}_updated",
            path=str(path),
            old_value=old_value,
            new_value=value,
            source=source or "unknown",
        )
        snapshot.events.append(event.model_dump())
        
        # 通知订阅者
        await self._watcher.notify_write(workflow_id, path, value)
        
        return WriteResult(
            success=True,
            path=str(path),
            version=snapshot.version,
            conflict=False,
        )

    async def append_event(self, workflow_id: WorkflowId, event: WeldMapEvent) -> None:
        snapshot = self._stores.get(str(workflow_id))
        if snapshot:
            snapshot.events.append(event.model_dump())

    async def read_events(
        self, workflow_id: WorkflowId, since: int | None = None
    ) -> list[WeldMapEvent]:
        snapshot = self._stores.get(str(workflow_id))
        if snapshot is None:
            return []
        events_data = snapshot.events[since:] if since is not None else snapshot.events
        return [WeldMapEvent(**e) for e in events_data]

    def clear(self, workflow_id: WorkflowId | None = None) -> None:
        """清除数据（测试用）。"""
        if workflow_id:
            self._stores.pop(str(workflow_id), None)
        else:
            self._stores.clear()

    # ------------------------------------------------------------------
    # 路径 → 属性 映射
    # ------------------------------------------------------------------

    @staticmethod
    def _get_path_value(snapshot: WeldMapSnapshot, path: str) -> Any:
        # 支持更多路径
        path_mapping = {
            "image/quality": snapshot.image_quality,
            "image/preprocess": snapshot.image_quality,  # 暂时映射到image_quality（后续可扩展）
            "mask": snapshot.mask,
            "annotations": snapshot.annotations,
            "validation": snapshot.validation,
            "decision": snapshot.decision,
        }
        
        # 支持嵌套路径访问
        if path in path_mapping:
            return path_mapping[path]
        
        # 尝试从snapshot的events中获取最新写入
        for event in reversed(snapshot.events):
            if event.get("path") == path:
                return event.get("new_value")
        
        return None

    @staticmethod
    def _set_path_value(snapshot: WeldMapSnapshot, path: str, value: Any) -> None:
        # 支持更多路径写入
        if path == "image/quality" and isinstance(value, ImageQualityReport):
            snapshot.image_quality = value
        elif path == "image/preprocess":
            # 预处理结果存储在events中（后续可扩展Snapshot模型）
            pass  # 通过events自动记录
        elif path == "mask" and isinstance(value, MaskData):
            snapshot.mask = value
        elif path == "annotations" and isinstance(value, AnnotationsData):
            snapshot.annotations = value
        elif path == "validation" and isinstance(value, ValidationData):
            snapshot.validation = value
        elif path == "decision" and isinstance(value, DecisionData):
            snapshot.decision = value
        else:
            # 其他路径写入到events中
            pass  # 通过events自动记录
