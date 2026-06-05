from datetime import datetime, timezone

from src.shared.ports.validation import (
    SafetyValidatorInput,
    SafetyValidatorOutput,
    SafetyValidatorPort,
)
from src.shared.enums import SafetyStatus


class StubSafetyValidator(SafetyValidatorPort):
    async def check(self, input_data: SafetyValidatorInput) -> SafetyValidatorOutput:
        return SafetyValidatorOutput(
            result=SafetyStatus.PASS,
            checked_rules=["stub_safety_check"],
            timestamp=datetime.now(timezone.utc),
        )
