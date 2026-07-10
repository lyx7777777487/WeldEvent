"""MockLLMProvider — canned responses for testing without a real LLM.

Source: L1-Interaction-Layer-Business-Requirements.md §3.6.9 (可测试性).
"""

from collections.abc import AsyncGenerator

from cognitiveplane.capability.provider import LLMProvider, LLMRequest, LLMResponse


class MockLLMProvider(LLMProvider):
    """Mock LLM provider that returns canned or default responses."""

    def __init__(
        self,
        canned_responses: dict[str, str] | None = None,
        default_response: str = "mock response",
        canned_json: dict | None = None,
        embedding_dim: int = 8,
    ) -> None:
        self._canned = canned_responses or {}
        self._default = default_response
        self._canned_json = canned_json
        self._embedding_dim = embedding_dim

    @property
    def supports_function_calling(self) -> bool:
        """Mock cannot produce real tool_calls — plan §2.2 LLMTier-2/3 fallback."""
        return False

    @property
    def supports_json_mode(self) -> bool:
        """Mock honors response_format via canned_json — LLMTier-2 available."""
        return True

    async def complete(self, request: LLMRequest) -> LLMResponse:
        # Skill classification requests — return null so keyword fallback is used.
        # This prevents mock LLMs from interfering with skill selection in tests.
        sys_msg = next((m.get("content", "") for m in request.messages if m.get("role") == "system"), "")
        if "意图分类器" in sys_msg:
            return LLMResponse(content='{"skill": null}', model_used="mock-classification")

        content = self._default
        for msg in request.messages:
            text = msg.get("content", "")
            for key, val in self._canned.items():
                if key in text:
                    content = val
                    break

        parsed_object = None
        if request.response_format is not None and self._canned_json is not None:
            try:
                if isinstance(request.response_format, type):
                    parsed_object = request.response_format(**self._canned_json)
                else:
                    parsed_object = self._canned_json
            except Exception:
                parsed_object = None

        return LLMResponse(
            content=content,
            parsed_object=parsed_object,
            model_used="mock",
        )

    async def stream(self, request: LLMRequest) -> AsyncGenerator[str, None]:
        content = self._default
        for msg in request.messages:
            text = msg.get("content", "")
            for key, val in self._canned.items():
                if key in text:
                    content = val
                    break
        words = content.split()
        for i, word in enumerate(words):
            yield word if i == 0 else " " + word

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * self._embedding_dim for _ in texts]

    def health_check(self) -> bool:
        return True
