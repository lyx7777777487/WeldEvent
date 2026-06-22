"""3-tier fallback strategy (spec §7 lines 1258-1329).

Tier 1: REACT_FUNCTION_CALLING — full LLM tool-loop
Tier 2: STRUCTURED_OUTPUT — single-shot Pydantic-constrained output
Tier 3: EMBEDDING_RULES — semantic embedding + cosine similarity + rules
                          (deterministic, no LLM)

The selector picks the highest tier the available capabilities support.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class InteractionTier(str, Enum):
    REACT_FUNCTION_CALLING = "react_function_calling"
    STRUCTURED_OUTPUT = "structured_output"
    EMBEDDING_RULES = "embedding_rules"


@dataclass
class TierCapabilities:
    """Available capabilities the selector inspects to pick a tier."""

    has_function_calling_llm: bool = False
    has_structured_llm: bool = False
    has_embeddings: bool = False
    force_tier: InteractionTier | None = None


class FallbackSelector:
    """Pick the highest available tier.

    Order:
    - force_tier overrides everything
    - has_function_calling_llm → REACT_FUNCTION_CALLING
    - has_structured_llm → STRUCTURED_OUTPUT
    - has_embeddings → EMBEDDING_RULES
    - default → EMBEDDING_RULES (always available)
    """

    @staticmethod
    def select(caps: TierCapabilities) -> InteractionTier:
        if caps.force_tier is not None:
            return caps.force_tier
        if caps.has_function_calling_llm:
            return InteractionTier.REACT_FUNCTION_CALLING
        if caps.has_structured_llm:
            return InteractionTier.STRUCTURED_OUTPUT
        return InteractionTier.EMBEDDING_RULES


def degrade(current: InteractionTier) -> InteractionTier:
    """Return the next-lower tier, or the same tier if already at the bottom."""
    if current == InteractionTier.REACT_FUNCTION_CALLING:
        return InteractionTier.STRUCTURED_OUTPUT
    if current == InteractionTier.STRUCTURED_OUTPUT:
        return InteractionTier.EMBEDDING_RULES
    return InteractionTier.EMBEDDING_RULES
