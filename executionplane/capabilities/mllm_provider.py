"""MLLM Provider 抽象 — 多模态大模型统一接口。

Source: Complete_architecture_V1.docx §3.1 (深度视觉层)

多模态 LLM 用于:
  - IQA 深度视觉层：检测异常遮挡/镜头污染/异物/反光
  - VDA 判定推理：结合图像+标准做风险评估
  - CAA 对话交互：用户发图问答

L3 Execution Plane 独立的 MLLM 实例（不与 L1 Cognitive Plane 共享，
见 boundary-pinning §1.1 — 认知 MCP vs 工业执行 MCP 不共享实现）。
调用模式:
  - L1 Cognitive: 对话式，低频，面向用户
  - L3 Execution: 批处理式，高频，结构化输出
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from enum import Enum

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 数据类型
# ---------------------------------------------------------------------------

class ImageInput(BaseModel):
    """MLLM 图像输入。"""
    image_data: bytes | None = None       # 原始字节 (PNG/JPEG)
    image_url: str | None = None          # URL 或 base64
    image_path: str | None = None         # 本地文件路径
    image_array_shape: tuple[int, ...] | None = None  # (H, W, C) 形状信息


class MllmPurpose(str, Enum):
    """MLLM 调用目的 — 用于路由和计费。"""
    CHAT = "chat"                         # 对话式 (大脑用)
    JUDGE = "judge"                       # 判定推理 (VDA 用)
    INSPECT = "inspect"                   # 图像检测 (IQA 深度视觉用)
    ANOMALY_DETECT = "anomaly_detect"     # 异常检测 (IQA 深度视觉用)


class VisualRegion(BaseModel):
    """视觉定位区域（框选结果）。"""
    x1: float
    y1: float
    x2: float
    y2: float
    label: str = ""
    confidence: float = 0.0


class MllmRequest(BaseModel):
    """多模态请求。"""
    images: list[ImageInput] = Field(default_factory=list)
    text: str = ""
    purpose: MllmPurpose = MllmPurpose.INSPECT
    response_format: type[BaseModel] | dict | None = None  # 结构化输出 schema
    temperature: float = 0.3
    max_tokens: int = 2048


class MllmResponse(BaseModel):
    """多模态响应。"""
    content: str = ""
    parsed_object: Any | None = None     # 结构化解析结果
    visual_regions: list[VisualRegion] = Field(default_factory=list)
    model_used: str = ""
    latency_ms: int = 0
    tokens_prompt: int = 0
    tokens_completion: int = 0


# ---------------------------------------------------------------------------
# Provider 接口
# ---------------------------------------------------------------------------

class MllmProvider(ABC):
    """MLLM Provider 抽象 — 多模态大模型统一入口。

    实现可以是:
      - OpenAI-compatible API (Qwen2.5-VL, GPT-4o, DeepSeek-VL)
      - 本地 vLLM/Ollama 部署
      - Mock (开发/测试)
    """

    @abstractmethod
    async def analyze(self, request: MllmRequest) -> MllmResponse:
        """通用多模态分析。"""
        ...

    @abstractmethod
    async def chat_with_image(
        self, images: list[ImageInput], question: str
    ) -> str:
        """便捷方法：带图对话（Cognitive Plane 用）。"""
        ...

    @abstractmethod
    async def inspect_image(
        self, image: ImageInput, criteria: dict[str, Any]
    ) -> dict[str, Any]:
        """便捷方法：图像检测 + 结构化输出（Agent Pool 用）。
        
        Args:
            image: 输入图像
            criteria: 检测标准，如 {"check": ["blur", "noise", "occlusion"]}

        Returns:
            结构化检测结果字典。
        """
        ...

    @abstractmethod
    def health_check(self) -> bool:
        """健康检查。"""
        ...
