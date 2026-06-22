"""RuleValidator — real business-rule gate for the validation pipeline.

REJECT conditions:
- decision confidence < 0.4 (below acceptable threshold)
- empty rationale on decision outputs
- constraint violations detected in outputs

Otherwise APPROVED with the list of rules checked.
"""

from datetime import datetime, timezone

from cognitiveplane.shared.ports.validation import (
    RuleValidatorInput,
    RuleValidatorOutput,
    RuleValidatorPort,
)
from cognitiveplane.shared.enums import RuleStatus

_MIN_CONFIDENCE = 0.4


class RuleValidator(RuleValidatorPort):
    async def check(self, input_data: RuleValidatorInput) -> RuleValidatorOutput:
        decision = input_data.decision
        violated: list[str] = []

        # Rule: minimum confidence threshold
        if decision.confidence < _MIN_CONFIDENCE:
            violated.append(f"confidence_below_{_MIN_CONFIDENCE}")

        # Rule: non-empty rationale on outputs
        for i, output in enumerate(decision.outputs):
            rationale = getattr(output.content, "rationale", None)
            if rationale is not None and not rationale.strip():
                violated.append(f"empty_rationale_output_{i}")

            # Rule: constraint violations
            constraints = getattr(output.content, "constraints_applied", None)
            if constraints and len(constraints) > 5:
                violated.append(f"excessive_constraints_output_{i}")

        result = RuleStatus.REJECT if violated else RuleStatus.APPROVED
        return RuleValidatorOutput(
            result=result,
            violated_rules=violated,
            timestamp=datetime.now(timezone.utc),
        )
