"""Mock DeepAgents adapter returning canned responses.

Source: L1_Port_and_Contract_Design.md (Phase 4, Sections 7.1–7.5).
"""

from src.shared.dto_deepagents import (
    Conclusion,
    Plan,
)
from src.shared.ports.deepagents import (
    DeepAgentsExplanationPort,
    DeepAgentsMemoryUtilizationPort,
    DeepAgentsPlanningPort,
    DeepAgentsReasoningPort,
    DeepAgentsReflectionPort,
    ExplanationInput,
    ExplanationOutput,
    MemoryUtilizationInput,
    MemoryUtilizationOutput,
    PlanningInput,
    PlanningOutput,
    ReasoningInput,
    ReasoningOutput,
    ReflectionInput,
    ReflectionOutput,
)


class MockDeepAgentsAdapter(
    DeepAgentsReasoningPort,
    DeepAgentsPlanningPort,
    DeepAgentsReflectionPort,
    DeepAgentsExplanationPort,
    DeepAgentsMemoryUtilizationPort,
):
    """Mock adapter returning fixed canned responses for all 5 ports.

    Suitable for unit tests and local development where real cognitive
    reasoning is not required.
    """

    # ------------------------------------------------------------------
    # 7.1 DeepAgentsReasoningPort
    # ------------------------------------------------------------------

    async def reason(self, input_data: ReasoningInput) -> ReasoningOutput:
        return ReasoningOutput(
            reasoning_trace="mock_reasoning_trace",
            conclusions=[
                Conclusion(
                    statement="mock_conclusion",
                    confidence=0.8,
                    supporting_evidence=["mock_evidence"],
                )
            ],
            confidence=0.8,
        )

    # ------------------------------------------------------------------
    # 7.2 DeepAgentsPlanningPort
    # ------------------------------------------------------------------

    async def plan(self, input_data: PlanningInput) -> PlanningOutput:
        return PlanningOutput(
            plan=Plan(
                steps=["mock_step"],
                expected_outcome="mock_outcome",
                confidence=0.8,
            ),
            alternative_plans=[],
            confidence=0.8,
        )

    # ------------------------------------------------------------------
    # 7.3 DeepAgentsReflectionPort
    # ------------------------------------------------------------------

    async def reflect(self, input_data: ReflectionInput) -> ReflectionOutput:
        return ReflectionOutput(
            reflection="mock_reflection",
            identified_gaps=[],
            improvement_suggestions=[],
        )

    # ------------------------------------------------------------------
    # 7.4 DeepAgentsExplanationPort
    # ------------------------------------------------------------------

    async def explain(self, input_data: ExplanationInput) -> ExplanationOutput:
        return ExplanationOutput(
            explanation="mock_explanation",
            key_factors=[],
            confidence_justification="mock_justification",
        )

    # ------------------------------------------------------------------
    # 7.5 DeepAgentsMemoryUtilizationPort
    # ------------------------------------------------------------------

    async def utilize_memory(
        self, input_data: MemoryUtilizationInput
    ) -> MemoryUtilizationOutput:
        return MemoryUtilizationOutput(
            relevant_experiences=[],
            applicability_assessment=[],
            adaptation_suggestions=[],
        )
