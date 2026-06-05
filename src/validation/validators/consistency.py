from datetime import datetime, timezone

from src.shared.ports.validation import (
    ConsistencyValidatorInput,
    ConsistencyValidatorOutput,
    ConsistencyValidatorPort,
)
from src.shared.enums import ConsistencyStatus


class StubConsistencyValidator(ConsistencyValidatorPort):
    async def check(
        self, input_data: ConsistencyValidatorInput
    ) -> ConsistencyValidatorOutput:
        return ConsistencyValidatorOutput(
            result=ConsistencyStatus.CONSISTENT,
            factual_consistency=ConsistencyStatus.CONSISTENT,
            historical_consistency=ConsistencyStatus.CONSISTENT,
            inconsistencies=[],
            timestamp=datetime.now(timezone.utc),
        )
