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

    temperature: float = 0.3
    max_tokens: int = 2048
    top_p: float = 0.9

    caller: str = ""
    case_id: str | None = None
    purpose: str = ""


class LLMResponse(BaseModel):
    """Unified LLM response with tracking metadata."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    content: str
    parsed_object: Any | None = None
    tool_calls: list[dict] | None = None

    model_used: str = ""
    tokens_prompt: int = 0
    tokens_completion: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0


class LLMProvider(ABC):
    """LLM Provider interface — single entry point for all LLM calls.

    Source: L1-Interaction-Layer-Business-Requirements.md §3.6.2.
    """

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
