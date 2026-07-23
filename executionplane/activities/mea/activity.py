"""MEA Activity - 焊缝几何测量执行单元。

Source: docs/WELD_INSPECTION_FLOW_EXAMPLES.md 阶段5②（从 MLLM 估计变真实测量）
        docs/INDUSTRIAL_AGENT_BENCHMARK.md（确定性测量 + 诚实置信度）

设计原则（与 IQA 一致，区别于 mock）:
  - 确定性 CV 几何测量（GeometryEngine），非 LLM 估计
  - 关键判定依据（焊脚最小尺寸 K_min）由用户在阶段2确认的阈值驱动，不靠 LLM 默认
  - 诚实标注数据来源：标定缺失/置信度低 -> MARGINAL（建议复核），绝不假合格
  - 测量结果写入 WeldMap annotations（weldmap:///{wf_id}/annotations/）

判定逻辑（GB/T 19418 角焊缝构造要求）:
  - 焊脚最小尺寸 K_min = 0.7 × 板厚（构造要求）
  - 用户可经阶段2确认覆盖：weld_leg_min_mm
  - 焊脚 < K_min -> NG（硬指标）
  - 未标定(无 pixels_per_mm) -> MARGINAL（仅有像素值，不能下 mm 结论）
"""

import logging
from typing import Any

from numpy.typing import NDArray

from ..base import (
    BaseActivity,
    ActivityInput,
    ActivityOutput,
    ActivityStatus,
    ActivityMetadata,
)
from .geometry_engine import GeometryEngine, GeometryMeasurement
from ...capabilities.vision.preprocessor import (
    ImagePreprocessingPipeline,
    DecodeStep,
    ValidateStep,
    ColorConvertStep,
)
from ...weldmap.client import WeldMapClient
from ...weldmap.models import (
    WorkflowId,
    AnnotationsData,
    AnnotationElement,
)

logger = logging.getLogger(__name__)

# 角焊缝构造要求：焊脚最小尺寸系数（GB/T 19418 构造要求 K_min = 0.7 × 板厚）
_K_MIN_FACTOR = 0.7
# 置信度低于此值 -> 即使标定也走 MARGINAL（检测质量不足以支撑确定结论）
_MIN_CONFIDENCE_FOR_OK = 0.55


