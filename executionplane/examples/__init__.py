"""示例模块 — 使用示例代码。

包含:
  - iqa_example: IQA图像质量检测使用示例
"""

from .iqa_example import (
    example_single_image,
    example_batch_images,
    example_different_standards,
    example_without_mllm,
)

__all__ = [
    "example_single_image",
    "example_batch_images",
    "example_different_standards",
    "example_without_mllm",
]