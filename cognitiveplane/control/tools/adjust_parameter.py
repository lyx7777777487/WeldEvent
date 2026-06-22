"""AdjustParameterTool — adjust inspection parameters via GatewayWritePort."""

from cognitiveplane.control.tools import BrainTool, ToolResult
from cognitiveplane.shared.ports.gateway import GatewayWritePort


class AdjustParameterTool(BrainTool):
    """Adjust inspection parameters on the production line via WeldMap."""

    def __init__(self, port: GatewayWritePort) -> None:
        self._port = port

    @property
    def name(self) -> str:
        return "adjust_parameter"

    @property
    def description(self) -> str:
        return (
            "Adjust inspection parameters on the running production line. "
            "Writes parameter changes via WeldMap. Requires reason. "
            "Max 5 calls per session by policy."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "parameter_name": {
                    "type": "string",
                    "description": "Name of parameter to adjust (e.g. voltage, speed)",
                },
                "current_value": {
                    "type": "string",
                    "description": "Current parameter value",
                },
                "proposed_value": {
                    "type": "string",
                    "description": "Proposed new value",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for adjustment (required by policy)",
                },
                "case_id": {
                    "type": "string",
                    "description": "Case ID this adjustment applies to",
                },
            },
            "required": ["parameter_name", "proposed_value", "reason"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        from cognitiveplane.shared.dto_gateway import ParameterPatch
        from cognitiveplane.shared.dto_decision.outputs import ParameterAdjustment
        from cognitiveplane.shared.types import CaseId, DecisionId
        from datetime import datetime, timezone
        from uuid import uuid4

        case_id = CaseId(value=kwargs.get("case_id", "unknown"))
        adjustment = ParameterAdjustment(
            parameter_name=kwargs["parameter_name"],
            current_value=kwargs.get("current_value", ""),
            proposed_value=kwargs["proposed_value"],
            unit=kwargs.get("unit", ""),
        )
        patch = ParameterPatch(
            patch_id=uuid4(),
            decision_id=DecisionId(value=uuid4()),
            case_id=case_id,
            parameter_adjustments=[adjustment],
            confidence=0.7,
            rationale=kwargs["reason"],
            created_at=datetime.now(timezone.utc),
        )

        try:
            result = await self._port.publish_parameter_patch(patch)
            return ToolResult(output={
                "patch_id": str(patch.patch_id),
                "success": result.success,
                "weldmap_path": result.weldmap_path,
            })
        except Exception as e:
            return ToolResult(error=str(e))
