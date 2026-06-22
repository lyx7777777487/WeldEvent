"""LLM Provider 抽象 — 文本大模型统一接口。

用于不需要视觉能力的场景：
  - VDA 的 NG 根因分析（纯文本推理）
  - RVA 审查意见生成
  - CAA 对话 Copilot
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LlmRequest(BaseModel):
    """统一 LLM 请求。"""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    messages: list[dict[str, str]]
    model: str | None = None
    response_format: type[BaseModel] | dict | None = None
    tools: list[dict[str, Any]] | None = None
    temperature: float = 0.3
    max_tokens: int = 2048
    caller: str = ""
    purpose: str = ""                   # reasoning / explanation / planning / chat


class LlmResponse(BaseModel):
    """统一 LLM 响应。"""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    content: str = ""
    parsed_object: Any | None = None
    tool_calls: list[dict[str, Any]] | None = None
    model_used: str = ""
    tokens_prompt: int = 0
    tokens_completion: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0


class LlmProvider(ABC):
    """LLM Provider 抽象 — 文本大模型统一入口。"""

    @abstractmethod
    async def complete(self, request: LlmRequest) -> LlmResponse:
        """同步补全（chat completions）。"""
        ...

    @abstractmethod
    async def stream(self, request: LlmRequest) -> AsyncGenerator[str, None]:
        """流式补全（长文本/对话场景）。"""
        ...

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """文本向量化（RAG 知识检索）。"""
        ...

    @abstractmethod
    def health_check(self) -> bool:
        """健康检查。"""
        ...
