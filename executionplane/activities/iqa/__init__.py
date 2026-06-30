"""IQA Activity子模块 — 图像质量评估。

组件:
  - activity: IqaActivity 实现（核心执行单元）
  - 用户接口（run_iqa 等）统一从 executionplane.interface 导入

使用方式:
  ```python
  # 使用接口（推荐）
  from executionplane.interface import run_iqa, IqaResult
  result = await run_iqa("/path/to/image.png")

  # 使用 Activity（L3 内部）
  from executionplane.activities.iqa import IqaActivity
  iqa = IqaActivity(weldmap, cv_checker, mllm)
  output = await iqa.execute(input)
  ```
"""

from .activity import IqaActivity

# 用户接口统一从 executionplane.interface 转发（避免重复实现）
from ...interface import (
    run_iqa,
    run_iqa_batch,
    run_iqa_folder,
    run_iqa_sync,
    run_iqa_batch_sync,
    run_iqa_folder_sync,
    IqaResult,
    list_available_standards,
    get_standard_info,
    save_results_to_json,
    print_summary,
)
from ...config.quality_standard import (
    QualityStandard,
    QualityStandardRegistry,
)

__all__ = [
    # Activity
    "IqaActivity",
    # 接口（转发自 executionplane.interface）
    "run_iqa",
    "run_iqa_batch",
    "run_iqa_folder",
    "run_iqa_sync",
    "run_iqa_batch_sync",
    "run_iqa_folder_sync",
    "IqaResult",
    "list_available_standards",
    "get_standard_info",
    "save_results_to_json",
    "print_summary",
    # 配置
    "QualityStandard",
    "QualityStandardRegistry",
]
