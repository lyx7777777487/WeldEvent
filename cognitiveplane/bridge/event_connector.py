"""Bridge — EventConnector（boundary-pinning §6.2 重写）。

⚠️ 本文件已按 boundary-pinning §6.2 重写，废弃 legacy 的
`BrainDecision → WorkflowTemplate` 管线，改为直接接收 WorkflowSpec
并提交到 L2 Temporal。

旧版 EventConnector 订阅 BrainDecision 事件并翻译为 WorkflowTemplate；
新版直接接收 design_workflow 工具产出的 WorkflowSpec，跳过翻译步骤。

Source: boundary-pinning §6.2
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from cognitiveplane.shared.dto_workflow import WorkflowSpec
from cognitiveplane.shared.types import CaseId

from .workflow_launcher import WorkflowLaunchResult, WorkflowLauncher

if TYPE_CHECKING:
    from cognitiveplane.gateway.ports import CognitiveGatewayWritePort

logger = logging.getLogger(__name__)


class EventConnector:
    """Glue layer: WorkflowSpec → Launcher → L2 Temporal。

    boundary-pinning §6.2: Brain 调 design_workflow 产出 WorkflowSpec，
    上层确认后通过 EventConnector 提交到 L2 Temporal。

    P2-9 fix: 提交 Temporal 前先调 gateway.notify_workflow_trigger，
    让 WeldMap workflow domain 记录触发事件（§6.2 契约 2）。
    Temporal 监听此 Signal 确保事件链完整。

    Phase 2 实现：直接接收 WorkflowSpec（无 BrainDecision 翻译步骤）。
    Phase 4+ 接 NATS JetStream 订阅 WeldMap workflow domain。
    """

    def __init__(
        self,
        launcher: WorkflowLauncher,
        gateway_write: "CognitiveGatewayWritePort | None" = None,
    ) -> None:
        self._launcher = launcher
        self._gateway_write = gateway_write

    async def on_workflow_spec(
        self, spec: WorkflowSpec
    ) -> WorkflowLaunchResult:
        """提交 WorkflowSpec 启动 L2 Temporal workflow。

        Args:
            spec: design_workflow 工具产出的 WorkflowSpec

        Returns:
            WorkflowLaunchResult 含 Temporal workflow_id / run_id / accepted
        """
        if not spec.nodes:
            logger.warning(
                "WorkflowSpec %s has no nodes — skipping launch",
                spec.workflow_id,
            )
            return WorkflowLaunchResult(
                workflow_id=spec.workflow_id,
                run_id="",
                accepted=False,
                adapter="null",
                error="WorkflowSpec has no nodes",
            )

        logger.info(
            "Launching workflow %s: objective=%s nodes=%d",
            spec.workflow_id, spec.objective, len(spec.nodes),
        )

        # P2-9 fix: 先通知 WeldMap workflow domain（§6.2 契约 2）
        # 让 L3 执行层能通过事件溯源感知到触发
        await self._notify_gateway(spec)

        result = await self._launcher.launch(spec)

        if result.accepted:
            logger.info(
                "Workflow %s launched: run_id=%s adapter=%s",
                result.workflow_id, result.run_id, result.adapter,
            )
        else:
            logger.warning(
                "Workflow %s launch failed: %s",
                result.workflow_id, result.error,
            )

        return result

    async def _notify_gateway(self, spec: WorkflowSpec) -> None:
        """P2-9: 调 gateway.notify_workflow_trigger 写 WeldMap workflow domain。

        从 spec.nodes 的 caller_context 提取 case_id（取第一个非空）。
        gateway_write 为 None 时跳过（开发/测试环境降级）。
        通知失败不阻断 Temporal 提交 — WeldMap 写入是事件溯源记录，
        Temporal workflow 本身不依赖此信号（Phase 2 直连提交）。
        """
        if self._gateway_write is None:
            return
        # 从 nodes 的 caller_context 提取 case_id
        case_id_str: str | None = None
        for node in spec.nodes:
            ctx = node.caller_context
            if ctx and ctx.case_id:
                case_id_str = ctx.case_id
                break
        if not case_id_str:
            case_id_str = "unknown"
        try:
            workflow_config = {
                "workflow_id": spec.workflow_id,
                "objective": spec.objective,
                "node_count": len(spec.nodes),
            }
            await self._gateway_write.notify_workflow_trigger(
                CaseId(value=case_id_str),
                workflow_config,
            )
            logger.info(
                "Notified WeldMap workflow domain: case=%s workflow=%s",
                case_id_str, spec.workflow_id,
            )
        except Exception as e:
            # P2-7 fix: 通知失败不阻断 Temporal 提交（见方法 docstring）
            # 但要区分编程错误（AttributeError/TypeError）和运行时故障
            if isinstance(e, (AttributeError, TypeError)):
                # 编程错误（如 gateway_write 缺方法）— 记 error 级别暴露问题
                logger.error(
                    "gateway.notify_workflow_trigger programming error "
                    "(non-blocking but must fix): %s: %s",
                    type(e).__name__, e, exc_info=True,
                )
            else:
                # 运行时故障（如 gateway 不可用）— 记 warning 降级
                logger.warning(
                    "gateway.notify_workflow_trigger failed (non-blocking): %s: %s",
                    type(e).__name__, e,
                )

    async def aclose(self) -> None:
        """P2-3: 转发 launcher.aclose()，关闭 Temporal Client 连接。"""
        await self._launcher.aclose()

    async def is_healthy(self) -> bool:
        """P2-R3-5: 转发 launcher.is_healthy()，探测 Temporal 连通性。"""
        return await self._launcher.is_healthy()

    async def query_status(self, workflow_id: str) -> dict[str, Any]:
        """查询 workflow 执行状态（转发到底层 Temporal port）。

        Returns:
            {
                "status": str,          # "RUNNING" | "COMPLETED" | "FAILED"
                "completed_nodes": list[str],
                "failed_nodes": list[str],
                "node_results": dict,    # node_id → {status, data, error}
                "error": str | None,
            }
        """
        return await self._launcher.query_status(workflow_id)

    async def send_human_gate_signal(
        self, workflow_id: str, node_id: str, approved: bool
    ) -> bool:
        """P2-9 fix: 向 Temporal workflow 发送 HumanGate signal。

        boundary-pinning §6.2 契约 5: feedback 走 Temporal HumanGateSignal。
        转发到底层 TemporalWorkflowLaunchPort（若支持）。

        Returns:
            True=成功, False=失败或不支持
        """
        return await self._launcher.send_human_gate_signal(workflow_id, node_id, approved)

    async def send_human_review_signal(
        self, workflow_id: str, node_id: str, review_result: dict
    ) -> bool:
        """Op 2: 发送 human_review signal (五决策: approve/rework/modify/reject/escalate)。"""
        return await self._launcher.send_human_review_signal(workflow_id, node_id, review_result)

    async def send_signal(
        self, workflow_id: str, signal_name: str, args: Any = None
    ) -> bool:
        """P1-6: 发送通用 Temporal signal(pause/resume/cancel_by_user)。

        LLM workflow_control 工具调此方法控制正在执行的 workflow。
        signal_name 与 dag_runner_workflow.py 的 @workflow.signal 方法名对应:
          - "pause"         → 暂停 workflow
          - "resume"        → 恢复暂停的 workflow
          - "cancel_by_user" → 用户取消 workflow

        Returns:
            True=成功, False=失败或不支持
        """
        return await self._launcher.send_signal(workflow_id, signal_name, args)

    async def cancel_workflow(self, workflow_id: str, reason: str = "user requested") -> bool:
        """取消 Temporal workflow（转发到底层 Temporal port）。

        Returns:
            True=成功, False=失败或不支持
        """
        return await self._launcher.cancel_workflow(workflow_id, reason)


__all__ = ["EventConnector"]
