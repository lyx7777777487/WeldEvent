"""ShadowValidator — heuristic shadow decision for the validation pipeline.

Produces an independent shadow decision based on the context and decision
data, then compares alignment. This is a heuristic implementation that
checks confidence and consistency heuristics rather than running a full
secondary reasoning pass.

- CRITICAL: shadow confidence < 0.3 or context suggests dangerous operation
- WARN: alignment_score < 0.6
- CONCUR: otherwise
"""

from datetime import datetime, timezone

from cognitiveplane.shared.dto_validation import ShadowDecision, DivergencePoint
from cognitiveplane.shared.ports.validation import (
    ShadowValidatorInput,
    ShadowValidatorOutput,
    ShadowValidatorPort,
)
from cognitiveplane.shared.enums import ShadowStatus

_SHADOW_LOW_CONFIDENCE = 0.3
_ALIGNMENT_WARN_THRESHOLD = 0.6


class ShadowValidator(ShadowValidatorPort):
    async def check(self, input_data: ShadowValidatorInput) -> ShadowValidatorOutput:
        context = input_data.context
        decision = input_data.decision

        # Build a heuristic shadow confidence
        base_conf = 0.7
        # Boost if memory match is strong
        if context.memory_match_confidence > 0.7:
            base_conf += 0.1
        # Reduce if knowledge coverage is low
        if context.knowledge_coverage < 0.5:
            base_conf -= 0.15
        # Reduce if critical validations exist
        if context.validation_critical_count > 0:
            base_conf -= 0.1 * context.validation_critical_count
        shadow_confidence = max(0.0, min(1.0, base_conf))

        shadow_decision = ShadowDecision(
            decision_type="heuristic_shadow",
            outputs={"shadow_confidence": f"{shadow_confidence:.2f}"},
            confidence=shadow_confidence,
        )

        # Compute alignment score
        alignment_score = 1.0 - abs(decision.confidence - shadow_confidence)
        alignment_score = max(0.0, min(1.0, alignment_score))

        # Detect divergence points
        divergence_points: list[DivergencePoint] = []
        if abs(decision.confidence - shadow_confidence) > 0.2:
            divergence_points.append(
                DivergencePoint(
                    output_field="confidence",
                    brain_value=f"{decision.confidence:.2f}",
                    shadow_value=f"{shadow_confidence:.2f}",
                    divergence_severity=min(1.0, abs(decision.confidence - shadow_confidence)),
                )
            )

        # Determine shadow status
        if shadow_confidence < _SHADOW_LOW_CONFIDENCE:
            result = ShadowStatus.CRITICAL
        elif alignment_score < _ALIGNMENT_WARN_THRESHOLD:
            result = ShadowStatus.WARN
        else:
            result = ShadowStatus.CONCUR

        return ShadowValidatorOutput(
            result=result,
            shadow_decision=shadow_decision,
            alignment_score=alignment_score,
            divergence_points=divergence_points,
            timestamp=datetime.now(timezone.utc),
        )


# Keep stub as alias for backward compat
StubShadowValidator = ShadowValidator
