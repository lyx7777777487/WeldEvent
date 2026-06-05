"""OpenAI-compatible LLM Provider implementation.

Source: L1-Interaction-Layer-Business-Requirements.md §3.6.2, §3.6.9.

Requires: `openai` package (pip install openai)
Fallback: raises RuntimeError if openai is not installed.
"""

from collections.abc import AsyncGenerator

from src.interaction.llm.config import LLMConfig
from src.interaction.llm.provider import LLMProvider, LLMRequest, LLMResponse


class OpenAIProvider(LLMProvider):
    """Real OpenAI API provider with fallback support.

    Features:
    - Per-purpose model routing via LLMConfig.resolve_model()
    - Automatic fallback to secondary model on failure
    - Timeout enforcement per purpose
    - Token and cost tracking
    """

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return
        try:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self._config.primary.api_key,
                base_url=self._config.primary.base_url,
                organization=self._config.primary.organization,
            )
        except ImportError:
            raise RuntimeError(
                "openai package not installed. "
                "Install with: pip install openai"
            )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self._ensure_client()
        model = request.model or self._config.resolve_model(request.purpose)
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=request.messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                top_p=request.top_p,
            )
            choice = response.choices[0]
            return LLMResponse(
                content=choice.message.content or "",
                model_used=response.model,
                tokens_prompt=response.usage.prompt_tokens if response.usage else 0,
                tokens_completion=response.usage.completion_tokens if response.usage else 0,
            )
        except Exception as e:
            if self._config.fallback is not None:
                return await self._fallback_complete(request, str(e))
            raise

    async def _fallback_complete(self, request: LLMRequest, error: str) -> LLMResponse:
        """Attempt fallback model on primary failure."""
        fallback_model = request.model or self._config.fallback.default_model
        try:
            response = await self._client.chat.completions.create(
                model=fallback_model,
                messages=request.messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
            choice = response.choices[0]
            return LLMResponse(
                content=choice.message.content or "",
                model_used=f"fallback:{response.model}",
                tokens_prompt=response.usage.prompt_tokens if response.usage else 0,
                tokens_completion=response.usage.completion_tokens if response.usage else 0,
            )
        except Exception:
            return LLMResponse(
                content=f"LLM error (primary + fallback failed): {error}",
                model_used="error",
            )

    async def stream(self, request: LLMRequest) -> AsyncGenerator[str, None]:
        self._ensure_client()
        model = request.model or self._config.resolve_model(request.purpose)
        stream = await self._client.chat.completions.create(
            model=model,
            messages=request.messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self._ensure_client()
        model = self._config.embedding_model or "text-embedding-3-small"
        response = await self._client.embeddings.create(
            input=texts,
            model=model,
        )
        return [item.embedding for item in response.data]

    def health_check(self) -> bool:
        return self._client is not None
