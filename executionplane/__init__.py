"""Execution Plane — 图像质量检测执行层。

核心组件:
  - activities: Activity执行单元（IqaActivity、PpaActivity等）
  - capabilities: 能力组件（CV规则、MLLM、预处理）
  - weldmap: WeldMap黑板客户端

子模块结构:
  - activities/iqa: 图像质量评估子模块
  - activities/ppa: 图像预处理子模块
  - activities/mea: 缺陷检测子模块（待实现）
  - ...

使用方式:
  ```python
  # 使用IQA接口
  from executionplane.activities.iqa import run_iqa
  result = await run_iqa("/path/to/image.png")
  
  # 使用Activity
  from executionplane.activities import IqaActivity, PpaActivity
  iqa = IqaActivity(weldmap, cv_checker, mllm)
  output = await iqa.execute(input)
  ```
"""

# 导出Activity基类
from .activities import (
    BaseActivity,
    ActivityInput,
    ActivityOutput,
    ActivityStatus,
    ActivityMetadata,
    IqaActivity,
    PpaActivity,
)

# 导出IQA接口（便捷访问）
from .activities.iqa import (
    run_iqa,
    run_iqa_batch,
    run_iqa_folder,
    run_iqa_sync,
    IqaResult,
    list_available_standards,
    get_standard_info,
)

__all__ = [
    # Activity基类
    "BaseActivity",
    "ActivityInput",
    "ActivityOutput",
    "ActivityStatus",
    "ActivityMetadata",
    # Activity实现
    "IqaActivity",
    "PqaActivity",
    # IQA接口
    "run_iqa",
    "run_iqa_batch",
    "run_iqa_folder",
    "run_iqa_sync",
    "IqaResult",
    "list_available_standards",
    "get_standard_info",
]