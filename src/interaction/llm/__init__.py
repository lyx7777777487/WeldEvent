"""LLM Provider abstraction layer — single entry point for all LLM calls.

Source: L1-Interaction-Layer-Business-Requirements.md §3.6.

Usage:
    from src.interaction.llm import get_llm, init_llm
    init_llm(MockLLMProvider())  # or OpenAIProvider(config)
    provider = get_llm()
    resp = await provider.complete(LLMRequest(...))
"""

from src.interaction.llm.config import LLMConfig, OpenAIConfig
from src.interaction.llm.mock_provider import MockLLMProvider
from src.interaction.llm.provider import LLMProvider, LLMRequest, LLMResponse
from src.interaction.llm.tracking import LLMCallRecord, LLMCallTracker

__all__ = [
    "LLMConfig",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "LLMCallRecord",
    "LLMCallTracker",
    "MockLLMProvider",
    "OpenAIConfig",
    "get_llm",
    "init_llm",
    "reset_llm",
]

_llm_provider: LLMProvider | None = None


def get_llm() -> LLMProvider:
    """Get the global LLM Provider instance."""
    global _llm_provider
    if _llm_provider is None:
        raise RuntimeError("LLM Provider 未初始化 — 请先调用 init_llm()")
    return _llm_provider


def init_llm(provider: LLMProvider) -> None:
    """Initialize the global LLM Provider (call once at startup)."""
    global _llm_provider
    _llm_provider = provider


def reset_llm() -> None:
    """Reset the global LLM Provider (for testing)."""
    global _llm_provider
    _llm_provider = None
