"""Reflector — self-built reflection on reasoning results (spec §5 line 1007).

Inspects a BrainDecision + ValidationResult (and optional feedback) and emits
identified gaps + improvement suggestions. Deterministic LLM-free baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cognitiveplane.shared.dto.collaboration import FeedbackContent
from cognitiveplane.shared.dto.context import ContextSnapshot
from cognitiveplane.shared.dto.deepagents import Gap, Suggestion
from cognitiveplane.shared.dto_decision import BrainDecision
from cognitiveplane.shared.dto.validation import ValidationResult
from cognitiveplane.shared.enums import (
    AggregatedValidationResult,
    SafetyStatus,
    UrgencyLevel,
)


@dataclass
class ReflectionInput:
    context: ContextSnapshot
    decision: BrainDecision
    validation_result: ValidationResult
    feedback: FeedbackContent | None = None


@dataclass
class ReflectionOutput:
    reflection: str
    identified_gaps: list[Gap] = field(default_factory=list)
    improvement_suggestions: list[Suggestion] = field(default_factory=list)


class Reflector:
    """Reflect on a decision/validation pair, surface gaps and suggestions."""

    @staticmethod
    def reflect(inp: ReflectionInput) -> ReflectionOutput:
        gaps: list[Gap] = []
        suggestions: list[Suggestion] = []
        notes: list[str] = []

        agg = inp.validation_result.aggregated_result
        if agg == AggregatedValidationResult.REJECTED:
            gaps.append(Gap(description="Decision was rejected by governance", severity=0.9))
            suggestions.append(
                Suggestion(
                    description="Re-derive parameters with stricter constraints",
                    expected_improvement="Higher chance of validation approval",
                    priority=UrgencyLevel.URGENT,
                )
            )
        elif agg == AggregatedValidationResult.ESCALATED:
            gaps.append(Gap(description="Decision was escalated", severity=0.7))
            suggestions.append(
                Suggestion(
                    description="Surface evidence supporting human decision",
                    expected_improvement="Faster human resolution",
                    priority=UrgencyLevel.URGENT,
                )
            )
        elif agg == AggregatedValidationResult.REQUIRES_REVIEW:
            gaps.append(Gap(description="Decision requires human review", severity=0.5))

        if inp.validation_result.safety_result.result == SafetyStatus.BLOCK:
            gaps.append(Gap(description="Safety BLOCK detected", severity=1.0))
            suggestions.append(
                Suggestion(
                    description="Re-check safety constraints before retry",
                    expected_improvement="Prevent recurring safety blocks",
                    priority=UrgencyLevel.CRITICAL,
                )
            )

        if inp.decision.confidence < 0.6:
            gaps.append(
                Gap(
                    description=f"Low decision confidence ({inp.decision.confidence:.2f})",
                    severity=0.5,
                )
            )
            suggestions.append(
                Suggestion(
                    description="Gather more memory matches or retrieve additional standards",
                    expected_improvement="Raise decision confidence",
                    priority=UrgencyLevel.ROUTINE,
                )
            )

        if inp.feedback is not None and getattr(inp.feedback, "feedback_text", None):
            notes.append(f"Operator feedback noted: {str(inp.feedback.feedback_text)[:200]}")

        notes.append(
            f"Aggregated validation: {agg.value}; "
            f"confidence={inp.decision.confidence:.2f}; "
            f"persona={inp.decision.persona.value}"
        )
        reflection = " | ".join(notes)

        return ReflectionOutput(
            reflection=reflection,
            identified_gaps=gaps,
            improvement_suggestions=suggestions,
        )
