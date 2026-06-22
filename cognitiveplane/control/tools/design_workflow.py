"""DesignWorkflowTool — design inspection workflow via BrainOrchestrator."""

from cognitiveplane.control.tools import BrainTool, ToolResult


class DesignWorkflowTool(BrainTool):
    """Design an inspection workflow using the BrainOrchestrator pipeline."""

    def __init__(self, orchestrator, deps) -> None:
        self._orchestrator = orchestrator
        self._deps = deps

    @property
    def name(self) -> str:
        return "design_workflow"

    @property
    def description(self) -> str:
        return (
            "Design a complete inspection workflow for a case. "
            "Invokes the Brain decision pipeline (Persona → Reasoning → Knowledge → "
            "Memory → Decision → Validation → Publish). Requires a reason."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "objective": {
                    "type": "string",
                    "description": "High-level goal (e.g. 'Design inspection workflow for Q345R 22mm weld')",
                },
                "requirements": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of quality requirements or constraints",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for invoking workflow design (required by policy)",
                },
            },
            "required": ["objective", "reason"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        from cognitiveplane.shared.dto_context import ContextSnapshot
        from cognitiveplane.shared.enums import EventType, NoveltyLevel
        from cognitiveplane.shared.types import CaseId
        from datetime import datetime, timezone

        objective = kwargs["objective"]
        requirements = kwargs.get("requirements", [])

        # Build a minimal context if none available
        context = ContextSnapshot(
            case_id=CaseId(value=kwargs.get("case_id", "tool-design-workflow")),
            event_type=EventType.WORKFLOW_ENTERED,
            workflow_state={},
            case_data={"objective": objective},
            measurements=[],
            memory_match_confidence=0.5,
            knowledge_coverage=0.5,
            event_novelty=NoveltyLevel.PARTIAL,
            validation_critical_count=0,
            timestamp=datetime.now(timezone.utc),
        )

        try:
            result = await self._orchestrator.execute(
                objective=objective,
                requirements=requirements,
                context=context,
                deps=self._deps,
            )
            if result.success and result.decision:
                return ToolResult(output={
                    "decision_id": str(result.decision.decision_id.value),
                    "persona": result.decision.persona.value,
                    "reasoning_mode": result.decision.reasoning_mode.value,
                    "confidence": result.decision.confidence,
                    "published": result.published,
                })
            else:
                return ToolResult(error=result.error or "Orchestration failed")
        except Exception as e:
            return ToolResult(error=str(e))
