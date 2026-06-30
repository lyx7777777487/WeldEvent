"""Capabilities — L3 执行层的能力提供者抽象。

职责划分:
  - mllm_provider.py: 多模态 LLM 抽象（IQA 深度视觉触发时使用，通用）
  - mock_providers.py: 上述 provider 的 mock 实现（测试用）
  - llm_provider.py:  纯文本 LLM 抽象（预留，L3 暂无真实实现者）
  - cv_rules.py:      CV 规则检查器接口（IQA 专用，pool.register_iqa 暴露）
  - numpy_cv_checker.py: CV 规则的 numpy 实现（IQA 专用）
  - vision/preprocessor.py: 图像预处理 Pipeline（IQA + PPA 共用）

设计说明:
  - cv_rules / numpy_cv_checker 虽然只有 IQA 用，但保留在 capabilities/
    是因为 pool.py 的 register_iqa() 签名需要 CVRuleChecker 类型，
    放在 capabilities/ 避免 pool.py 深挖 activities/iqa/ 内部。
  - llm_provider 暂无真实实现者，保留为未来 VDA/RVA 的预留抽象。
"""

from .cv_rules import CVRuleChecker
from .llm_provider import LlmProvider, LlmRequest, LlmResponse
from .mllm_provider import (
    ImageInput,
    MllmProvider,
    MllmRequest,
    MllmResponse,
    MllmPurpose,
    VisualRegion,
)
from .mock_providers import MockLlmProvider, MockMllmProvider
from .numpy_cv_checker import NumpyCVRuleChecker

__all__ = [
    "LlmProvider", "LlmRequest", "LlmResponse",
    "MllmProvider", "MllmRequest", "MllmResponse", "MllmPurpose",
    "ImageInput", "VisualRegion",
    "CVRuleChecker", "NumpyCVRuleChecker",
    "MockLlmProvider", "MockMllmProvider",
]
