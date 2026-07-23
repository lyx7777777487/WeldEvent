"""MEA / RDA Activity 单元测试 - 验证确定性几何测量与缺陷识别。

测试层级：直接调 Activity.execute()（L3 单元），不经 execute_node/Temporal
（后者依赖 activity context，见 test_l2_l3_integration.py）。

验证矩阵:
  MEA: 合格(OK) / 未标定(MARGINAL) / 焊脚不足(NG) / annotations 写入
  RDA: 干净(MARGINAL低置信) / 气孔超限(MARGINAL) / 裂纹(NG) / annotations 合并不覆盖 MEA
"""

from __future__ import annotations

import asyncio
import os
import sys

import numpy as np
import pytest
import cv2

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from executionplane.activities.base import ActivityInput
from executionplane.activities.mea import MeaActivity
from executionplane.activities.rda import RdaActivity
from executionplane.weldmap.in_memory import InMemoryWeldMapClient
from executionplane.activities.base import ActivityStatus
from executionplane.weldmap.models import WorkflowId

GRAY = (200, 200, 200)
HI = (240, 240, 240)
DARK = (20, 20, 20)
CRACK = (15, 15, 15)


def _t_joint_weld(leg_px: int = 120) -> np.ndarray:
    """合成 T 型角焊缝图（正确 RGB 元组颜色，避免单通道伪影）。"""
    img = np.full((600, 600, 3), 130, np.uint8)
    cv2.line(img, (0, 300), (600, 300), GRAY, 3)
    cv2.line(img, (300, 0), (300, 600), GRAY, 3)
    pts = np.array([[300, 300], [300 + leg_px, 300], [300, 300 + leg_px]], np.int32)
    cv2.fillPoly(img, [pts], GRAY)
    cv2.polylines(img, [pts], True, HI, 2)
    return img


def _add_porosity(img: np.ndarray, centers: list[tuple[int, int, int]]) -> np.ndarray:
    for cx, cy, r in centers:
        cv2.circle(img, (cx, cy), r, DARK, -1)
    return img


# ── MEA ──────────────────────────────────────────────────────────

class TestMeaActivity:
    @pytest.mark.asyncio
    async def test_calibrated_adequate_weld_passes(self):
        wm = InMemoryWeldMapClient()
        mea = MeaActivity(weldmap=wm)
        out = await mea.execute(ActivityInput(
            "CP2", {"workflow_id": "wf-mea-ok"},
            {"image_array": _t_joint_weld(), "pixels_per_mm": 8.0, "plate_thickness_mm": 12},
        ))
        assert out.status == ActivityStatus.OK
        assert out.data["route_decision"] == "AUTO_PASS"
        assert out.data["mock"] is False
        assert out.data["data_source"] == "deterministic_cv"
        # K_min = 0.7 * 12 = 8.4mm；120px / 8 = 15mm > 8.4
        assert out.data["measurements"]["weld_leg_1_mm"] > 8.4

    @pytest.mark.asyncio
    async def test_uncalibrated_routes_to_review(self):
        wm = InMemoryWeldMapClient()
        mea = MeaActivity(weldmap=wm)
        out = await mea.execute(ActivityInput(
            "CP2", {"workflow_id": "wf-mea-uncal"}, {"image_array": _t_joint_weld()},
        ))
        assert out.status == ActivityStatus.MARGINAL
        assert out.data["route_decision"] == "SUGGEST_REVIEW"
        assert out.data["measurements"]["calibrated"] is False

    @pytest.mark.asyncio
    async def test_undersized_weld_rejected(self):
        wm = InMemoryWeldMapClient()
        mea = MeaActivity(weldmap=wm)
        # 30px 焊脚 @ 8px/mm = 3.75mm < K_min 8.4mm
        out = await mea.execute(ActivityInput(
            "CP2", {"workflow_id": "wf-mea-ng"},
            {"image_array": _t_joint_weld(leg_px=30), "pixels_per_mm": 8.0, "plate_thickness_mm": 12},
        ))
        assert out.status == ActivityStatus.NG
        assert out.data["route_decision"] == "REJECT"

    @pytest.mark.asyncio
    async def test_writes_annotations_to_weldmap(self):
        wm = InMemoryWeldMapClient()
        mea = MeaActivity(weldmap=wm)
        await mea.execute(ActivityInput(
            "CP2", {"workflow_id": "wf-mea-ann"},
            {"image_array": _t_joint_weld(), "pixels_per_mm": 8.0, "plate_thickness_mm": 12},
        ))
        ann = await wm.read_annotations(WorkflowId("wf-mea-ann"))
        assert ann is not None
        assert "weld_leg_1" in ann.elements
        assert "weld_leg_2" in ann.elements
        assert "weld_throat" in ann.elements


