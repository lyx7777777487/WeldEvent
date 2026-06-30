"""WeldMap Client — 跨 Activity 状态共享的键值存储抽象。

导出:
  - WeldMapClient: ABC 接口
  - InMemoryWeldMapClient: 内存实现（开发/测试用）
  - WorkflowId / WeldMapPath: 类型别名
  - ReadResult / WriteResult: 操作结果
  - WeldMapSnapshot / WeldMapEvent / 各 Data 模型
  - StateWatcher: 订阅 ABC
"""

from .client import (
    ReadResult,
    WeldMapClient,
    WeldMapPath,
    WorkflowId,
    WriteResult,
)
from .in_memory import InMemoryStateWatcher, InMemoryWeldMapClient
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

__all__ = [
    "WeldMapClient",
    "InMemoryWeldMapClient",
    "InMemoryStateWatcher",
    "StateWatcher",
    "WorkflowId",
    "WeldMapPath",
    "ReadResult",
    "WriteResult",
    "WeldMapSnapshot",
    "WeldMapEvent",
    "ImageQualityReport",
    "MaskData",
    "AnnotationsData",
    "ValidationData",
    "DecisionData",
]
