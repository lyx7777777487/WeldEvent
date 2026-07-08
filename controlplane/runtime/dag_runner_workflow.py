"""RunWorkflowSpec — Generic DAG Runner Workflow.

boundary-pinning §6.2: Temporal workflow 是 generic DAG runner，按 nodes 顺序
调度 Activity，每个 Activity 按 capability 调 ToolPool。

本 workflow 接收 WorkflowSpec dict（从 cognitiveplane bridge 层传入），按
depends_on 拓扑排序推进 nodes，每个 node 调 execute_node activity。

与 TemplateWorkflow 的区别：
  - TemplateWorkflow: 基于预定义 WorkflowTemplate FSM（control_points + transitions）
  - RunWorkflowSpec:  基于任意 WorkflowSpec DAG（nodes + depends_on 拓扑排序）

Source: boundary-pinning §6.2
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from controlplane.domain.workflow_spec import (
    WorkflowSpec,
    topological_sort,
    workflow_spec_from_dict,
)


@workflow.defn(name="RunWorkflowSpec")
class RunWorkflowSpec:
    """Generic DAG Runner — 按 WorkflowSpec.nodes 拓扑排序推进。

    Phase 2 实现：
      - 串行执行（同层无依赖节点也串行，Phase 4+ 加并行）
      - on_failure=abort → 立即终止 workflow
      - on_failure=escalate → 标记 node 失败，继续（Phase 4+ 接 HumanGateSignal）
      - on_failure=continue/retry → 当前等同 escalate
    """

    def __init__(self) -> None:
        self._spec: WorkflowSpec | None = None
        self._node_results: dict[str, dict[str, Any]] = {}
        # P2-5 fix: 拆分语义：
        #   _completed_nodes: 成功完成的节点（status=OK/MARGINAL）— 消费者据此判断成功
        #   _failed_nodes:    失败的节点（status=NG/ERROR 或被 HumanGate 拒绝）
        #   _processed_nodes: 已处理的节点（成功+失败），用于依赖检查放行下游
        self._completed_nodes: list[str] = []
        self._failed_nodes: list[str] = []
        self._processed_nodes: set[str] = set()
        # P0-2 fix: mock 节点单独跟踪 — 静默 mock OK 在工业质检是安全风险
        #   _mocked_nodes: 走 mock fallback 的节点(data.mock=True),即便 status=MARGINAL
        #   消费者(L1/用户)必须能识别"哪些节点没真执行",_build_result / query_status 暴露
        self._mocked_nodes: list[str] = []
        self._status: str = "RUNNING"
        # P2-10: human_task 等待人工确认的 signal 事件
        self._human_gate_approvals: dict[str, bool] = {}  # node_id → approved?
        self._human_gate_waiting: set[str] = set()
        # P1-4/P1-5: L2→L1 事件回传 + 智能暂停
        #   _event_meta: 从 spec.metadata 提取的 session_id/callback_url,供 emit 用
        #   _pause_signals: 用户通过 LLM pause_workflow 工具发来的暂停信号
        #     workflow_id → "paused" | "resumed" | "cancelled"
        self._event_meta: dict[str, str] = {}
        self._pause_state: str = "running"  # running | paused | cancelled

    @workflow.run
    async def run(self, workflow_spec_dict: dict[str, Any]) -> dict[str, Any]:
        """主入口 — 接收 WorkflowSpec dict，按拓扑排序执行 nodes。

        Args:
            workflow_spec_dict: WorkflowSpec 序列化 dict（来自 cognitiveplane
                bridge 层的 workflow_spec_to_dict()）

        Returns:
            workflow 执行结果，含 node_results / status / completed / failed
        """
        self._spec = workflow_spec_from_dict(workflow_spec_dict)

        # P1-4: 从 spec.metadata 提取 session_id + callback_url,供 emit_workflow_event 用
        # L1 launch_workflow 工具注入这两个字段,让 L2 能 HTTP POST 节点事件到 L1
        self._event_meta = {
            "session_id": str(self._spec.metadata.get("session_id", "unknown")),
            "callback_url": str(self._spec.metadata.get("callback_url", "")),
            "workflow_id": self._spec.workflow_id,
        }

        workflow.logger.info(
            "RunWorkflowSpec started: workflow_id=%s objective=%s nodes=%d",
            self._spec.workflow_id,
            self._spec.objective,
            len(self._spec.nodes),
        )

        # P1-4: emit workflow_started 事件到 L1
        await self._emit_event("workflow_started")

        # 拓扑排序（若环或引用错误会抛 ValueError → workflow fail）
        try:
            sorted_nodes = topological_sort(self._spec.nodes)
        except ValueError as e:
            self._status = "FAILED"
            workflow.logger.error("Topological sort failed: %s", e)
            await self._emit_event("workflow_failed", error=str(e))
            return self._build_result(error=str(e))

        # 串行执行每个 node
        for node in sorted_nodes:
            if self._status != "RUNNING":
                break

            # P1-5: 智能暂停 — 检查用户是否发了 pause signal
            if self._pause_state == "cancelled":
                self._status = "FAILED"
                await self._emit_event("workflow_failed", error="cancelled by user")
                return self._build_result(error="cancelled by user")
            # 暂停状态:等待 resume signal(最多 30 分钟,超时自动取消)
            while self._pause_state == "paused":
                await self._emit_event("workflow_paused")
                ok = await workflow.wait_condition(
                    lambda: self._pause_state != "paused",
                    timeout=timedelta(minutes=30),
                )
                if not ok:
                    self._status = "FAILED"
                    await self._emit_event("workflow_failed", error="pause timeout (30m)")
                    return self._build_result(error="pause timeout")
                await self._emit_event("workflow_resumed")

            workflow.logger.info(
                "Executing node: %s (type=%s capability=%s depends_on=%s)",
                node.node_id, node.type, node.capability, node.depends_on,
            )

            # P1-4: emit node_start 事件到 L1
            await self._emit_event("node_start", node_id=node.node_id)

            # 检查依赖是否都已处理（成功或失败均放行 — on_failure=escalate/continue 语义）
            # P2-5 fix: 用 _processed_nodes 而非 _completed_nodes，避免失败节点的下游被误判依赖缺失
            for dep_id in node.depends_on:
                if dep_id not in self._processed_nodes:
                    self._status = "FAILED"
                    err = f"Dependency '{dep_id}' not processed for node '{node.node_id}'"
                    workflow.logger.error(err)
                    return self._build_result(error=err)

            # P2-10: human_task 暂停等待 HumanGateSignal（§6.2 契约 5）
            if node.type == "human_task":
                workflow.logger.info(
                    "Node %s is human_task — waiting for HumanGate signal",
                    node.node_id,
                )
                # P2-1 fix: 标记正在等待，让 query_status 能看到 pending 节点
                self._human_gate_waiting.add(node.node_id)
                # P1-1a fix: wait_condition 超时返回 False（不抛异常）
                # 必须检查返回值判断是否超时
                ok = await workflow.wait_condition(
                    lambda: node.node_id in self._human_gate_approvals,
                    timeout=timedelta(minutes=30),
                )
                # P2-1 fix: 离开等待状态（无论成功/超时/拒绝）
                self._human_gate_waiting.discard(node.node_id)
                if not ok:
                    # 超时 → 标记失败并终止（加入 _failed_nodes 避免 query_status 不一致）
                    self._failed_nodes.append(node.node_id)
                    self._status = "FAILED"
                    err = f"HumanGate timeout for node '{node.node_id}' (waited 30m)"
                    workflow.logger.error(err)
                    return self._build_result(error=err)
                # signal 到达，从 dict 取实际审批值（可能是 True=通过 或 False=拒绝）
                approved = self._human_gate_approvals.get(node.node_id, False)
                if not approved:
                    # 被拒绝 → 走 on_failure 决策
                    result = {
                        "status": "NG",
                        "data": {"node_id": node.node_id, "rejected_by": "human_gate"},
                        "error": "HumanGate rejected",
                    }
                    self._node_results[node.node_id] = result
                    self._failed_nodes.append(node.node_id)
                    self._processed_nodes.add(node.node_id)
                    if node.on_failure == "abort":
                        self._status = "FAILED"
                        workflow.logger.error(
                            "Node %s rejected by HumanGate, on_failure=abort → workflow FAILED",
                            node.node_id,
                        )
                        break
                    # escalate/continue/retry → 继续下游（标记已处理放行依赖）
                    continue
                # approved → 继续 execute_node activity（记录审批通过）
                workflow.logger.info(
                    "Node %s approved by HumanGate — proceeding to activity",
                    node.node_id,
                )

            # 调 execute_node activity
            # P4 fix: 注入 workflow_id 供 L3 activity 读写 WeldMap（IQA 写 quality，PPA 读 quality）
            # P2-6 fix: human_task 已通过 HumanGate 后注入 gate_approved=True，
            # 让 execute_node activity 层校验通过（activity 层双重保险防 workflow bug）
            # P5 fix: 注入 dependency_results，让下游节点可直接获取上游节点结果，
            # 不再仅依赖 WeldMap（InMemory 实现重启即丢失，生产环境 Redis 也可能瞬时不可用）
            dep_results: dict[str, dict[str, Any]] = {}
            for dep_id in node.depends_on:
                if dep_id in self._node_results:
                    dep_results[dep_id] = self._node_results[dep_id]
            node_input = {
                "node_id": node.node_id,
                "type": node.type,
                "capability": node.capability,
                "input": node.input,
                "workflow_id": self._spec.workflow_id if self._spec else "",
                "dependency_results": dep_results,
                "caller_context": {
                    "caller_type": node.caller_context.caller_type,
                    "case_id": node.caller_context.case_id,
                    "node_id": node.caller_context.node_id,
                    "session_id": node.caller_context.session_id,
                },
            }
            if node.type == "human_task":
                node_input["gate_approved"] = self._human_gate_approvals.get(node.node_id, False)

            try:
                # P1-1 fix: 加 schedule_to_start_timeout（worker 缺席时快速失败）
                # 和 schedule_to_close_timeout（总超时上限）
                result = await workflow.execute_activity(
                    "execute_node",
                    node_input,
                    # P2-3 fix: 跨层超时统一设计 — 端到端 SLA:单图质检 ≤ 90s
                    # 反推:L1 工具 90s > L2 activity 60s > L3 MLLM 30s
                    # 原 start_to_close=30s 过紧(L1 注释说 vision API ~35s × 2),
                    # 真实视觉调用会被 L2 超时杀掉。改为 60s 留足 L3 MLLM(30s)+CV 规则时间
                    schedule_to_start_timeout=timedelta(seconds=30),
                    start_to_close_timeout=timedelta(seconds=60),
                    schedule_to_close_timeout=timedelta(minutes=3),
                    retry_policy=RetryPolicy(
                        maximum_attempts=3,
                        initial_interval=timedelta(seconds=1),
                        backoff_coefficient=2.0,
                    ),
                )
            except Exception as e:
                # P2-1 fix: 异常兜底用大写 ERROR（与 ActivityStatus.ERROR.value 一致）
                result = {
                    "status": "ERROR",
                    "data": {},
                    "error": f"Activity execution failed: {e}",
                }

            self._node_results[node.node_id] = result

            # P1-4: emit node_end 事件到 L1(含节点状态+结果摘要)
            # 截断 node_data 避免 L2→L1 HTTP 载荷过大(只取关键字段)
            emit_data = {}
            if isinstance(result.get("data"), dict):
                emit_data = {
                    k: v for k, v in result["data"].items()
                    if k not in ("image_b64", "image_paths", "raw_image") and not str(v).startswith("data:")
                }
            await self._emit_event(
                "node_end",
                node_id=node.node_id,
                node_status=result.get("status", "ERROR"),
                node_data=emit_data,
                error=result.get("error"),
            )

            # P0-2 fix: 识别 mock 节点(data.mock=True),单独跟踪
            # mock 节点即便 status=MARGINAL 也要让消费者能区分"未真执行"
            result_data = result.get("data") or {}
            if isinstance(result_data, dict) and result_data.get("mock") is True:
                self._mocked_nodes.append(node.node_id)
                workflow.logger.warning(
                    "Node %s executed as MOCK (no real L3 activity). "
                    "Result MUST NOT be treated as real pass.",
                    node.node_id,
                )

            # 按 status 决定流转（统一大写比较，与 ActivityStatus 枚举值一致）
            # P2-5 fix: _completed_nodes 只放成功节点；失败的进 _failed_nodes + _processed_nodes
            status = str(result.get("status", "ERROR")).upper()
            if status == "OK":
                self._completed_nodes.append(node.node_id)
                self._processed_nodes.add(node.node_id)
            elif status == "MARGINAL":
                # marginal 视为完成（有保留意见但仍成功）
                self._completed_nodes.append(node.node_id)
                self._processed_nodes.add(node.node_id)
            else:
                # ng / error — 按 on_failure 决策
                self._failed_nodes.append(node.node_id)
                self._processed_nodes.add(node.node_id)
                if node.on_failure == "abort":
                    self._status = "FAILED"
                    workflow.logger.error(
                        "Node %s failed, on_failure=abort → workflow FAILED",
                        node.node_id,
                    )
                    break
                # escalate / continue / retry → 继续执行下游（已加入 _processed_nodes 放行依赖）
                workflow.logger.warning(
                    "Node %s failed (status=%s), on_failure=%s → continue",
                    node.node_id, status, node.on_failure,
                )

        if self._status == "RUNNING":
            self._status = "COMPLETED"

        workflow.logger.info(
            "RunWorkflowSpec finished: status=%s completed=%d failed=%d",
            self._status, len(self._completed_nodes), len(self._failed_nodes),
        )

        # P1-4: emit workflow_completed/failed 事件到 L1
        if self._status == "COMPLETED":
            await self._emit_event("workflow_completed")
        else:
            await self._emit_event("workflow_failed", error=f"status={self._status}")

        return self._build_result()

    async def _emit_event(
        self,
        event_type: str,
        node_id: str | None = None,
        node_status: str | None = None,
        node_data: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """P1-4: 通过 emit_workflow_event activity 向 L1 推送事件。

        Temporal workflow 不能直接调 HTTP(sandbox 限制),必须通过 activity。
        用短超时 + 0 次重试,失败不阻断 workflow。
        callback_url 缺失时跳过(开发环境无 L1 回调时不报错)。
        """
        if not self._event_meta.get("callback_url"):
            return  # 无回调 URL — 静默跳过
        try:
            await workflow.execute_activity(
                "emit_workflow_event",
                {
                    **self._event_meta,
                    "event_type": event_type,
                    "node_id": node_id,
                    "node_status": node_status,
                    "node_data": node_data,
                    "error": error,
                },
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(
                    maximum_attempts=1,  # 事件回传不重试
                    initial_interval=timedelta(seconds=1),
                ),
            )
        except Exception as e:
            # 事件回传失败不阻断 workflow
            workflow.logger.warning(
                "emit_workflow_event failed (non-blocking): %s: %s",
                type(e).__name__, e,
            )

    @workflow.query
    def query_status(self) -> dict[str, Any]:
        """查询 workflow 当前状态。"""
        return {
            "status": self._status,
            "workflow_id": self._spec.workflow_id if self._spec else None,
            "completed_nodes": list(self._completed_nodes),
            "failed_nodes": list(self._failed_nodes),
            # P0-2 fix: 暴露 mock 节点,让 L1/用户识别"哪些没真执行"
            "mocked_nodes": list(self._mocked_nodes),
            "node_results": dict(self._node_results),
            "human_gate_pending": sorted(self._human_gate_waiting),
        }

    @workflow.signal
    def human_gate(self, node_id: str, approved: bool) -> None:
        """P2-10: 人工确认信号 — human_task 节点暂停等待此信号。

        boundary-pinning §6.2 契约 5: feedback 走 Temporal HumanGateSignal。
        L1 AgentLoop 或人工操作员通过此 signal 传递审批结果。

        Args:
            node_id: 要确认的 human_task 节点 ID
            approved: True=通过, False=拒绝（拒绝 → on_failure 决策）
        """
        # 内联校验（temporalio 1.27.2 不支持 @signal.validator）
        if not isinstance(node_id, str) or not node_id:
            workflow.logger.error("human_gate: invalid node_id=%s", node_id)
            return
        if not isinstance(approved, bool):
            workflow.logger.error("human_gate: invalid approved=%s", approved)
            return
        self._human_gate_approvals[node_id] = approved
        workflow.logger.info(
            "HumanGate signal received: node=%s approved=%s",
            node_id, approved,
        )

    @workflow.signal
    def pause(self) -> None:
        """P1-5: 暂停 workflow — 用户通过 LLM pause_workflow 工具发此 signal。

        workflow 在下一个节点执行前检查 _pause_state,若为 "paused" 则
        wait_condition 等待 resume signal。最多等 30 分钟,超时自动取消。
        """
        if self._pause_state == "running":
            self._pause_state = "paused"
            workflow.logger.info("Workflow paused by user signal")

    @workflow.signal
    def resume(self) -> None:
        """P1-5: 恢复暂停的 workflow。"""
        if self._pause_state == "paused":
            self._pause_state = "running"
            workflow.logger.info("Workflow resumed by user signal")

    @workflow.signal
    def cancel_by_user(self) -> None:
        """P1-5: 用户取消 workflow — 与 Temporal 内置 cancel 区分(那是系统级)。

        workflow 检测到 _pause_state="cancelled" 后立即终止,返回 FAILED。
        """
        self._pause_state = "cancelled"
        workflow.logger.info("Workflow cancelled by user signal")

    def _build_result(self, error: str | None = None) -> dict[str, Any]:
        """构建 workflow 返回值。"""
        result: dict[str, Any] = {
            "workflow_id": self._spec.workflow_id if self._spec else None,
            "objective": self._spec.objective if self._spec else None,
            "status": self._status,
            "completed_nodes": list(self._completed_nodes),
            "failed_nodes": list(self._failed_nodes),
            # P0-2 fix: 暴露 mock 节点列表 — 静默 mock 在工业质检是安全风险
            # 消费者(L1 launch_workflow / 用户)必须能识别"这些节点没真执行"
            "mocked_nodes": list(self._mocked_nodes),
            "node_results": dict(self._node_results),
        }
        if error:
            result["error"] = error
        return result
