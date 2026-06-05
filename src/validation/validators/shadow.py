from datetime import datetime, timezone

from src.shared.dto_validation import ShadowDecision
from src.shared.ports.validation import (
    ShadowValidatorInput,
    ShadowValidatorOutput,
    ShadowValidatorPort,
)
from src.shared.enums import ShadowStatus


class StubShadowValidator(ShadowValidatorPort):
    async def check(self, input_data: ShadowValidatorInput) -> ShadowValidatorOutput:
        return ShadowValidatorOutput(
            result=ShadowStatus.CONCUR,
            shadow_decision=ShadowDecision(
                decision_type="stub",
                outputs={},
                confidence=0.95,
            ),
            alignment_score=0.95,
            divergence_points=[],
            timestamp=datetime.now(timezone.utc),
        )
