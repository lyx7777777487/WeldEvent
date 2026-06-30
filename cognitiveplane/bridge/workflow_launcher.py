"""Bridge — WorkflowLauncher（boundary-pinning §6.2 重写）。

⚠️ 本文件已按 boundary-pinning §6.2 重写，废弃 legacy 的
`BrainDecision → WorkflowTemplate` 管线，改为
`WorkflowSpec → temporal_client.start_workflow(RunWorkflowSpec)` 直通。

旧版 WorkflowLaunchPort(ABC) + WorkflowLaunchResult + WorkflowLauncher
保留为兼容层，但 submit 签名改为接收 WorkflowSpec。

Source: boundary-pinning §6.2
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from cognitiveplane.shared.dto_workflow import WorkflowSpec


@dataclass(frozen=True)
class WorkflowLaunchResult:
    """L1→L2 提交结果。"""
    workflow_id: str
    run_id: str
    accepted: bool = True
    adapter: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class WorkflowLaunchPort(ABC):
    """Outbound port — L2 Temporal 适配器实现此接口。

    boundary-pinning §6.2: bridge 层把 WorkflowSpec 翻译成
    temporal_client.start_workflow(RunWorkflowSpec, workflow_spec)。
    """

    @abstractmethod
    async def submit(self, spec: WorkflowSpec) -> WorkflowLaunchResult: ...


class WorkflowLauncher:
    """提交 WorkflowSpec 到 L2 Temporal。

    默认用 _NullWorkflowLaunchPort（不提交，仅记录），生产环境在
    app.py::_build_dependencies() 中替换为 TemporalWorkflowLaunchPort。
    """

    def __init__(self, port: WorkflowLaunchPort | None = None) -> None:
        self._port = port or _NullWorkflowLaunchPort()

    async def launch(self, spec: WorkflowSpec) -> WorkflowLaunchResult:
        """提交 WorkflowSpec 启动 L2 Temporal workflow。"""
        return await self._port.submit(spec)

    async def aclose(self) -> None:
        """P2-3: 转发 port.aclose()（若 port 支持）。"""
        if hasattr(self._port, 'aclose'):
            await self._port.aclose()

    async def is_healthy(self) -> bool:
        """P2-R3-5: 转发 port.is_healthy()（若 port 支持），否则默认健康。"""
        if hasattr(self._port, 'is_healthy'):
            return await self._port.is_healthy()
        return True


class _NullWorkflowLaunchPort(WorkflowLaunchPort):
    """默认空适配器 — Temporal 未配置时不提交，仅记录。

    用于开发/测试环境，或 Temporal Server 未启动时的降级。
    """

    async def submit(self, spec: WorkflowSpec) -> WorkflowLaunchResult:
        return WorkflowLaunchResult(
            workflow_id=spec.workflow_id,
            run_id="",
            accepted=False,
            adapter="null",
            metadata={"reason": "Temporal not configured"},
            error="No Temporal adapter configured (set TemporalWorkflowLaunchPort in app.py)",
        )


__all__ = ["WorkflowLaunchPort", "WorkflowLaunchResult", "WorkflowLauncher"]
