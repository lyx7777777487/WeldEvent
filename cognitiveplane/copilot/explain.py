"""CopilotExplain — Decision explanation + reasoning trace (spec §11).

Given a DecisionId, retrieves the BrainDecision and renders a human-readable
explanation: persona, mode, outputs, confidence, and the reasoning path.
LLM is optional; baseline mode produces a deterministic textual summary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from cognitiveplane.capability.provider import LLMProvider, LLMRequest
from cognitiveplane.control.ports import BrainDecisionRepository
from cognitiveplane.shared.dto_decision import BrainDecision
from cognitiveplane.shared.types import DecisionId


@dataclass
class ExplanationResult:
    decision_id: DecisionId
    explanation: str
    persona: str
    reasoning_mode: str
    confidence: float
    output_count: int


class DecisionNotFoundError(Exception):
    def __init__(self, decision_id: DecisionId) -> None:
        super().__init__(f"BrainDecision not found: {decision_id.value}")


class CopilotExplain:
    def __init__(
        self,
        repo: BrainDecisionRepository,
        llm: Optional[LLMProvider] = None,
    ) -> None:
        self._repo = repo
        self._llm = llm

    async def explain(self, decision_id: DecisionId) -> ExplanationResult:
        decision = await self._repo.find_by_id(decision_id)
        if decision is None:
            raise DecisionNotFoundError(decision_id)
        text = await self._render(decision)
        return ExplanationResult(
            decision_id=decision.decision_id,
            explanation=text,
            persona=decision.persona.value,
            reasoning_mode=decision.reasoning_mode.value,
            confidence=decision.confidence,
            output_count=len(decision.outputs),
        )

    async def _render(self, decision: BrainDecision) -> str:
        baseline = self._baseline(decision)
        if self._llm is None:
            return baseline
        try:
            req = LLMRequest(
                messages=[
                    {
                        "role": "system",
                        "content": "Rephrase the structured decision summary into a clear operator-facing explanation.",
                    },
                    {"role": "user", "content": baseline},
                ],
                caller="CopilotExplain",
                purpose="decision_explanation",
            )
            resp = await self._llm.complete(req)
            return resp.content or baseline
        except Exception:
            return baseline

    @staticmethod
    def _baseline(decision: BrainDecision) -> str:
        outputs = ", ".join(
            type(out.content).__name__ for out in decision.outputs
        ) or "none"
        return (
            f"Decision {decision.decision_id.value} for case {decision.case_id.value}: "
            f"persona={decision.persona.value}, "
            f"mode={decision.reasoning_mode.value}, "
            f"DP={decision.decision_point.value}, "
            f"state={decision.state.value}, "
            f"confidence={decision.confidence:.2f}, "
            f"outputs=[{outputs}], "
            f"trigger={decision.trigger_event_type.value}"
        )
