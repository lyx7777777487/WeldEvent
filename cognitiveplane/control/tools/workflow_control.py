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
    """工作流控制工具 — query / pause / resume / cancel 四合一。"""

    phase = 3  # 与 launch_workflow 同阶段,工作流启动后才有意义

    def __init__(self, deps) -> None:
        self._deps = deps

    @property
    def name(self) -> str:
        return "control_workflow"

    @property
    def description(self) -> str:
        return (
            "Control a running workflow: query status, pause, resume, or cancel. "
            "Use this when the user asks about workflow progress, wants to "
            "pause/resume/cancel an in-progress workflow, or asks 'what step "
            "is the workflow on'.\n\n"
            "Actions:\n"
            "- query: Get current node statuses + completion progress. "
            "Reads from L1 event cache first (fast), falls back to Temporal query.\n"
            "- pause: Pause workflow before next node (won't interrupt running node).\n"
            "- resume: Resume a paused workflow.\n"
            "- cancel: Cancel workflow entirely (terminates, no rollback).\n\n"
            "workflow_id is required. Get it from launch_workflow's output."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["query", "pause", "resume", "cancel"],
                    "description": "Control action to perform",
                },
                "workflow_id": {
                    "type": "string",
                    "description": "Workflow ID from launch_workflow output",
                },
            },
            "required": ["action", "workflow_id"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        action = kwargs.get("action")
        workflow_id = kwargs.get("workflow_id")
        if not action or action not in ("query", "pause", "resume", "cancel"):
            return ToolResult(error=f"invalid action: {action}")
        if not workflow_id:
            return ToolResult(error="workflow_id is required")

        # query: 优先从 L1 WorkflowEventBus 缓存读（快）
        if action == "query":
            return await self._query(workflow_id)
        # pause/resume/cancel: 调 Temporal signal
        return await self._send_signal(action, workflow_id)

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
