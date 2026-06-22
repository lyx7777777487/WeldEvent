"""ExplainDecisionTool — explain a decision's reasoning trace.

Source: 7-plane redesign spec §7.
Queries the BrainDecisionRepository to retrieve the decision and
produces a human-readable explanation of persona, reasoning mode,
confidence, outputs, and validation status.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cognitiveplane.control.tools import BrainTool, ToolResult

if TYPE_CHECKING:
    from cognitiveplane.control.ports import BrainDecisionRepository


class ExplainDecisionTool(BrainTool):
    """Explain the reasoning behind a decision.

    Retrieves the decision from the repository and formats a
    structured explanation. Audience parameter adjusts verbosity.
    """

    def __init__(self, decision_repo: BrainDecisionRepository | None = None) -> None:
        self._repo = decision_repo

    @property
    def name(self) -> str:
        return "explain_decision"

    @property
    def description(self) -> str:
        return (
            "Explain the reasoning behind a decision. Returns the decision's "
            "reasoning trace, persona, confidence, and validation status."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "decision_id": {
                    "type": "string",
                    "description": "ID of the decision to explain",
                },
                "audience": {
                    "type": "string",
                    "enum": ["operator", "engineer", "manager"],
                    "description": "Target audience for the explanation",
                },
            },
            "required": ["decision_id"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        from cognitiveplane.shared.types import DecisionId as DID

        decision_id_str = kwargs["decision_id"]
        audience = kwargs.get("audience", "operator")

        if self._repo is not None:
            from uuid import UUID

            try:
                uid = UUID(decision_id_str)
            except ValueError:
                return ToolResult(error=f"Invalid decision_id: {decision_id_str!r}")

            decision = await self._repo.find_by_id(DID(value=uid))
            if decision is None:
                return ToolResult(error=f"Decision {decision_id_str} not found")

            return ToolResult(output={
                "decision_id": decision_id_str,
                "persona": getattr(decision.persona, "value", str(decision.persona)),
                "reasoning_mode": getattr(decision.reasoning_mode, "value", str(decision.reasoning_mode)),
                "confidence": decision.confidence,
                "validation_result": (
                    getattr(decision.validation_result, "value", str(decision.validation_result))
                    if decision.validation_result else None
                ),
                "output_count": len(decision.outputs),
                "explanation": self._format_explanation(decision, audience),
                "audience": audience,
            })

        return ToolResult(output={
            "decision_id": decision_id_str,
            "explanation": "Decision explanation not yet available (Copilot module pending Phase 1f).",
            "audience": audience,
        })

    @staticmethod
    def _format_explanation(decision: object, audience: str) -> str:
        persona = getattr(decision, "persona", "unknown")
        mode = getattr(decision, "reasoning_mode", "unknown")
        confidence = getattr(decision, "confidence", 0.0)
        outputs = getattr(decision, "outputs", [])
        validation = getattr(decision, "validation_result", None)

        parts = [
            f"Decision made by {persona} persona using {mode} reasoning.",
            f"Confidence: {confidence:.0%}.",
        ]
        if validation:
            parts.append(f"Validation: {validation}.")
        if outputs:
            parts.append(f"Generated {len(outputs)} output(s).")

        if audience == "manager":
            return " ".join(parts)
        if audience == "engineer":
            output_types = []
            for o in outputs:
                content = getattr(o, "content", None)
                kind = getattr(content, "type", None) if content else None
                if kind:
                    output_types.append(kind)
            if output_types:
                parts.append(f"Output types: {', '.join(output_types)}.")
            return " ".join(parts)
        return " ".join(parts)
