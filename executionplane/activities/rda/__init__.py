"""RDA Activity 子模块 - 焊缝表面缺陷识别。

组件:
  - activity: RdaActivity 实现（核心执行单元）
  - defect_engine: 确定性 CV 缺陷检测引擎

使用方式::

    from executionplane.activities.rda import RdaActivity
    rda = RdaActivity(weldmap)
    output = await rda.execute(input)
"""

from .activity import RdaActivity
from .defect_engine import DefectEngine, DefectReport, DefectFinding

__all__ = ["RdaActivity", "DefectEngine", "DefectReport", "DefectFinding"]
