"""Mock MLLM Provider — 用于开发/测试，不调用真实模型。"""

from typing import Any

from .llm_provider import LlmProvider, LlmRequest, LlmResponse
from .mllm_provider import (
    ImageInput,
    MllmProvider,
    MllmRequest,
    MllmResponse,
    MllmPurpose,
    VisualRegion,
)


class MockMllmProvider(MllmProvider):
    """Mock MLLM Provider — 返回预设响应，不调用真实模型。"""

    def __init__(self, default_response: str = "(mock MLLM response)") -> None:
        self._default = default_response

    async def analyze(self, request: MllmRequest) -> MllmResponse:
        return MllmResponse(
            content=self._default,
            model_used="mock-mllm-v1",
            latency_ms=10,
        )

    async def chat_with_image(
        self, images: list[ImageInput], question: str
    ) -> str:
        return f"[Mock MLLM] 关于图像的问题: {question}"

    async def inspect_image(
        self, image: ImageInput, criteria: dict[str, Any]
    ) -> dict[str, Any]:
        checks = criteria.get("check", [])
        return {
            "findings": [f"[Mock] 图像{c}正常" for c in checks],
            "anomalies": [],
            "confidence": 0.95,
            "model_used": "mock-mllm-v1",
        }

    def health_check(self) -> bool:
        return True


class MockLlmProvider(LlmProvider):
    """Mock LLM Provider — 用于不需要 LLM 能力的场景。

    P2-6 fix: 现在是 LlmProvider ABC 的正式子类（类型安全）。
    """

    def __init__(self, default_response: str = "(mock LLM response)") -> None:
        self._default = default_response

    async def complete(self, request: LlmRequest) -> LlmResponse:
        return LlmResponse(content=self._default, model_used="mock-llm")

    async def stream(self, request: LlmRequest):
        yield self._default

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 16 for _ in texts]

    def health_check(self) -> bool:
        return True
