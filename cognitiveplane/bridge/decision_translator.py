"""Bridge — DecisionTranslator (spec §6.1 lines 340-344).

Translates a published `BrainDecision` (decision domain on WeldMap)
into a `WorkflowTemplate` payload that the L2 Temporal control plane
knows how to launch. The template is intentionally a plain dataclass
— it must be JSON-serialisable to cross the L1/L2 boundary cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cognitiveplane.shared.dto_decision import BrainDecision

GENERIC_KIND = "generic_workflow"


@dataclass(frozen=True)
class WorkflowTemplate:
    """Plan-of-work payload handed to Temporal.

    Phase 1 keeps this payload-shape minimal; Phase 2 enriches it with
    Temporal-specific fields (task queue, retry policy, child workflow
    declarations) once the L2 catalogue stabilises.
    """

    template_id: str
    case_id: str
    decision_id: str
    workflow_kind: str
    parameters: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class DecisionTranslator:
    """Pure function: BrainDecision → WorkflowTemplate.

    The translator does NOT decide whether a decision should launch a
    workflow — that policy lives in `EventConnector`. Here we only
    project a published decision into the L2-facing template shape.
    The first output's `content.type` becomes the workflow kind; the
    same content payload is dumped into `parameters`.
    """

    def translate(self, decision: BrainDecision) -> WorkflowTemplate:
        primary = self._primary_content(decision)
        return WorkflowTemplate(
            template_id=f"tmpl-{decision.decision_id.value}",
            case_id=str(decision.case_id.value),
            decision_id=str(decision.decision_id.value),
            workflow_kind=self._infer_kind(primary),
            parameters=self._dump(primary),
            metadata={
                "persona": getattr(decision.persona, "value", str(decision.persona)),
                "reasoning_mode": getattr(
                    decision.reasoning_mode, "value", str(decision.reasoning_mode)
                ),
                "confidence": decision.confidence,
            },
        )

    @staticmethod
    def _primary_content(decision: BrainDecision) -> Any | None:
        outputs = getattr(decision, "outputs", None) or []
        if not outputs:
            return None
        first = outputs[0]
        return getattr(first, "content", first)

    @staticmethod
    def _infer_kind(content: Any | None) -> str:
        if content is None:
            return GENERIC_KIND
        kind = getattr(content, "type", None) or getattr(content, "kind", None)
        return str(kind) if kind else GENERIC_KIND

    @staticmethod
    def _dump(content: Any | None) -> dict[str, Any]:
        if content is None:
            return {}
        if hasattr(content, "model_dump"):
            return content.model_dump()
        if hasattr(content, "dict"):
            return content.dict()
        return {}


__all__ = ["DecisionTranslator", "GENERIC_KIND", "WorkflowTemplate"]
