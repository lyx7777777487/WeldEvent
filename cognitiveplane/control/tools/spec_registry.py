"""P3-6 fix: In-memory WorkflowSpec registry.

design_workflow 把产出的 WorkflowSpec 存到这里，返回 workflow_id 给 LLM。
launch_workflow 优先用 workflow_id 从这里取 spec，避免 LLM 把整个 spec
dict 作为参数传递（节省 token + 减少 schema 校验开销）。

向后兼容：launch_workflow 仍接受 workflow_spec 参数（从 registry 取不到时回退）。

Phase 3+ 可替换为 Redis/DB 持久化实现，接口不变。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cognitiveplane.shared.dto_workflow import WorkflowSpec

logger = logging.getLogger(__name__)

# TTL：spec 在 registry 中保留 10 分钟（足够 LLM 多轮工具调用 + launch）
# 超过后被 cleanup 移除，避免内存泄漏。
_TTL_SECONDS = 600.0
# 最大条目数（防滥用）
_MAX_ENTRIES = 200


class WorkflowSpecRegistry:
    """线程安全的 in-memory WorkflowSpec 注册表，带 TTL。"""

    def __init__(self, ttl_seconds: float = _TTL_SECONDS, max_entries: int = _MAX_ENTRIES) -> None:
        self._store: dict[str, tuple["WorkflowSpec", float]] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
        self._max = max_entries

    def put(self, spec: "WorkflowSpec") -> str:
        """存 spec，返回 workflow_id。"""
        wf_id = spec.workflow_id
        with self._lock:
            self._store[wf_id] = (spec, time.monotonic())
            # 超过上限时清理过期项
            if len(self._store) > self._max:
                self._cleanup_locked(time.monotonic())
        return wf_id

    def get(self, workflow_id: str) -> "WorkflowSpec | None":
        """取 spec。不存在或已过期返回 None。"""
        with self._lock:
            entry = self._store.get(workflow_id)
            if entry is None:
                return None
            spec, created_at = entry
            if (time.monotonic() - created_at) > self._ttl:
                # 过期 — 顺便清理
                self._store.pop(workflow_id, None)
                return None
            return spec

    def _cleanup_locked(self, now: float) -> int:
        """调用方需持有 _lock。移除过期项，返回移除数。"""
        expired = [
            wid for wid, (_, ts) in self._store.items()
            if (now - ts) > self._ttl
        ]
        for wid in expired:
            del self._store[wid]
        return len(expired)

    def clear(self) -> None:
        """清空（测试用）。"""
        with self._lock:
            self._store.clear()


# 模块级单例 — design_workflow 和 launch_workflow 共享
_default_registry: WorkflowSpecRegistry | None = None


def get_default_registry() -> WorkflowSpecRegistry:
    """获取模块级单例 registry。"""
    global _default_registry
    if _default_registry is None:
        _default_registry = WorkflowSpecRegistry()
    return _default_registry


def reset_default_registry() -> None:
    """重置单例（测试隔离用）。"""
    global _default_registry
    _default_registry = None


__all__ = ["WorkflowSpecRegistry", "get_default_registry", "reset_default_registry"]