class MeaActivity(BaseActivity):
    """焊缝几何测量 Activity。

    Usage::

        mea = MeaActivity(weldmap=weldmap_client)
        output = await mea.execute(ActivityInput(
            control_point_id="CP2",
            workflow_context={"workflow_id": "WF-001"},
            params={"image_path": "/path/to/image.png",
                    "plate_thickness_mm": 12,
                    "pixels_per_mm": 8.0},
        ))
        # output.data["measurements"]["weld_leg_1_mm"]
        # output.status  # OK / MARGINAL / NG
    """

    def __init__(
        self,
        weldmap: WeldMapClient,
        engine: GeometryEngine | None = None,
        preprocessor: ImagePreprocessingPipeline | None = None,
    ):
        self._weldmap = weldmap
        self._engine = engine or GeometryEngine()
        self._preprocessor = preprocessor or self._create_default_preprocessor()
        self._metadata = ActivityMetadata(
            activity_id="mea_activity",
            name="几何测量",
            version="v1",
            description="确定性 CV 几何测量：焊脚 K1/K2、焊喉、两焊脚差",
            capabilities=["cv", "geometry", "deterministic"],
            execution_target="cpu",
            estimated_latency_ms=150,
        )

    @property
    def activity_name(self) -> str:
        return "mea_activity"

    @property
    def metadata(self) -> ActivityMetadata:
        return self._metadata

    def _create_default_preprocessor(self) -> ImagePreprocessingPipeline:
        return ImagePreprocessingPipeline(steps=[
            DecodeStep(),
            ValidateStep(min_size=64, max_dimension=16384),
            ColorConvertStep(target="RGB"),
        ])

    async def execute(self, input: ActivityInput) -> ActivityOutput:
        workflow_id = input.workflow_context.get("workflow_id", "")
        wf_id = WorkflowId(workflow_id)
        params = input.params or {}

        preprocessed = await self._preprocessor.run(params=params)
        if not preprocessed.metadata.is_valid:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error=f"预处理失败: {preprocessed.metadata.error}",
                data={"preprocessing_error": preprocessed.metadata.error},
            )
        image = preprocessed.image
        if image is None:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error="预处理后图像为空",
            )

        measurement = self._engine.measure(image, params)
        k_min = self._resolve_k_min(params)
        verdict, route, status = self._judge(measurement, params, k_min)

        # 写入 WeldMap annotations（理论根部 + 焊脚测量元素）
        annotations = self._build_annotations(measurement)
        try:
            await self._weldmap.write_annotations(wf_id, annotations, source="mea_agent")
        except Exception as e:
            logger.error("WeldMap写入annotations失败 (workflow=%s): %s: %s - 将由 Temporal 重试",
                         wf_id, type(e).__name__, e, exc_info=True)
            raise RuntimeError(f"WeldMap写入失败: {type(e).__name__}: {e}") from e

        return self._map_to_output(measurement, verdict, route, status, k_min)

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------

    def _judge(
        self,
        m: GeometryMeasurement,
        params: dict,
        k_min: float | None,
    ) -> tuple[str, str, ActivityStatus]:
        """返回 (verdict_text, route_decision, ActivityStatus)。

        路由原则（保守，绝不假合格）:
          - 标定完成 + 置信达标 + 焊脚 >= K_min -> AUTO_PASS / OK
          - 标定完成 + 焊脚 < K_min -> REJECT / NG（硬指标不合格）
          - 未标定 或 置信度过低 -> SUGGEST_REVIEW / MARGINAL
            （像素测量可得，但无 mm 结论或检测质量不足，需人工复核）
        """
        if not m.calibrated:
            return (
                "uncalibrated",
                "SUGGEST_REVIEW",
                ActivityStatus.MARGINAL,
            )
        if m.confidence < _MIN_CONFIDENCE_FOR_OK:
            return (
                "low_confidence",
                "SUGGEST_REVIEW",
                ActivityStatus.MARGINAL,
            )

        # k_min 由调用方解析后传入（见 _resolve_k_min）
        legs_mm = [v for v in (m.weld_leg_1_mm, m.weld_leg_2_mm) if v is not None]
        if k_min is not None and legs_mm:
            if min(legs_mm) < k_min:
                return (
                    f"weld_leg_below_kmin({k_min}mm)",
                    "REJECT",
                    ActivityStatus.NG,
                )
            return ("weld_leg_ok", "AUTO_PASS", ActivityStatus.OK)
        # 已标定但无板厚/阈值 -> 不能下硬指标结论，走复核
        return ("no_threshold_no_hard_verdict", "SUGGEST_REVIEW", ActivityStatus.MARGINAL)

    def _resolve_k_min(self, params: dict) -> float | None:
        """焊脚最小尺寸阈值（用户阶段2确认优先，否则 0.7×板厚）。"""
        explicit = params.get("weld_leg_min_mm") or params.get("min_leg_mm")
        if explicit:
            try:
                v = float(explicit)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                pass
        plate_mm = params.get("plate_thickness_mm")
        if plate_mm:
            try:
                return round(_K_MIN_FACTOR * float(plate_mm), 3)
            except (TypeError, ValueError):
                pass
        return None

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------

    def _build_annotations(self, m: GeometryMeasurement) -> AnnotationsData:
        elements: dict[str, AnnotationElement] = {}

        def add(eid: str, value_mm: float | None, px: float, cx: float = 0.0, cy: float = 0.0):
            elements[eid] = AnnotationElement(
                element_id=eid,
                geometry_px={"length_px": round(px, 2), "x": round(cx, 2), "y": round(cy, 2)},
                geometry_mm={"length_mm": round(value_mm, 3)} if value_mm is not None else {},
                value=value_mm,
                uncertainty=0.0,
                confidence=m.confidence,
                source=m.method,
                validation_status="unchecked",
            )

        add("weld_leg_1", m.weld_leg_1_mm, m.weld_leg_1_px)
        add("weld_leg_2", m.weld_leg_2_mm, m.weld_leg_2_px)
        add("weld_throat", m.throat_mm, m.throat_px)
        add("leg_difference", m.leg_difference_mm, m.leg_difference_px)
        return AnnotationsData(elements=elements)

    def _map_to_output(
        self,
        m: GeometryMeasurement,
        verdict: str,
        route: str,
        status: ActivityStatus,
        k_min: float | None,
    ) -> ActivityOutput:
        measurements = {
            "weld_leg_1_px": round(m.weld_leg_1_px, 2),
            "weld_leg_2_px": round(m.weld_leg_2_px, 2),
            "throat_px": round(m.throat_px, 2),
            "leg_difference_px": round(m.leg_difference_px, 2),
            "weld_leg_1_mm": m.weld_leg_1_mm,
            "weld_leg_2_mm": m.weld_leg_2_mm,
            "throat_mm": m.throat_mm,
            "leg_difference_mm": m.leg_difference_mm,
            "plate_angle_deg": m.plate_angle_deg,
            "calibrated": m.calibrated,
            "k_min_mm": k_min,
        }
        return ActivityOutput(
            status=status,
            data={
                "capability": "mea",
                "method": m.method,
                "measurements": measurements,
                "dimensions": {
                    "root_point_px": list(m.root_point_px) if m.root_point_px else None,
                    "toe_points_px": [list(p) for p in m.toe_points_px],
                },
                "confidence": m.confidence,
                "route_decision": route,
                "verdict": verdict,
                "notes": m.notes,
                "mock": False,
                "data_source": "deterministic_cv",
            },
            error=None,
        )

