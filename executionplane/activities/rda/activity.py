"""RDA Activity - 焊缝表面缺陷识别执行单元。

Source: docs/WELD_INSPECTION_FLOW_EXAMPLES.md 阶段5③（从 MLLM 判断变真实检测）
        docs/INDUSTRIAL_AGENT_BENCHMARK.md（保守置信度 + 确定性检测）

设计原则（与 IQA/MEA 一致）:
  - 确定性 CV 缺陷检测（DefectEngine），非 LLM 判断
  - 保守路由：裂纹(B级不允许)强检出即 NG；咬边超标即复核；弱信号绝不假合格
  - 标定缺失时仅给像素代理，路由到复核
  - 缺陷结果以 annotation 元素合并写入 WeldMap（保留 MEA 几何标注，不覆盖）

判定逻辑（GB/T 19418 表面缺陷，默认 B 级，可经阶段2确认覆盖）:
  - 裂纹 crack          -> 任何强检出 -> REJECT / NG（B 级不允许裂纹）
  - 咬边 undercut        -> depth_mm > undercut_max_mm(默认0.5) -> MANDATORY_REVIEW / NG
                            未标定但有疑似 -> SUGGEST_REVIEW / MARGINAL
  - 气孔 porosity        -> 数量 > porosity_max_count(默认3) -> SUGGEST_REVIEW / MARGINAL
  - 焊瘤 spatter         -> 汇总提示，不单独判废
  - 无检出 + 高置信     -> AUTO_PASS / OK
  - 无检出 + 低置信     -> SUGGEST_REVIEW / MARGINAL（检测不可靠，需复核）
"""

import logging
from typing import Any

from ..base import (
    BaseActivity,
    ActivityInput,
    ActivityOutput,
    ActivityStatus,
    ActivityMetadata,
)
from .defect_engine import DefectEngine, DefectReport, DefectFinding
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

# GB/T 19418 默认阈值（用户可在阶段2确认覆盖）
_DEFAULT_UNDERCUT_MAX_MM = 0.5
_DEFAULT_POROSITY_MAX_COUNT = 3
_MIN_CONFIDENCE_FOR_OK = 0.55
# 裂纹置信度达到此值才认作强检出（低于视为弱信号，走复核）
_CRACK_STRONG_CONFIDENCE = 0.4


