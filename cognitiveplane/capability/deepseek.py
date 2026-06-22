"""Capability plane — DeepSeek adapter (spec §骨架 line 965).

WeldEvent's primary LLM is DeepSeek (per `auto memory: project_llm_provider`).
DeepSeek exposes an OpenAI-compatible API, so the existing
`OpenAIProvider` already serves DeepSeek when its `LLMConfig.primary`
is pointed at the DeepSeek base URL. This module is the spec-mandated
named entry point and ships a small factory that wires the right
defaults — keeping the adapter spec-aligned even though the underlying
client is shared with the OpenAI-compatible adapter.
"""

from __future__ import annotations

from cognitiveplane.capability.config import LLMConfig
from cognitiveplane.capability.openai_provider import OpenAIProvider


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek adapter — OpenAI-compatible client with DeepSeek defaults.

    Phase 1 keeps this as a thin subclass; Phase 2f introduces Langfuse
    tracing hooks that may diverge between providers.
    """


def deepseek_provider(config: LLMConfig) -> DeepSeekProvider:
    """Factory — caller is responsible for setting `config.primary` to
    the DeepSeek base URL + key (typically loaded from env)."""
    return DeepSeekProvider(config)


__all__ = ["DeepSeekProvider", "deepseek_provider"]
