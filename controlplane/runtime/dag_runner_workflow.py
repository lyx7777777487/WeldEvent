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
        self._status: str = "RUNNING"
        # P2-10: human_task 等待人工确认的 signal 事件
        self._human_gate_approvals: dict[str, bool] = {}  # node_id → approved?
        self._human_gate_waiting: set[str] = set()

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

        workflow.logger.info(
            "RunWorkflowSpec started: workflow_id=%s objective=%s nodes=%d",
            self._spec.workflow_id,
            self._spec.objective,
            len(self._spec.nodes),
        )

        # 拓扑排序（若环或引用错误会抛 ValueError → workflow fail）
        try:
            sorted_nodes = topological_sort(self._spec.nodes)
        except ValueError as e:
            self._status = "FAILED"
            workflow.logger.error("Topological sort failed: %s", e)
            return self._build_result(error=str(e))

        # 串行执行每个 node
        for node in sorted_nodes:
            if self._status != "RUNNING":
                break

            workflow.logger.info(
                "Executing node: %s (type=%s capability=%s depends_on=%s)",
                node.node_id, node.type, node.capability, node.depends_on,
            )

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
                    timeout=timedelta(hours=1),
                )
                # P2-1 fix: 离开等待状态（无论成功/超时/拒绝）
                self._human_gate_waiting.discard(node.node_id)
                if not ok:
                    # 超时 → 标记失败并终止（加入 _failed_nodes 避免 query_status 不一致）
                    self._failed_nodes.append(node.node_id)
                    self._status = "FAILED"
                    err = f"HumanGate timeout for node '{node.node_id}' (waited 1h)"
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
            node_input = {
                "node_id": node.node_id,
                "type": node.type,
                "capability": node.capability,
                "input": node.input,
                "workflow_id": self._spec.workflow_id if self._spec else "",
                "caller_context": {
                    "caller_type": node.caller_context.caller_type,
                    "case_id": node.caller_context.case_id,
                    "node_id": node.caller_context.node_id,
                    "session_id": node.caller_context.session_id,
                },
            }
            if node.type == "human_task":
                node_input["gate_approved"] = node.node_id in self._human_gate_approvals and \
                    self._human_gate_approvals.get(node.node_id, False)

            try:
                # P1-1 fix: 加 schedule_to_start_timeout（worker 缺席时快速失败）
                # 和 schedule_to_close_timeout（总超时上限）
                result = await workflow.execute_activity(
                    "execute_node",
                    node_input,
                    schedule_to_start_timeout=timedelta(seconds=30),
                    start_to_close_timeout=timedelta(seconds=30),
                    schedule_to_close_timeout=timedelta(minutes=2),
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

        return self._build_result()

    @workflow.query
    def query_status(self) -> dict[str, Any]:
        """查询 workflow 当前状态。"""
        return {
            "status": self._status,
            "workflow_id": self._spec.workflow_id if self._spec else None,
            "completed_nodes": list(self._completed_nodes),
            "failed_nodes": list(self._failed_nodes),
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
        self._human_gate_approvals[node_id] = approved
        workflow.logger.info(
            "HumanGate signal received: node=%s approved=%s",
            node_id, approved,
        )

    def _build_result(self, error: str | None = None) -> dict[str, Any]:
        """构建 workflow 返回值。"""
        result: dict[str, Any] = {
            "workflow_id": self._spec.workflow_id if self._spec else None,
            "objective": self._spec.objective if self._spec else None,
            "status": self._status,
            "completed_nodes": list(self._completed_nodes),
            "failed_nodes": list(self._failed_nodes),
            "node_results": dict(self._node_results),
        }
        if error:
            result["error"] = error
        return result
