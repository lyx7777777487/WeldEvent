"""MEA Activity 子模块 - 焊缝几何测量。

组件:
  - activity: MeaActivity 实现（核心执行单元）
  - geometry_engine: 确定性 CV 几何测量引擎（Canny + Hough 直线拟合）

使用方式::

    from executionplane.activities.mea import MeaActivity
    mea = MeaActivity(weldmap)
    output = await mea.execute(input)
"""

from .activity import MeaActivity
from .geometry_engine import GeometryEngine, GeometryMeasurement

__all__ = ["MeaActivity", "GeometryEngine", "GeometryMeasurement"]
