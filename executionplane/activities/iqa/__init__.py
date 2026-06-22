"""IQA Activity子模块 — 图像质量评估。

组件:
  - activity: IqaActivity实现
  - config: QualityStandard配置系统
  - interface: 用户接口（run_iqa等）

使用方式:
  ```python
  from executionplane.activities.iqa import run_iqa, IqaActivity
  
  # 使用接口
  result = await run_iqa("/path/to/image.png")
  
  # 使用Activity
  iqa = IqaActivity(weldmap, cv_checker, mllm)
  output = await iqa.execute(input)
  ```
"""

from .activity import IqaActivity
from .interface import (
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
from .config import (
    QualityStandard,
    QualityStandardRegistry,
)

__all__ = [
    # Activity
    "IqaActivity",
    # 接口
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