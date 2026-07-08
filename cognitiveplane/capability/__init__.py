"""LLM Provider abstraction layer — single entry point for all LLM calls.

Source: L1-Interaction-Layer-Business-Requirements.md §3.6.

Usage:
    from cognitiveplane.capability import get_llm, init_llm
    init_llm(MockLLMProvider())  # or OpenAIProvider(config)
    provider = get_llm()
    resp = await provider.complete(LLMRequest(...))
"""

from cognitiveplane.capability.config import LLMConfig, OpenAIConfig
from cognitiveplane.capability.mock import MockLLMProvider
from cognitiveplane.capability.provider import LLMProvider, LLMRequest, LLMResponse
from cognitiveplane.capability.tracking import LLMCallRecord, LLMCallTracker

__all__ = [
    "LLMConfig",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "LLMCallRecord",
    "LLMCallTracker",
    "MockLLMProvider",
    "OpenAIConfig",
    "get_llm",        # deprecated: 改用 CognitiveDependencies 构造注入 (Phase 5+ 移除)
    "init_llm",       # deprecated: 改用 CognitiveDependencies 构造注入 (Phase 5+ 移除)
    "reset_llm",      # deprecated: 改用 CognitiveDependencies 构造注入 (Phase 5+ 移除)
]

_llm_provider: LLMProvider | None = None


def get_llm() -> LLMProvider:
    """Get the global LLM Provider instance.

    .. deprecated::
        使用 CognitiveDependencies 构造注入替代全局单例。
        此函数保留向后兼容，将在 Phase 5+ 移除。
    """
    global _llm_provider
    if _llm_provider is None:
        raise RuntimeError("LLM Provider 未初始化 — 请先调用 init_llm()")
    import warnings
    warnings.warn(
        "get_llm() 全局单例已废弃，请改用 CognitiveDependencies 构造注入 (Phase 5+ 迁移)",
        DeprecationWarning,
        stacklevel=2,
    )
    return _llm_provider


def init_llm(provider: LLMProvider) -> None:
    """Initialize the global LLM Provider (call once at startup).

    .. deprecated::
        使用 CognitiveDependencies 构造注入替代全局单例。
        此函数保留向后兼容，将在 Phase 5+ 移除。
    """
    global _llm_provider
    _llm_provider = provider
    import warnings
    warnings.warn(
        "init_llm() 全局单例已废弃，请改用 CognitiveDependencies 构造注入 (Phase 5+ 迁移)",
        DeprecationWarning,
        stacklevel=2,
    )


def reset_llm() -> None:
    """Reset the global LLM Provider (for testing)."""
    global _llm_provider
    _llm_provider = None
