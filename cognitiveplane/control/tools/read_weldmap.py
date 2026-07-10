"""ReadWeldMapTool — read WeldMap state for a case."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cognitiveplane.control.tools import BrainTool, ToolResult

if TYPE_CHECKING:
    from cognitiveplane.shared.ports.gateway import GatewayReadPort


class ReadWeldMapTool(BrainTool):
    """Read current WeldMap state: workflow progress, case data, decisions."""

    phase = 3

    def __init__(self, port: GatewayReadPort) -> None:
        self._port = port

    @property
    def name(self) -> str:
        return "read_weldmap"

    @property
    def description(self) -> str:
        return (
            "读取当前 Case 的 WeldMap 状态。\n"
            "**何时使用**：需要了解工作流进度、已产生的数据、"
            "之前的决策记录或测量结果。\n"
            "**用法**：无需参数，自动读取当前 case 的完整状态快照。"
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "case_id": {
                    "type": "string",
                    "description": "Case ID to read state for",
                },
            },
            "required": ["case_id"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        from cognitiveplane.shared.types import CaseId

        case_id_str = kwargs.get("case_id") or kwargs.get("query", "unknown")
        case_id = CaseId(value=case_id_str)
        try:
            snapshot = await self._port.read_weldmap_snapshot(case_id)
            return ToolResult(output={
                "case_id": str(snapshot.case_id),
                "workflow_state": snapshot.workflow_state,
                "decisions": [str(d) for d in snapshot.decisions] if hasattr(snapshot, "decisions") else [],
                "events_count": len(snapshot.events) if hasattr(snapshot, "events") else 0,
            })
        except Exception as e:
            return ToolResult(error=str(e))
