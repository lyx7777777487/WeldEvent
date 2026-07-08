"""TemporalWorkflowLaunchPort — L1→L2 桥接适配器（boundary-pinning §6.2）。

把 cognitiveplane 产出的 WorkflowSpec 提交给 controlplane 的 Temporal
RunWorkflowSpec workflow。

设计原则（继承自 legacy workflow_launcher.py）：
  "The Cognitive Plane never imports the Temporal SDK directly;
   all Temporal-specific code lives behind WorkflowLaunchPort."

本文件是 WorkflowLaunchPort 的 Temporal 实现。temporalio 通过延迟 import
引入，仅在真用到时才需要 temporalio 已安装（可选依赖）。

Source: boundary-pinning §6.2 + legacy bridge/workflow_launcher.py
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from cognitiveplane.shared.dto_workflow import WorkflowSpec
from cognitiveplane.bridge.workflow_launcher import WorkflowLaunchPort, WorkflowLaunchResult

logger = logging.getLogger(__name__)


class TemporalWorkflowLaunchPort(WorkflowLaunchPort):
    """WorkflowLaunchPort 的 Temporal 实现。

    调用 temporalio.client.Client.start_workflow(RunWorkflowSpec,
    workflow_spec_dict) 启动 controlplane 的 generic DAG runner。

    Args:
        temporal_host: Temporal Server 地址（默认 localhost:7233）
        namespace: Temporal namespace（默认 default）
        task_queue: Worker task queue（默认 control-plane，与
            controlplane/config.py 的 ControlPlaneConfig.task_queue 对应）
    """

    def __init__(
        self,
        temporal_host: str = "localhost:7233",
        namespace: str = "default",
        task_queue: str = "control-plane",
    ) -> None:
        self._temporal_host = temporal_host
        self._namespace = namespace
        self._task_queue = task_queue
        self._client = None  # 延迟初始化 Client
        self._lock = asyncio.Lock()  # P2-2 fix: 防止并发首连 race condition

    async def _ensure_client(self) -> Any:
        """延迟连接 Temporal Client（首次调用时建立连接）。

        P2-2 fix: 用 asyncio.Lock 保护首次连接，防止并发协程
        同时进入导致建立多条连接（后者覆盖前者，前者泄漏）。
        """
        if self._client is not None:
            return self._client
        async with self._lock:
            # double-check：拿到锁后再检查一次（可能在等锁期间已被其他协程初始化）
            if self._client is not None:
                return self._client
            # 延迟 import — temporalio 是可选依赖
            from temporalio.client import Client
            self._client = await Client.connect(
                self._temporal_host,
                namespace=self._namespace,
            )
        return self._client

    async def aclose(self) -> None:
        """P2-3 fix: 关闭 Temporal Client 连接，释放 gRPC 资源。

        应在 FastAPI shutdown 钩子中调用。
        """
        if self._client is not None:
            # temporalio Client 没有 aclose，但底层 gRPC channel 会在 GC 时关闭
            # 显式置 None 让 GC 尽早回收
            self._client = None

    # ── P1-R3-2 / P2-R3-6 辅助方法 ──

    # 连接类异常关键词（不依赖 temporalio 内部异常类型，保持解耦）
    _CONNECTION_ERROR_KEYWORDS = (
        "connection", "unavailable", "transport", "channel",
        "refused", "reset", "unreachable",
    )

    def _is_connection_error(self, error: Exception) -> bool:
        """启发式判断是否为连接类异常（gRPC UNAVAILABLE / TCP refused / 等）。

        不依赖 temporalio 内部异常类型（保持 L1↔temporalio 解耦），用
        isinstance + 关键词匹配双重判断。
        """
        if isinstance(error, (ConnectionError, OSError)):
            return True
        error_text = f"{type(error).__name__}: {error}".lower()
        return any(kw in error_text for kw in self._CONNECTION_ERROR_KEYWORDS)

    def _maybe_invalidate_client(self, error: Exception) -> None:
        """P1-R3-2 fix: 连接类异常时置 self._client = None，触发下次重连。

        非连接类异常（如参数校验错、workflow 已存在）不重置 client —
        client 本身健康，重连只会浪费资源。
        """
        if self._is_connection_error(error):
            self._client = None

    def _sanitize_error(self, error: Exception) -> str:
        """P2-R3-6 fix: 脱敏错误信息，不泄漏 host:port / gRPC 内部细节给 LLM。

        - 连接类异常 → 分类化 "Temporal service unavailable"
        - 其他 → 异常类型名 + 脱敏消息（host:port 已替换为 <temporal-host>）
        """
        if self._is_connection_error(error):
            return "Temporal service unavailable"
        error_text = f"{type(error).__name__}: {error}"
        # 脱敏 host:port（如 localhost:7233 → <temporal-host>）
        import re
        sanitized = re.sub(r"[\w.-]+:\d{2,5}", "<temporal-host>", error_text)
        # 限制长度，避免泄漏过多内部栈
        return sanitized[:200]

    async def is_healthy(self) -> bool:
        """P2-R3-5: 探测 Temporal 连通性（供 /health 端点调用）。

        尝试建立/复用 Client 连接。成功返回 True，失败返回 False。
        不会抛异常 — /health 端点需要优雅降级。

        加 3 秒超时保护：Temporal 不可达时 gRPC 连接可能挂起很久，
        /health 不应因此阻塞。
        """
        try:
            await asyncio.wait_for(self._ensure_client(), timeout=3.0)
            return True
        except Exception:
            return False

    async def submit(self, spec: WorkflowSpec) -> WorkflowLaunchResult:
        """提交 WorkflowSpec 启动 RunWorkflowSpec workflow。

        Args:
            spec: cognitiveplane 的 WorkflowSpec DTO

        Returns:
            WorkflowLaunchResult 含 Temporal workflow_id / run_id / accepted
        """
        try:
            client = await self._ensure_client()

            # WorkflowSpec → dict（跨 Temporal 边界用 JSON 安全类型）
            spec_dict = self._spec_to_dict(spec)

            # 用 workflow_id 作为 Temporal workflow 的 ID（幂等性：同 ID 不会重复启动）
            workflow_id = spec.workflow_id

            # 延迟 import RunWorkflowSpec — 避免 cognitiveplane 强依赖 controlplane
            # 用字符串名 "RunWorkflowSpec" 而非类引用，解耦两模块
            from datetime import timedelta
            handle = await client.start_workflow(
                "RunWorkflowSpec",           # workflow type name（与 controlplane @workflow.defn(name=) 对应）
                spec_dict,                    # workflow input
                id=workflow_id,
                task_queue=self._task_queue,
                # P1-1 fix: workflow 级超时，防止 worker 缺席时永久挂起
                execution_timeout=timedelta(minutes=10),
            )

            return WorkflowLaunchResult(
                workflow_id=workflow_id,
                run_id=handle.run_id,
                accepted=True,
                adapter="temporal",
                error=None,
            )
        except Exception as e:
            # P1-R3-2 fix: 连接类异常时置 None 触发下次重连
            self._maybe_invalidate_client(e)
            # P2-R3-6 fix: 脱敏错误信息（不泄漏 host:port / gRPC 内部细节给 LLM）
            safe_error = self._sanitize_error(e)
            return WorkflowLaunchResult(
                workflow_id=spec.workflow_id,
                run_id="",
                accepted=False,
                adapter="temporal",
                error=safe_error,
            )

    async def query_status(self, workflow_id: str) -> dict[str, Any]:
        """查询 workflow 当前状态（通过 Temporal query）。

        Returns:
            {
                "status": str,
                "completed_nodes": list[str],
                "failed_nodes": list[str],
                "node_results": dict,
                "error": str | None,
            }
        """
        try:
            client = await self._ensure_client()
            handle = client.get_workflow_handle(workflow_id)
            result = await handle.query("query_status")
            return result if isinstance(result, dict) else {"error": "invalid query result"}
        except Exception as e:
            # P1-R3-2 + P2-R3-6: query 路径同样断线重连 + 脱敏
            self._maybe_invalidate_client(e)
            return {"error": self._sanitize_error(e)}

    async def send_human_gate_signal(
        self, workflow_id: str, node_id: str, approved: bool
    ) -> bool:
        """P2-9 fix: 向 Temporal workflow 发送 HumanGate signal。

        boundary-pinning §6.2 契约 5: feedback 走 Temporal HumanGateSignal。
        L2 RunWorkflowSpec 的 human_task 节点会 wait_condition 等待此 signal。

        Args:
            workflow_id: Temporal workflow ID
            node_id: 要确认的 human_task 节点 ID
            approved: True=通过, False=拒绝

        Returns:
            True=signal 发送成功, False=失败
        """
        try:
            client = await self._ensure_client()
            handle = client.get_workflow_handle(workflow_id)
            await handle.signal("human_gate", node_id, approved)
            return True
        except Exception as e:
            self._maybe_invalidate_client(e)
            return False

    async def cancel_workflow(self, workflow_id: str, reason: str = "user requested") -> bool:
        """取消一个正在运行的 Temporal workflow（terminate）。

        调用 Temporal Client 的 terminate() API，
        工作流收到终止信号后执行清理逻辑并退出。

        Args:
            workflow_id: 要取消的 Temporal workflow ID
            reason: 取消原因（记录在 Temporal history 中）

        Returns:
            True=成功, False=失败
        """
        try:
            client = await self._ensure_client()
            handle = client.get_workflow_handle(workflow_id)
            await handle.terminate(reason=reason)
            logger.info("Workflow %s terminated: %s", workflow_id, reason)
            return True
        except Exception as e:
            self._maybe_invalidate_client(e)
            logger.warning(
                "cancel_workflow failed for %s: %s",
                workflow_id, self._sanitize_error(e),
            )
            return False

    async def send_signal(
        self, workflow_id: str, signal_name: str, args: Any = None
    ) -> bool:
        """P1-6: 发送通用 Temporal signal — pause/resume/cancel_by_user。

        LLM control_workflow 工具调此方法控制正在执行的 workflow:
          - signal_name="pause"          → 暂停 workflow
          - signal_name="resume"         → 恢复暂停的 workflow
          - signal_name="cancel_by_user" → 用户取消 workflow

        Args:
            workflow_id: 目标 workflow ID
            signal_name: dag_runner_workflow.py 的 @workflow.signal 方法名
            args: signal 参数(无参 signal 传 None,python-client 接受空 args)

        Returns:
            True=成功, False=失败
        """
        try:
            client = await self._ensure_client()
            handle = client.get_workflow_handle(workflow_id)
            # temporalio python-client: signal 无参时传 args=()
            # 有参时传 args=(arg1, arg2, ...)
            if args is None:
                await handle.signal(signal_name)
            elif isinstance(args, (list, tuple)):
                await handle.signal(signal_name, *args)
            else:
                await handle.signal(signal_name, args)
            logger.info(
                "Signal '%s' sent to workflow %s", signal_name, workflow_id,
            )
            return True
        except Exception as e:
            self._maybe_invalidate_client(e)
            logger.warning(
                "send_signal failed for %s (signal=%s): %s",
                workflow_id, signal_name, self._sanitize_error(e),
            )
            return False

    def _spec_to_dict(self, spec: WorkflowSpec) -> dict[str, Any]:
        """WorkflowSpec → JSON 安全 dict（与 controlplane 的 workflow_spec_from_dict 对应）。

        P2-1 fix: 用 pydantic model_dump 替代手工字段序列化。
        原代码逐字段手写,WorkflowSpec 加字段时易遗漏同步(已导致 P0-1 类 bug)。
        model_dump 保证 DTO 字段全集自动序列化,与 dto_workflow.py SSOT 同步。
        """
        return spec.model_dump()


__all__ = ["TemporalWorkflowLaunchPort"]
