"""Capability plane — OpenAI-compatible adapter (spec §骨架 line 966).

Canonical alias for `capability.openai_provider.OpenAIProvider`, which
already targets the OpenAI-compatible API surface (DeepSeek included).
"""

from cognitiveplane.capability.openai_provider import OpenAIProvider

# Spec uses the name `OpenAICompatProvider`; keep both aliases live
# while legacy callers use the older name.
OpenAICompatProvider = OpenAIProvider

__all__ = ["OpenAICompatProvider", "OpenAIProvider"]