class RdaActivity(BaseActivity):
    """焊缝表面缺陷识别 Activity。

    Usage::

        rda = RdaActivity(weldmap=weldmap_client)
        output = await rda.execute(ActivityInput(
            control_point_id="CP3",
            workflow_context={"workflow_id": "WF-001"},
            params={"image_path": "/path/to/image.png",
                    "pixels_per_mm": 8.0,
                    "undercut_max_mm": 0.5},
        ))
    """

    def __init__(
        self,
        weldmap: WeldMapClient,
        engine: DefectEngine | None = None,
        preprocessor: ImagePreprocessingPipeline | None = None,
    ):
        self._weldmap = weldmap
        self._engine = engine or DefectEngine()
        self._preprocessor = preprocessor or self._create_default_preprocessor()
        self._metadata = ActivityMetadata(
            activity_id="rda_activity",
            name="缺陷识别",
            version="v1",
            description="确定性 CV 表面缺陷检测：气孔/裂纹/咬边/焊瘤",
            capabilities=["cv", "defect", "deterministic"],
            execution_target="cpu",
            estimated_latency_ms=120,
        )

    @property
    def activity_name(self) -> str:
        return "rda_activity"

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

        report = self._engine.detect(image, params)
        calibrated = self._is_calibrated(params)
        verdict, route, status = self._judge(report, params, calibrated)

        # 合并写入 annotations：保留 MEA 几何标注 + 追加缺陷标注
        annotations = await self._merge_defects_into_annotations(wf_id, report)
        try:
            await self._weldmap.write_annotations(wf_id, annotations, source="rda_agent")
        except Exception as e:
            logger.error("WeldMap写入annotations失败 (workflow=%s): %s: %s - 将由 Temporal 重试",
                         wf_id, type(e).__name__, e, exc_info=True)
            raise RuntimeError(f"WeldMap写入失败: {type(e).__name__}: {e}") from e

        return self._map_to_output(report, verdict, route, status, calibrated)

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------

    def _is_calibrated(self, params: dict) -> bool:
        ppm = params.get("pixels_per_mm")
        if ppm:
            try:
                return float(ppm) > 0
            except (TypeError, ValueError):
                pass
        return bool(params.get("plate_thickness_mm") and params.get("plate_thickness_px"))

    def _judge(
        self,
        report: DefectReport,
        params: dict,
        calibrated: bool,
    ) -> tuple[str, str, ActivityStatus]:
        """保守判定：裂纹->NG；咬边超标->复核/NG；弱信号->复核；无缺陷高置信->OK。"""
        undercut_max_mm = self._param_float(params, "undercut_max_mm", _DEFAULT_UNDERCUT_MAX_MM)
        porosity_max = int(self._param_float(params, "porosity_max_count", _DEFAULT_POROSITY_MAX_COUNT))

        # 1. 裂纹：B 级不允许。强检出即 NG，弱检出走复核
        for d in report.defects:
            if d.defect_type == "crack":
                if d.confidence >= _CRACK_STRONG_CONFIDENCE:
                    return ("crack_detected", "REJECT", ActivityStatus.NG)
                return ("crack_weak_signal", "MANDATORY_REVIEW", ActivityStatus.MARGINAL)

        # 2. 咬边：depth_mm 超标 -> NG；疑似未标定 -> 复核
        for d in report.defects:
            if d.defect_type == "undercut":
                # 咬边为代理检测(非确定性精确测量)，按设计文档"不能直接判不合格"，
                # 一律路由到复核(MARGINAL)，由人工/后续精确测量裁定。
                if calibrated and d.depth_px > 0:
                    depth_mm = self._px_to_mm(d.depth_px, params)
                    if depth_mm is not None and depth_mm > undercut_max_mm:
                        return (
                            f"undercut_exceeds_{undercut_max_mm}mm({depth_mm:.2f}mm)",
                            "MANDATORY_REVIEW",
                            ActivityStatus.MARGINAL,
                        )
                return ("undercut_suspected_uncalibrated_or_below_limit",
                        "SUGGEST_REVIEW", ActivityStatus.MARGINAL)

        # 3. 气孔：数量超限 -> 复核
        if report.porosity_count > porosity_max:
            return (
                f"porosity_count_{report.porosity_count}_exceeds_{porosity_max}",
                "SUGGEST_REVIEW",
                ActivityStatus.MARGINAL,
            )

        # 4. 无硬缺陷：置信达标才 OK，否则复核
        if report.confidence >= _MIN_CONFIDENCE_FOR_OK:
            return ("no_critical_defects", "AUTO_PASS", ActivityStatus.OK)
        return ("no_defects_low_confidence", "SUGGEST_REVIEW", ActivityStatus.MARGINAL)

    def _param_float(self, params: dict, key: str, default: float) -> float:
        v = params.get(key)
        if v is None:
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _px_to_mm(self, px: float, params: dict) -> float | None:
        ppm = params.get("pixels_per_mm")
        if ppm:
            try:
                v = float(ppm)
                if v > 0:
                    return round(px / v, 3)
            except (TypeError, ValueError):
                pass
        thick_mm = params.get("plate_thickness_mm")
        thick_px = params.get("plate_thickness_px")
        if thick_mm and thick_px:
            try:
                return round(px * float(thick_mm) / float(thick_px), 3)
            except (TypeError, ValueError):
                pass
        return None

    # ------------------------------------------------------------------
    # 输出 + WeldMap 合并
    # ------------------------------------------------------------------

    async def _merge_defects_into_annotations(
        self, wf_id: WorkflowId, report: DefectReport
    ) -> AnnotationsData:
        """读取现有 annotations（含 MEA 几何），追加缺陷元素，不覆盖。"""
        existing = await self._weldmap.read_annotations(wf_id)
        elements: dict[str, AnnotationElement] = {}
        if existing is not None:
            elements = dict(existing.elements)
        for idx, d in enumerate(report.defects):
            eid = f"defect_{d.defect_type}_{idx + 1}"
            elements[eid] = AnnotationElement(
                element_id=eid,
                geometry_px={
                    "cx": float(round(d.centroid_px[0], 2)),
                    "cy": float(round(d.centroid_px[1], 2)),
                    "bbox_x": float(d.bbox_px[0]),
                    "bbox_y": float(d.bbox_px[1]),
                    "bbox_w": float(d.bbox_px[2]),
                    "bbox_h": float(d.bbox_px[3]),
                },
                geometry_mm={},
                value=None,
                uncertainty=0.0,
                confidence=d.confidence,
                source=f"{d.defect_type}_detector",
                validation_status="unchecked",
            )
        return AnnotationsData(elements=elements)

    def _map_to_output(
        self,
        report: DefectReport,
        verdict: str,
        route: str,
        status: ActivityStatus,
        calibrated: bool,
    ) -> ActivityOutput:
        return ActivityOutput(
            status=status,
            data={
                "capability": "rda",
                "method": report.method,
                "defects": [
                    {
                        "type": d.defect_type,
                        "centroid_px": list(d.centroid_px),
                        "bbox_px": list(d.bbox_px),
                        "area_px": d.area_px,
                        "length_px": d.length_px,
                        "depth_px": d.depth_px,
                        "confidence": d.confidence,
                        "severity": d.severity,
                        "detail": d.detail,
                    }
                    for d in report.defects
                ],
                "locations": [list(d.centroid_px) for d in report.defects],
                "defect_types": report.defect_types,
                "porosity_count": report.porosity_count,
                "has_crack": report.has_crack,
                "confidence": report.confidence,
                "calibrated": calibrated,
                "route_decision": route,
                "verdict": verdict,
                "notes": report.notes,
                "mock": False,
                "data_source": "deterministic_cv",
            },
            error=None,
        )
