"""Control Plane configuration.

与 cognitiveplane/app.py 的 bridge 装配对称读取环境变量，
确保 L1 和 L2 连同一个 Temporal Server。
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class ControlPlaneConfig:
    """Temporal Worker 配置。

    环境变量（与 cognitiveplane/app.py 的 TemporalWorkflowLaunchPort 对称）:
        TEMPORAL_HOST: Temporal Server 地址（默认 localhost:7233）
        TEMPORAL_NAMESPACE: Temporal namespace（默认 default）
        TEMPORAL_TASK_QUEUE: Worker task queue（默认 control-plane）
    """
    temporal_host: str | None = None
    namespace: str | None = None
    task_queue: str | None = None

    def __post_init__(self) -> None:
        # P2-4 fix: 用 or 对空字符串也 fallback（os.environ.get 对空字符串返回 ""）
        if self.temporal_host is None:
            self.temporal_host = os.environ.get("TEMPORAL_HOST") or "localhost:7233"
        if self.namespace is None:
            self.namespace = os.environ.get("TEMPORAL_NAMESPACE") or "default"
        if self.task_queue is None:
            self.task_queue = os.environ.get("TEMPORAL_TASK_QUEUE") or "control-plane"
