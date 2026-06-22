"""Validation DTOs — backward-compat shim.

Canonical home is now ``cognitiveplane.shared.dto.validation``.
This module re-exports every public name so existing callers
(``from cognitiveplane.shared.dto_validation import ...``) keep working.
"""

from cognitiveplane.shared.dto.validation import (  # noqa: F401
    ConsistencyValidationResult,
    DivergencePoint,
    InconsistencyDetail,
    RuleValidationResult,
    SafetyValidationResult,
    ShadowDecision,
    ShadowValidationResult,
    StageResult,
    ValidationResult,
)

__all__ = [
    "ConsistencyValidationResult",
    "DivergencePoint",
    "InconsistencyDetail",
    "RuleValidationResult",
    "SafetyValidationResult",
    "ShadowDecision",
    "ShadowValidationResult",
    "StageResult",
    "ValidationResult",
]
