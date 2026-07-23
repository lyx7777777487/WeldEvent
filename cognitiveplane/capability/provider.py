"""LLMProvider ABC, LLMRequest, LLMResponse.

Source: L1-Interaction-Layer-Business-Requirements.md §3.6.2–3.6.3.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LLMRequest(BaseModel):
    """Unified LLM request — all LLM calls use this schema."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    messages: list[dict]
    model: str | None = None

    response_format: type | dict | None = None
    tools: list[dict] | None = None

    temperature: float = 0.3  # Agent 场景默认低 temperature 以保证 system prompt 遵从度
    max_tokens: int = 8192
    top_p: float = 0.95

    caller: str = ""
    case_id: str | None = None
    purpose: str = ""
    # Op 36: Prompt caching - number of tokens in the stable cache prefix.
    # Provider can use this to set cache_control on the prefix messages.
    # 0 = no caching (default). Set by _make_llm_request when CACHE_BOUNDARY found.
    cache_prefix_tokens: int = 0


class LLMResponse(BaseModel):
    """Unified LLM response with tracking metadata."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    content: str
    parsed_object: Any | None = None
    tool_calls: list[dict] | None = None
    # thinking 模式（GLM-4.6 / DeepSeek-R1 / Qwen3 等）返回的推理内容。
    # 多轮对话时必须回传给 API，否则触发 "reasoning_content must be passed back" 校验错误。
    reasoning_content: str | None = None

    model_used: str = ""
    tokens_prompt: int = 0
    tokens_completion: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0


class LLMProvider(ABC):
    """LLM Provider interface — single entry point for all LLM calls.

    Source: L1-Interaction-Layer-Business-Requirements.md §3.6.2.
    """

    @property
    def supports_vision(self) -> bool:
        """Whether the *primary* chat-completions model accepts image_url
        content parts. False for text-only models like DeepSeek-chat; True
        for multimodal primaries. Used by ReActEngine to decide whether to
        forward multimodal content to `complete()` or degrade to text-only.

        Subclasses representing multimodal primaries should override.
        Vision-Complete (tool-layer) is independent of this flag — see
        `vision_complete`.
        """
        return False

    @property
    def supports_function_calling(self) -> bool:
        """Whether the primary model supports OpenAI-style function calling
        (tool_calls in the response). Plan §2.2 LLMTier-1 requires this.

        Default True — realistic LLM providers (OpenAI, DeepSeek, etc.) all
        support function calling. Mocks / stubs that can't produce tool_calls
        should override to False so ReActEngine falls back to LLMTier-2/3.
        """
        return True

    @property
    def supports_json_mode(self) -> bool:
        """Whether the primary model supports JSON mode (response_format=
        {"type":"json_object"}). Plan §2.2 LLMTier-2 requires this as the
        fallback when function calling is unavailable.

        Default True — most OpenAI-compatible APIs support JSON mode. Mocks
        that can't produce structured JSON should override to False so
        ReActEngine falls back to LLMTier-3 (keyword rules).
        """
        return True

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Synchronous completion (chat completions)."""
        ...

    @abstractmethod
    async def stream(self, request: LLMRequest) -> AsyncGenerator[str, None]:
        """Stream completion (async generator for long text / dialogue)."""
        ...

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Text vectorization (RAG knowledge retrieval)."""
        ...

    @abstractmethod
    def health_check(self) -> bool:
        """Health check."""
        ...