# ── RDA ──────────────────────────────────────────────────────────

class TestRdaActivity:
    @pytest.mark.asyncio
    async def test_crack_detected_rejects(self):
        wm = InMemoryWeldMapClient()
        mea, rda = MeaActivity(weldmap=wm), RdaActivity(weldmap=wm)
        img = _t_joint_weld()
        cv2.line(img, (330, 300), (390, 360), CRACK, 2)
        await mea.execute(ActivityInput("CP2", {"workflow_id": "wf-crack"},
            {"image_array": img, "pixels_per_mm": 8.0, "plate_thickness_mm": 12}))
        out = await rda.execute(ActivityInput("CP3", {"workflow_id": "wf-crack"},
            {"image_array": img, "pixels_per_mm": 8.0}))
        assert out.status == ActivityStatus.NG
        assert out.data["route_decision"] == "REJECT"
        assert out.data["has_crack"] is True
        assert out.data["mock"] is False

    @pytest.mark.asyncio
    async def test_porosity_over_limit_routes_to_review(self):
        wm = InMemoryWeldMapClient()
        mea, rda = MeaActivity(weldmap=wm), RdaActivity(weldmap=wm)
        img = _add_porosity(_t_joint_weld(), [(340, 312, 4), (355, 322, 4), (370, 332, 4), (385, 318, 4), (348, 338, 4)])
        await mea.execute(ActivityInput("CP2", {"workflow_id": "wf-poro"},
            {"image_array": img, "pixels_per_mm": 8.0, "plate_thickness_mm": 12}))
        out = await rda.execute(ActivityInput("CP3", {"workflow_id": "wf-poro"},
            {"image_array": img, "pixels_per_mm": 8.0, "porosity_max_count": 3}))
        assert out.status == ActivityStatus.MARGINAL
        assert out.data["route_decision"] == "SUGGEST_REVIEW"
        assert out.data["porosity_count"] > 3

    @pytest.mark.asyncio
    async def test_clean_image_no_false_crack(self):
        wm = InMemoryWeldMapClient()
        mea, rda = MeaActivity(weldmap=wm), RdaActivity(weldmap=wm)
        img = _t_joint_weld()
        await mea.execute(ActivityInput("CP2", {"workflow_id": "wf-clean"},
            {"image_array": img, "pixels_per_mm": 8.0, "plate_thickness_mm": 12}))
        out = await rda.execute(ActivityInput("CP3", {"workflow_id": "wf-clean"},
            {"image_array": img, "pixels_per_mm": 8.0}))
        # 干净图不应误判裂纹（B 级不允许裂纹，误判会直接 NG）
        assert out.data["has_crack"] is False
        assert out.status != ActivityStatus.NG or out.data["route_decision"] != "REJECT"

    @pytest.mark.asyncio
    async def test_rda_merges_annotations_preserving_mea(self):
        wm = InMemoryWeldMapClient()
        mea, rda = MeaActivity(weldmap=wm), RdaActivity(weldmap=wm)
        img = _t_joint_weld()
        cv2.line(img, (330, 300), (390, 360), CRACK, 2)
        await mea.execute(ActivityInput("CP2", {"workflow_id": "wf-merge"},
            {"image_array": img, "pixels_per_mm": 8.0, "plate_thickness_mm": 12}))
        await rda.execute(ActivityInput("CP3", {"workflow_id": "wf-merge"},
            {"image_array": img, "pixels_per_mm": 8.0}))
        ann = await wm.read_annotations(WorkflowId("wf-merge"))
        # MEA 几何标注保留 + RDA 缺陷标注追加
        assert "weld_leg_1" in ann.elements
        assert any(k.startswith("defect_") for k in ann.elements)
