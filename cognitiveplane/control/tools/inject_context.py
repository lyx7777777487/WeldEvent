"""InjectContextTool - Op 4: 向正在运行的工作流注入上下文信息。

用户在执行中补充的信息（如标准/参数/上下文）通过此工具注入到
Temporal workflow 的 inject_context signal，只影响尚未执行的节点。

参考来源: Magentic-One Task Ledger 可更新约束 (§3.2)
"""
from __future__ import annotations

import logging
from typing import Any

from cognitiveplane.control.tools import BrainTool, ToolResult

logger = logging.getLogger("inject_context_tool")


class InjectContextTool(BrainTool):
    """向正在运行的工作流注入上下文信息。"""

    phase = 3

    def __init__(self, deps) -> None:
        self._deps = deps

    @property
    def name(self) -> str:
        return "inject_context"

    @property
    def description(self) -> str:
        return (
            "向正在运行的工作流注入上下文信息。用户在执行中补充的信息"
            "（如'标准应该用 ISO 17635'/'这张图是 T 型接头'）通过此工具注入，"
            "只影响尚未执行的节点，已执行的不受影响。\n"
            "**何时使用**: 工作流正在执行时，用户补充了影响后续节点的信息。\n"
            "**用法**: 传 workflow_id + key + value。"
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "Workflow ID from launch_workflow output",
                },
                "key": {
                    "type": "string",
                    "description": "Context key (e.g. 'standard', 'joint_type', 'threshold')",
                },
                "value": {
                    "description": "Context value (any JSON-serializable type)",
                },
            },
            "required": ["workflow_id", "key", "value"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        workflow_id = kwargs.get("workflow_id")
        key = kwargs.get("key")
        value = kwargs.get("value")

        if not workflow_id:
            return ToolResult(error="workflow_id is required")
        if not key:
            return ToolResult(error="key is required")

        connector = (
            self._deps.bridge.event_connector
            if self._deps and self._deps.bridge
            else None
        )
        if connector is None:
            return ToolResult(error="bridge not available")

        if not hasattr(connector, "send_signal"):
            return ToolResult(error="bridge does not support send_signal")

        try:
            await connector.send_signal(
                workflow_id, "inject_context", args=[key, value]
            )
            logger.info(
                "inject_context: workflow=%s key=%s value=%s",
                workflow_id, key, str(value)[:100],
            )
            return ToolResult(output={
                "workflow_id": workflow_id,
                "key": key,
                "value": value,
                "ok": True,
                "message": f"Context '{key}' injected to workflow {workflow_id} "
                f"(affects pending nodes only)",
            })
        except Exception as e:
            return ToolResult(error=f"inject_context failed: {e}")


__all__ = ["InjectContextTool"]
