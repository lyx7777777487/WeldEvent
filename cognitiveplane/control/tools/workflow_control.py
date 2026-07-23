"""WorkflowControlTool — LLM 工具：查询/暂停/恢复/取消正在运行的工作流。

P1-6: 让 LLM 能通过自然语言介入正在执行的工作流。
  - 用户问"工作流进行到哪了" → LLM 调 query_workflow_status
  - 用户说"暂停一下" → LLM 调 pause_workflow
  - 用户说"继续" → LLM 调 resume_workflow
  - 用户说"取消工作流" → LLM 调 cancel_workflow

设计：单工具 + action 参数（避免 4 个独立工具膨胀 LLM 工具列表）。
query 优先从 L1 WorkflowEventBus 缓存读（快,实时更新）,
fallback 到 L2 Temporal query_status（权威,但跨进程慢）。

Source: 用户需求"过程逐步展示+每步可介入"
"""
from __future__ import annotations

import logging
from typing import Any

from cognitiveplane.control.tools import BrainTool, ToolResult

logger = logging.getLogger("workflow_control_tool")


class WorkflowControlTool(BrainTool):
    """工作流控制工具 — query / pause / resume / cancel / modify / rework_node / inject_context 等。"""

    phase = 3  # 与 launch_workflow 同阶段,工作流启动后才有意义

    def __init__(self, deps, approval_store=None) -> None:
        self._deps = deps
        # 第二档 governance action (revoke/override/case_correction)
        # 需强制确认门 - 责任留痕. None 时降级为非阻塞 (仅记录 warning).
        self._approval_store = approval_store

    @property
    def name(self) -> str:
        return "control_workflow"

    @property
    def description(self) -> str:
        desc = [
            "控制正在运行的工作流。",
            "**基础**: query(查进度), pause(暂停), resume(恢复), cancel(取消)。",
            "**回溯**: rework_node(回退节点+下游重跑), inject_context(注入补充信息)。",
            "**拓扑**: modify(改工作流结构, cancel+relaunch)。",
            "**批次**: batch_hold(冻结批次), release_hold(释放冻结), rework_batch(重做冻结批次), quarantine_batch(隔离批次)。",
            "**暂停**: pause_scope(分级暂停), resume_scope(恢复分级暂停)。",
            "**人工**: human_review(5向审查: approve/rework/modify/reject/escalate)。",
            "**标注**: relabel_request(重新标注，标签错但结果对时回标注环节)。",
            "**审查**: delegate_review(转派审查人), revoke_approval(撤回已批准, 需确认), ground_truth_override(人工强制覆盖, 需确认)。",
            "**标准**: standard_update(标准库版本升级), case_library_correction(案例库纠错, 需确认)。",
            "**用法**: 传 action + workflow_id。rework_node 需传 node_id。human_review 需传 node_id + decision。",
        ]
        return "\n".join(desc)
        return "\n".join(desc)

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["query", "pause", "resume", "cancel", "modify", "rework_node", "inject_context",
                             "pause_scope", "resume_scope",
                             "batch_hold", "release_hold", "rework_batch", "quarantine_batch",
                             "relabel_request",
                             "delegate_review", "standard_update",
                             "human_review",
                             "revoke_approval", "ground_truth_override",
                             "case_library_correction"],
                    "description": "Control action. query/pause/resume/cancel/rework_node/inject_context/modify. resume_scope(恢复分级暂停). release_hold(释放批次冻结)/rework_batch(重做冻结批次)/quarantine_batch(隔离冻结批次). human_review: 5-way human decision (approve/rework/modify/reject/escalate) — must include 'decision' and 'node_id'. revoke_approval/ground_truth_override/case_library_correction: 需确认门.",
                },
                "workflow_id": {
                    "type": "string",
                    "description": "Workflow ID from launch_workflow output",
                },
                "node_id": {
                    "type": "string",
                    "description": "For rework_node: the node_id to rewind to (e.g. 'iqa_check'). The node and all downstream will be re-executed.",
                },
                "key": {
                    "type": "string",
                    "description": "For inject_context: context key name (e.g. 'parameter_adjustment', 'standard_override').",
                },
                "value": {
                    "type": "string",
                    "description": "For inject_context: context value (free text or JSON).",
                },
                "new_spec": {
                    "type": "object",
                    "description": "For modify action: new WorkflowSpec with updated nodes topology",
                },
                "batch_id": {
                    "type": "string",
                    "description": "For batch_hold: 批次 ID (焊检域 batch=case_id). 用户说'批次X'/'扣住X'时填此字段。",
                },
                "scope_type": {
                    "type": "string",
                    "enum": ["workflow", "station", "batch"],
                    "description": "For pause_scope/resume_scope: workflow/station/batch。station 时 scope_id=node_id; batch 时 scope_id=batch_id(=case_id)。",
                },
                "scope_id": {
                    "type": "string",
                    "description": "For pause_scope/resume_scope: 暂停范围 ID (node_id 或 batch_id 或 workflow_id，取决于 scope_type)。",
                },
                "reason": {
                    "type": "string",
                    "description": "干预原因 (必填). 如'可疑批次扣留'/'标签错误'/'标准升级'等。",
                },
                "inspector_id": {
                    "type": "string",
                    "description": "For ground_truth_override: 质检员 ID (责任人)。",
                },
                "forced_verdict": {
                    "type": "string",
                    "description": "For ground_truth_override: 人工强制裁定的结果 (如 'OK'/'NG'/'MARGINAL')。",
                },
                "original_label": {
                    "type": "string",
                    "description": "For relabel_request: 原标签。",
                },
                "corrected_label": {
                    "type": "string",
                    "description": "For relabel_request: 修正后的标签。",
                },
                "new_reviewer_id": {
                    "type": "string",
                    "description": "For delegate_review: 新审查人 ID。",
                },
                "standard_id": {
                    "type": "string",
                    "description": "For standard_update: 标准 ID (如 'AWS_D1.1')。",
                },
                "old_version": {
                    "type": "string",
                    "description": "For standard_update: 旧版本号 (如 '2020')。",
                },
                "new_version": {
                    "type": "string",
                    "description": "For standard_update: 新版本号 (如 '2025')。",
                },
                "case_id": {
                    "type": "string",
                    "description": "For case_library_correction: 案例库中的案例 ID。",
                },
                "error_type": {
                    "type": "string",
                    "description": "For case_library_correction: 错误类型 (如 'mislabel'/'wrong_conclusion')。",
                },
                "correction": {
                    "type": "string",
                    "description": "For case_library_correction: 修正内容。",
                },
            },
            "required": ["action", "workflow_id"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        action = kwargs.get("action")
        workflow_id = kwargs.get("workflow_id")
        _GOVERNANCE = ("pause_scope", "resume_scope", "batch_hold", "release_hold",
                       "rework_batch", "quarantine_batch",
                       "relabel_request",
                       "delegate_review", "standard_update",
                       "human_review",
                       "revoke_approval", "ground_truth_override",
                       "case_library_correction")
        if not action or action not in ("query", "pause", "resume", "cancel", "modify", "rework_node", "inject_context", *_GOVERNANCE):
            return ToolResult(error=f"invalid action: {action}")
        if not workflow_id:
            return ToolResult(error="workflow_id is required")

        # query: 优先从 L1 WorkflowEventBus 缓存读（快）
        if action == "query":
            return await self._query(workflow_id)
        if action in _GOVERNANCE:
            return await self._send_governance(action, workflow_id, kwargs)
        if action == "modify":
            new_spec = kwargs.get("new_spec")
            if not new_spec:
                return ToolResult(error="modify requires new_spec parameter")
            return await self._modify_workflow(workflow_id, new_spec)
        if action == "rework_node":
            node_id = kwargs.get("node_id", "")
            return await self._rework_node(workflow_id, node_id)
        if action == "inject_context":
            key = kwargs.get("key", "")
            value = kwargs.get("value", "")
            return await self._inject_context_to_workflow(workflow_id, key, value)
        # pause/resume/cancel: 调 Temporal signal
        return await self._send_signal(action, workflow_id)

    async def _rework_node(self, workflow_id: str, node_id: str, event_callback: Any = None) -> ToolResult:
        """回溯到指定节点重新执行 — 发 rework_node signal 到 L2。

        L2 rework_node BFS 找到所有下游节点，清空结果，标记到 _rework_nodes。
        主循环再次到达这些节点时重新执行。工作流不取消不重建。
        """
        if not node_id:
            return ToolResult(error="rework_node requires node_id parameter")
        connector = self._deps.bridge.event_connector if self._deps and self._deps.bridge else None
        if connector is None or not hasattr(connector, "send_signal"):
            return ToolResult(error="bridge 未配置 send_signal，无法发 rework_node signal")
        try:
            ok = await connector.send_signal(workflow_id, "rework_node", [node_id])
        except Exception as e:
            return ToolResult(error=f"发送 rework_node 失败：{e}。可以重试。")
        if not ok:
            return ToolResult(error=f"发送 rework_node 失败（bridge 返回 False）。请检查 workflow 是否还在运行后重试。")
        return ToolResult(output={
            "action": "rework_node",
            "workflow_id": workflow_id,
            "node_id": node_id,
            "ok": True,
            "message": f"已回溯到节点 '{node_id}'，该节点及下游 {node_id}+downstream 将在下一轮重新执行。工作流未取消。",
        })

    async def _inject_context_to_workflow(self, workflow_id: str, key: str, value: str) -> ToolResult:
        """向运行中的工作流注入补充信息 — 发 inject_context signal 到 L2。

        已执行节点不受影响（不回退），未执行节点自动使用新上下文。
        """
        if not key:
            return ToolResult(error="inject_context requires key parameter")
        connector = self._deps.bridge.event_connector if self._deps and self._deps.bridge else None
        if connector is None or not hasattr(connector, "send_signal"):
            return ToolResult(error="bridge 未配置 send_signal，无法发 inject_context signal")
        try:
            ok = await connector.send_signal(workflow_id, "inject_context", [key, value])
        except Exception as e:
            return ToolResult(error=f"发送 inject_context 失败：{e}。可以重试。")
        if not ok:
            return ToolResult(error=f"发送 inject_context 失败（bridge 返回 False）。请检查 workflow 是否还在运行后重试。")
        return ToolResult(output={
            "action": "inject_context",
            "workflow_id": workflow_id,
            "key": key,
            "ok": True,
            "message": f"已将 {key} 注入工作流上下文，未执行节点将用新值。已执行节点不受影响。",
        })

    async def _query(self, workflow_id: str) -> ToolResult:
        """查询 workflow 状态 — L1 缓存优先,Temporal fallback。"""
        # 1. 优先从 L1 WorkflowEventBus 缓存读（实时更新,无需跨进程）
        try:
            from cognitiveplane.interaction.workflow_events import get_workflow_event_bus
            bus = get_workflow_event_bus()
            cached = bus.get_workflow_status(workflow_id)
            if cached:
                # 补充 L2 Temporal 权威状态(若 L1 bridge 可用)
                temporal_status = await self._temporal_query(workflow_id)
                if temporal_status:
                    # 合并:Temporal 的 status + event bus 的 nodes 细节
                    cached["temporal_status"] = temporal_status.get("status")
                    cached["completed_nodes"] = temporal_status.get("completed_nodes", cached.get("completed_nodes", []))
                    cached["failed_nodes"] = temporal_status.get("failed_nodes", cached.get("failed_nodes", []))
                return ToolResult(output={
                    "status": cached.get("status", "UNKNOWN"),
                    "workflow_id": workflow_id,
                    "nodes": cached.get("nodes", {}),
                    "completed_nodes": cached.get("completed_nodes", []),
                    "failed_nodes": cached.get("failed_nodes", []),
                    "temporal_status": cached.get("temporal_status"),
                    "source": "L1_event_bus" + ("+L2_temporal" if temporal_status else ""),
                })
        except Exception as e:
            logger.warning("query_workflow_status: L1 cache read failed: %s", e)

        # 2. Fallback: 直接调 L2 Temporal query_status
        temporal_status = await self._temporal_query(workflow_id)
        if temporal_status:
            return ToolResult(output={
                "status": temporal_status.get("status", "UNKNOWN"),
                "workflow_id": workflow_id,
                "completed_nodes": temporal_status.get("completed_nodes", []),
                "failed_nodes": temporal_status.get("failed_nodes", []),
                "node_results": temporal_status.get("node_results", {}),
                "source": "L2_temporal",
            })
        return ToolResult(error=f"workflow {workflow_id} not found in L1 cache or L2 Temporal")

    async def _temporal_query(self, workflow_id: str) -> dict[str, Any] | None:
        """调 L2 Temporal query_status — 跨进程,慢但权威。"""
        connector = self._deps.bridge.event_connector if self._deps and self._deps.bridge else None
        if connector is None:
            return None
        try:
            return await connector.query_status(workflow_id)
        except Exception as e:
            logger.warning("temporal query_status failed: %s", e)
            return None

    # ── Op-新: 7 个 governance action 接入 (经 batch_signals signal 转发) ──
    # 设计: 所有 governance 都走 batch_signals(workflow_id, [list[dict]]) 入口,
    # 复用 dag_runner_workflow._apply_pending_signals router (已验证).
    # 避免给每个 governance signal 单独处理位置参数 (Temporal signal 是位置参数,
    # 而 governance 多是关键字参数, batch_signals 收 list[dict] 最干净).
    #
    # 用户体验保证:
    #   - 失败明确报错 + 提示重试 (不转圈无反应)
    #   - 第二档 (revoke/override/case_correction) 发 signal 前强制确认门,
    #     责任留痕 (审计记录用户显式确认, 而非 LLM 理解)
    _GOVERNANCE_SPEC = {
        "pause_scope": {
            "sig_type": "pause_scope",
            "params": ["scope_type", "scope_id", "reason", "initiator"],
            "human": "已暂停{scope_type} {scope_id}，其余继续执行",
            "tier2": False,
        },
        "batch_hold": {
            "sig_type": "batch_hold",
            "params": ["batch_id", "reason", "evidence", "node_ids"],
            "human": "已扣留批次 {batch_id}，其他批次继续，待你决定释放/重做/隔离",
            "tier2": False,
        },
        "release_hold": {
            "sig_type": "release_hold",
            "params": ["batch_id"],
            "human": "已释放批次 {batch_id}，从 HELD 恢复为 COMMITTED",
            "tier2": False,
        },
        "rework_batch": {
            "sig_type": "rework_batch",
            "params": ["batch_id", "reason"],
            "human": "已重做批次 {batch_id}：HELD → PREPARING，下游节点将重新执行",
            "tier2": False,
        },
        "quarantine_batch": {
            "sig_type": "quarantine_batch",
            "params": ["batch_id", "reason"],
            "human": "已隔离批次 {batch_id}：HELD → FAILED，永久隔离",
            "tier2": True,
            "confirm_q": "隔离批次 {batch_id}？此批次将被永久标记为 FAILED。",
        },
        "resume_scope": {
            "sig_type": "resume_scope",
            "params": ["scope_type", "scope_id"],
            "human": "已恢复 {scope_type} {scope_id} 的执行",
            "tier2": False,
        },
        "human_review": {
            "sig_type": "human_review",
            "params": ["node_id", "decision", "overrides", "rationale"],
            "human": "已向节点 {node_id} 发送人工审查决议：{decision}",
            "tier2": False,
        },
        "relabel_request": {
            "sig_type": "relabel_request",
            "params": ["node_id", "artifact_id", "original_label", "corrected_label", "reason"],
            "human": "已发起重新标注：{original_label} → {corrected_label}，只回标注环节，结果保留",
            "tier2": False,
        },
        "delegate_review": {
            "sig_type": "delegate_review",
            "params": ["target", "new_reviewer_id", "reason", "is_escalation"],
            "human": "已把 {target} 的审查转给 {new_reviewer_id}",
            "tier2": False,
        },
        "standard_update": {
            "sig_type": "standard_update",
            "params": ["standard_id", "old_version", "new_version", "effective_date", "diff"],
            "human": "已登记标准更新 {standard_id}: {old_version} → {new_version}，历史判定标记待复查",
            "tier2": False,
        },
        "revoke_approval": {
            "sig_type": "revoke_approval",
            "params": ["node_id", "revoker_id", "reason", "artifact_status"],
            "human": "已撤回节点 {node_id} 的已批准判定，下游受影响节点将重跑",
            "tier2": True,
            "confirm_q": "撤回节点 {node_id} 的已批准判定？该结果可能已提交，撤回后下游节点将重跑。",
        },
        "ground_truth_override": {
            "sig_type": "ground_truth_override",
            "params": ["node_id", "inspector_id", "forced_verdict", "reason", "evidence"],
            "human": "已用人工裁决覆盖节点 {node_id}：机器结果保留供校准，{forced_verdict} 实际生效（责任：{inspector_id}）",
            "tier2": True,
            "confirm_q": "用人工裁决覆盖节点 {node_id} 的机器结果？设为 {forced_verdict}，由 {inspector_id} 承担责任，机器结果保留供校准。",
        },
        "case_library_correction": {
            "sig_type": "case_library_correction",
            "params": ["case_id", "error_type", "correction"],
            "human": "已纠错案例 {case_id}，未来所有引用此案例的工作流受影响",
            "tier2": True,
            "confirm_q": "纠正案例库中的案例 {case_id}？此操作不可逆，且会波及未来所有引用此案例的工作流。",
        },
    }

    async def _send_governance(
        self, action: str, workflow_id: str, kwargs: dict,
        event_callback: Any = None,
    ) -> ToolResult:
        """governance action -> batch_signals signal -> workflow router.

        第二档 action (revoke/override/case_correction) 先弹确认卡, 用户显式
        确认后才发 signal; 拒绝则不发. 保证责任留痕可审计.
        """
        spec = self._GOVERNANCE_SPEC.get(action)
        if spec is None:
            return ToolResult(error=f"未知 governance action: {action}")

        # 1) 收集参数 (按 spec.params 取, 缺失给默认)
        sig_payload: dict[str, Any] = {"type": spec["sig_type"]}
        for p_name in spec["params"]:
            val = kwargs.get(p_name)
            if val is not None:
                sig_payload[p_name] = val
        # 必填校验: batch_id/node_id/case_id 等关键标识不能缺
        id_field = next((k for k in ("node_id", "batch_id", "case_id", "scope_id", "target", "standard_id")
                         if k in spec["params"]), None)
        if id_field and id_field not in sig_payload:
            return ToolResult(error=f"缺少必填参数 {id_field}（{action} 需要）")

        # 2) 第二档: 强制确认门 (用户显式确认才发 signal)
        if spec["tier2"]:
            confirmed = await self._confirm_gate(action, sig_payload, spec, event_callback)
            if not confirmed:
                return ToolResult(output={
                    "action": action, "workflow_id": workflow_id, "ok": False,
                    "message": "用户未确认，未执行 " + action,
                })
            # 确认通过: 在 sig_payload 记录确认事实, 责任留痕
            sig_payload["confirmed_by_user"] = True

        # 3) 经 batch_signals signal 发到 workflow (复用已验证 router)
        connector = (self._deps.bridge.event_connector
                     if self._deps and self._deps.bridge else None)
        if connector is None or not hasattr(connector, "send_signal"):
            return ToolResult(error="bridge 未配置 send_signal，无法发 governance signal")

        try:
            ok = await connector.send_signal(
                workflow_id, "batch_signals", [[sig_payload]],
            )
        except Exception as e:
            return ToolResult(error=f"发送 {action} 失败：{e}。可以重试。")

        if not ok:
            return ToolResult(error=f"发送 {action} 失败（bridge 返回 False）。请检查 workflow 是否还在运行后重试。")

        # 4) 人话反馈 (不暴露内部术语)
        try:
            human_msg = spec["human"].format(**sig_payload)
        except Exception:
            human_msg = f"已执行 {action}"
        return ToolResult(output={
            "action": action, "workflow_id": workflow_id, "ok": True,
            "message": human_msg,
        })

    async def _confirm_gate(
        self, action: str, payload: dict, spec: dict,
        event_callback: Any = None,
    ) -> bool:
        """第二档确认门 - 复用 ApprovalStore 阻塞等用户在卡片上确认.

        用户体验: 用人话问 (不暴露 sig_type/COMMITTED 等内部词),
        选项 = 确认执行 / 取消. 用户点确认才返回 True.
        approval_store 未配置时降级为非阻塞: 记 warning 但放行 (避免卡死 agent).
        """
        if self._approval_store is None:
            logger.warning(
                "approval_store 未注入, %s 跳过确认门 (降级非阻塞)", action,
            )
            return True
        try:
            q = spec["confirm_q"].format(**payload)
        except Exception:
            q = f"确认执行 {action}？此操作不可逆。"

        from uuid import uuid4
        approval_id = f"gov-{uuid4().hex[:12]}"
        req = self._approval_store.create(
            approval_id=approval_id,
            session_id="",
            iteration=0,
            tool_name="control_workflow",
            arguments={"action": action, "payload": payload, "question": q},
            summary=q,
        )
        # 推 approval_request SSE 事件 -> 前端弹确认卡片 (复用 request_confirmation 卡片渲染).
        # event_callback 由 ToolExecutor 自动注入; 测试/非流式环境为 None, 仅靠 approval_store 阻塞.
        if event_callback is not None:
            try:
                await event_callback("approval_request", {
                    "approval_id": approval_id,
                    "tool": "control_workflow",
                    "summary": q,
                    "iteration": 0,
                    "options": ["确认执行", "取消"],
                    "question": q,
                })
            except Exception:
                logger.debug("governance confirm event emit failed", exc_info=True)
        await req.event.wait()
        decision = getattr(req, "decision", "") or ""
        logger.info("%s confirm_gate decision=%s", action, decision)
        return decision in ("approved", "确认", "确认执行", "确认撤回", "确认覆盖", "确认纠错", True)

    async def _modify_workflow(self, workflow_id: str, new_spec: dict) -> ToolResult:
        """Op 16-19: Mid-workflow modification via cancel+relaunch.

        Source: Airflow dynamic DAG + Temporal Update API.

        Sends modify_spec signal to L2. The workflow completes with
        status 'MODIFIED' and returns the new spec + preserved nodes.
        L1 then launches a new workflow with the modified spec.
        """
        connector = self._deps.bridge.event_connector if self._deps and self._deps.bridge else None
        if connector is None:
            return ToolResult(error="Bridge not configured for signal sending")

        try:
            await connector.send_signal(workflow_id, "modify_spec", [new_spec])
            # Query the result - workflow should complete with MODIFIED status
            import asyncio
            for _ in range(30):  # wait up to 30s
                await asyncio.sleep(1)
                status = await self._temporal_query(workflow_id)
                if status and status.get("status") == "MODIFIED":
                    return ToolResult(output={
                        "status": "MODIFIED",
                        "workflow_id": workflow_id,
                        "modified_spec": status.get("modified_spec"),
                        "preserved_nodes": status.get("preserved_nodes", []),
                        "next_action": "Relaunch with modified spec to continue execution",
                    })
                if status and status.get("status") in ("COMPLETED", "FAILED"):
                    return ToolResult(output={
                        "status": status.get("status"),
                        "workflow_id": workflow_id,
                        "note": "Workflow already completed before modify could take effect",
                    })
            return ToolResult(output={
                "status": "PENDING",
                "workflow_id": workflow_id,
                "note": "Modify signal sent, workflow still processing. Query again later.",
            })
        except Exception as e:
            return ToolResult(error=f"Modify failed: {e}")

    async def _send_signal(self, action: str, workflow_id: str) -> ToolResult:
        """发 Temporal signal — pause/resume/cancel。"""
        connector = self._deps.bridge.event_connector if self._deps and self._deps.bridge else None
        if connector is None:
            return ToolResult(error="bridge not available")

        # signal name 与 dag_runner_workflow.py 的 @workflow.signal 方法名对应
        signal_map = {
            "pause": "pause",
            "resume": "resume",
            "cancel": "cancel_by_user",
        }
        signal_name = signal_map.get(action)
        if not signal_name:
            return ToolResult(error=f"unsupported action: {action}")

        # EventConnector 应有 send_signal 方法 — 检查是否存在
        if not hasattr(connector, "send_signal"):
            return ToolResult(error=(
                "EventConnector has no send_signal method. "
                "Need to add it to bridge/event_connector.py"
            ))

        try:
            await connector.send_signal(workflow_id, signal_name, args=None)
            # 同步更新 L1 WorkflowEventBus 状态(让前端立即看到)
            try:
                from cognitiveplane.interaction.workflow_events import (
                    WorkflowEvent, get_workflow_event_bus,
                )
                bus = get_workflow_event_bus()
                cached = bus.get_workflow_status(workflow_id)
                session_id = cached.get("session_id", "unknown") if cached else "unknown"
                event_type = {
                    "pause": "workflow_paused",
                    "resume": "workflow_resumed",
                    "cancel": "workflow_failed",
                }[action]
                await bus.publish(WorkflowEvent(
                    session_id=session_id,
                    workflow_id=workflow_id,
                    event_type=event_type,
                    error="cancelled by user" if action == "cancel" else None,
                ))
            except Exception:
                pass  # 事件推送失败不阻断
            return ToolResult(output={
                "action": action,
                "workflow_id": workflow_id,
                "ok": True,
                "message": f"signal '{signal_name}' sent to workflow {workflow_id}",
            })
        except Exception as e:
            return ToolResult(error=f"send_signal failed: {e}")


__all__ = ["WorkflowControlTool"]
