"""PPA Activity子模块 — 图像预处理。

组件:
  - activity: PpaActivity实现
  - interface: 用户接口（run_ppa等）
  - strategies: 预处理策略配置

使用方式:
  ```python
  from executionplane.activities.ppa import run_ppa, PpaActivity
  
  # 使用接口
  result = await run_ppa("/path/to/image.png", iqa_result)
  
  # 使用Activity
  ppa = PpaActivity(weldmap, preprocessor)
  output = await ppa.execute(input)
  ```
"""

from .activity import PpaActivity

__all__ = ["PpaActivity"]