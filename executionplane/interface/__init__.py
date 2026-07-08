"""IQA接口 — 图像质量检测入口。

使用方法:
  1. 设置图片路径（单张或批量）
  2. 调用run_iqa()执行检测
  3. 查看检测结果

示例:
  ```python
  from executionplane.interface import run_iqa, IqaResult

  # 单张图片检测
  result = await run_iqa("/path/to/image.png")
  print(result.route_decision)

  # 批量检测
  results = await run_iqa_batch([
      "/path/to/image1.png",
      "/path/to/image2.png",
  ])

  # 使用自定义标准
  result = await run_iqa(
      "/path/to/image.png",
      standard_id="high_speed_line",
  )
  ```
"""

from .async_api import run_iqa, run_iqa_batch, run_iqa_folder
from .config import (
    configure_mllm,
    get_standard_info,
    list_available_standards,
    register_standard,
)
from .io import print_summary, save_results_to_json
from .result import IqaResult
from .sync_api import run_iqa_batch_sync, run_iqa_folder_sync, run_iqa_sync

__all__ = [
    # 结果数据结构
    "IqaResult",
    # 配置与标准
    "configure_mllm",
    "register_standard",
    "list_available_standards",
    "get_standard_info",
    # 异步接口
    "run_iqa",
    "run_iqa_batch",
    "run_iqa_folder",
    # 同步接口
    "run_iqa_sync",
    "run_iqa_batch_sync",
    "run_iqa_folder_sync",
    # 结果输出
    "save_results_to_json",
    "print_summary",
]
