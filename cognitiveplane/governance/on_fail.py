"""OnFailAction enum (spec §17.4 lines 2500-2508).

Defines the strategy a guarded operation should take when a validator,
guardrail, or safety check fails. Inspired by Guardrails AI's on_fail
parameter (REASK / FIX / FILTER / REFRAIN / EXCEPTION) plus an additional
ESCALATE action that hands off to human review via the governance plane.
"""

from __future__ import annotations

from enum import Enum


class OnFailAction(str, Enum):
    """How to react when a validator/guardrail fires."""

    REASK = "reask"          # Re-prompt the LLM with the failure context
    FIX = "fix"              # Auto-correct via deterministic fix function
    FILTER = "filter"        # Drop the offending field and continue
    REFRAIN = "refrain"      # Refuse — return a neutral fallback response
    ESCALATE = "escalate"    # Hand off to human review via governance


def default_action_for_severity(severity: float) -> OnFailAction:
    """Pick a reasonable default OnFailAction from a normalized severity 0-1."""
    if severity >= 0.9:
        return OnFailAction.ESCALATE
    if severity >= 0.6:
        return OnFailAction.REFRAIN
    if severity >= 0.3:
        return OnFailAction.FIX
    return OnFailAction.REASK
