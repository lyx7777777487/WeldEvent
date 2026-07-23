"""Bridge — WorkflowLauncher（boundary-pinning §6.2 重写）。

⚠️ 本文件已按 boundary-pinning §6.2 重写，废弃 legacy 的
`BrainDecision → WorkflowTemplate` 管线，改为
`WorkflowSpec → temporal_client.start_workflow(RunWorkflowSpec)` 直通。

旧版 WorkflowLaunchPort(ABC) + WorkflowLaunchResult + WorkflowLauncher
保留为兼容层，但 submit 签名改为接收 WorkflowSpec。

Source: boundary-pinning §6.2
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from cognitiveplane.shared.dto_workflow import WorkflowSpec

logger = logging.getLogger(__name__)


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

    async def query_status(self, workflow_id: str) -> dict[str, Any]:
        """查询 workflow 状态。"""
        raise NotImplementedError("query_status not supported by this port")

    async def send_human_gate_signal(self, workflow_id: str, node_id: str, approved: bool) -> bool:
        """发送 HumanGate signal。"""
        raise NotImplementedError("send_human_gate_signal not supported by this port")

    async def send_signal(
        self, workflow_id: str, signal_name: str, args: Any = None
    ) -> bool:
        """P1-6: 发送通用 Temporal signal(pause/resume/cancel_by_user 等)。

        Args:
            workflow_id: 目标 workflow ID
            signal_name: dag_runner_workflow.py 中 @workflow.signal 方法名
            args: signal 参数(无参 signal 传 None)

        Returns:
            True=成功, False=失败或不支持
        """
        raise NotImplementedError("send_signal not supported by this port")

    async def cancel_workflow(self, workflow_id: str, reason: str = "user requested") -> bool:
        """取消 workflow。"""
        raise NotImplementedError("cancel_workflow not supported by this port")

    # ── Op 16-19: Plan versioning bridge methods ──

    async def send_rework_signal(self, workflow_id: str, node_id: str) -> bool:
        """Op 20: Send rework_node signal to trigger dependency pollution BFS."""
        return await self.send_signal(workflow_id, "rework_node", [node_id])

    async def send_batch_signals(self, workflow_id: str, signals: list[dict]) -> bool:
        """Op 37: Send batch_signals signal."""
        return await self.send_signal(workflow_id, "batch_signals", [signals])



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

    async def query_status(self, workflow_id: str) -> dict[str, Any]:
        """查询 workflow 状态（转发到底层 port）。"""
        try:
            return await self._port.query_status(workflow_id)
        except NotImplementedError:
            return {"error": "query_status not supported by current adapter"}

    async def send_human_gate_signal(self, workflow_id: str, node_id: str, approved: bool) -> bool:
        """发送 HumanGate signal（转发到底层 port）。"""
        try:
            return await self._port.send_human_gate_signal(workflow_id, node_id, approved)
        except NotImplementedError:
            logger.warning("send_human_gate_signal: port does not support signals (workflow=%s)", workflow_id)
            return False

    async def send_signal(
        self, workflow_id: str, signal_name: str, args: Any = None
    ) -> bool:
        """P1-6: 发送通用 Temporal signal（转发到底层 port）。"""
        try:
            return await self._port.send_signal(workflow_id, signal_name, args)
        except NotImplementedError:
            logger.warning(
                "send_signal: port does not support signals (workflow=%s signal=%s)",
                workflow_id, signal_name,
            )
            return False

    async def cancel_workflow(self, workflow_id: str, reason: str = "user requested") -> bool:
        """取消 workflow（转发到底层 port）。"""
        try:
            return await self._port.cancel_workflow(workflow_id, reason)
        except NotImplementedError:
            logger.warning("cancel_workflow: port does not support cancel (workflow=%s)", workflow_id)
            return False

    # ── Op 16-19: Plan versioning bridge methods ──

    async def send_rework_signal(self, workflow_id: str, node_id: str) -> bool:
        """Op 20: Send rework_node signal."""
        return await self.send_signal(workflow_id, "rework_node", [node_id])

    async def send_batch_signals(self, workflow_id: str, signals: list[dict]) -> bool:
        """Op 37: Send batch_signals signal."""
        return await self.send_signal(workflow_id, "batch_signals", [signals])



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
