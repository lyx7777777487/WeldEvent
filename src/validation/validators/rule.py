from datetime import datetime, timezone

from src.shared.ports.validation import (
    RuleValidatorInput,
    RuleValidatorOutput,
    RuleValidatorPort,
)
from src.shared.enums import RuleStatus


class StubRuleValidator(RuleValidatorPort):
    async def check(self, input_data: RuleValidatorInput) -> RuleValidatorOutput:
        return RuleValidatorOutput(
            result=RuleStatus.PASS,
            violated_rules=[],
            timestamp=datetime.now(timezone.utc),
        )
