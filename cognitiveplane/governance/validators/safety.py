"""SafetyValidator — real safety gate for the validation pipeline.

BLOCK conditions:
- context.validation_critical_count >= 3 (multiple critical validations)
- decision confidence < 0.3 (dangerously low confidence)
- safety-critical keywords in context data (e.g. pressure, toxic, explosion)

Otherwise PASS with the list of rules checked.
"""

from datetime import datetime, timezone

from cognitiveplane.shared.ports.validation import (
    SafetyValidatorInput,
    SafetyValidatorOutput,
    SafetyValidatorPort,
)
from cognitiveplane.shared.enums import SafetyStatus

_SAFETY_KEYWORDS = {"pressure", "toxic", "explosion", "flammable", "radiation", "高压", "有毒", "爆炸", "易燃", "辐射"}
_CRITICAL_THRESHOLD = 3
_LOW_CONFIDENCE_THRESHOLD = 0.3


class SafetyValidator(SafetyValidatorPort):
    async def check(self, input_data: SafetyValidatorInput) -> SafetyValidatorOutput:
        context = input_data.context
        decision = input_data.decision
        checked_rules: list[str] = []
        blocking_reasons: list[str] = []

        # Rule: critical validation count
        checked_rules.append("critical_validation_count")
        if context.validation_critical_count >= _CRITICAL_THRESHOLD:
            blocking_reasons.append(
                f"critical_validation_count={context.validation_critical_count}"
            )

        # Rule: minimum confidence
        checked_rules.append("minimum_confidence")
        if decision.confidence < _LOW_CONFIDENCE_THRESHOLD:
            blocking_reasons.append(f"confidence={decision.confidence:.2f}")

        # Rule: safety-critical keywords in case data
        checked_rules.append("safety_keywords_scan")
        case_text = " ".join(str(v) for v in context.case_data.values()).lower()
        found_keywords = [kw for kw in _SAFETY_KEYWORDS if kw in case_text]
        if found_keywords:
            blocking_reasons.append(f"safety_keywords={found_keywords}")

        result = SafetyStatus.BLOCK if blocking_reasons else SafetyStatus.PASS
        return SafetyValidatorOutput(
            result=result,
            checked_rules=checked_rules,
            timestamp=datetime.now(timezone.utc),
        )
