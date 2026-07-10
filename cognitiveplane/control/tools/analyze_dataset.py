"""数据集理解工具 — LLM 可直接调用的数据集级分析工具。

两类分析能力：
  1. 统计与 CV 特征分析（纯计算，快速）
  2. 多模态语义理解（视觉大模型，深度）

支持有标注/无标注数据的不同处理管线。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

from cognitiveplane.control.tools import BrainTool, ToolResult
from cognitiveplane.control.data_understanding.models import DataKind
from cognitiveplane.control.data_understanding.orchestrator import (
    DataUnderstandingOrchestrator,
)

if TYPE_CHECKING:
    from cognitiveplane.capability.ports import LLMProvider

logger = logging.getLogger(__name__)


class AnalyzeDatasetTool(BrainTool):
    """数据集理解工具 — 对整个图片目录做统计分析 + 多模态语义理解。

    与 analyze_image（单张图分析）互补：本工具做数据集级分布统计、
    异常检测、重复检测和语义级理解。
    """

    phase = 2
    subagent_only = True  # 只对 data-understanding subagent 可见，主 agent 通过 delegate 委派

    def __init__(self, llm_provider: "LLMProvider | None" = None) -> None:
        self._llm = llm_provider
        self._orchestrator: DataUnderstandingOrchestrator | None = None

    def _get_orchestrator(self) -> DataUnderstandingOrchestrator:
        if self._orchestrator is None:
            self._orchestrator = DataUnderstandingOrchestrator(self._llm)
        return self._orchestrator

    @property
    def name(self) -> str:
        return "analyze_dataset"

    @property
    def description(self) -> str:
        return (
            "分析整个数据集目录，返回统计 CV 特征 + 多模态语义理解报告。\n"
            "**何时使用**：用户上传一批图片想了解数据集整体情况、"
            "数据质量分布、缺陷类型分布、需要数据清洗建议时。"
            "也适用于有标注数据集的标签质量分析和无标注数据集的预标注建议。\n"
            "**用法**：传 image_dir（图片目录路径）+ 可选 data_kind "
            "(unlabeled/labeled) + 可选 depth (cv_only/cv+model/full)。\n"
            "有标注数据可传 labels 和 annotations（结构化标注 bbox/mask/OCR）；"
            "无标注数据可传 model_inference（已有模型/SAM 推理结果）"
            "或 model_inference_fn（模型推理回调）。\n"
            "返回：尺寸分布、质量分布、异常样本、重复样本、缺陷类别、"
            "数据偏差、预标注建议、前景/背景分析、标签质量等。\n"
            "**约束**：与 analyze_image 不同——本工具分析整个目录，"
            "不是单张图。适合数据探查和数据集质量评估场景。"
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "image_dir": {
                    "type": "string",
                    "description": "图片目录的绝对路径或相对路径",
                },
                "data_kind": {
                    "type": "string",
                    "enum": ["unlabeled", "labeled"],
                    "default": "unlabeled",
                    "description": "数据类型：unlabeled(无标注) 或 labeled(有标注)",
                },
                "depth": {
                    "type": "string",
                    "enum": ["cv_only", "cv+model", "full"],
                    "default": "full",
                    "description": "分析深度：cv_only(仅CV统计) / cv+model(CV+模型推理) / full(全部含多模态语义理解)",
                },
                "labels": {
                    "type": "object",
                    "description": "标签映射 {文件名: 标签}，有标注数据时传入",
                    "additionalProperties": {"type": "string"},
                },
                "annotations": {
                    "type": "object",
                    "description": (
                        "结构化标注 {文件名: 标注对象}，支持 bbox/mask/OCR 等格式。"
                        "示例: {\"img.jpg\": [{\"bbox\": [x,y,w,h], \"label\": \"气孔\"}]}"
                    ),
                    "additionalProperties": {"type": "object"},
                },
                "model_inference": {
                    "type": "object",
                    "description": (
                        "已有模型/SAM/开放语义模型推理结果（无标注数据第二层）。"
                        "字段: model_name, model_type, prompt, inferred_labels, "
                        "confidence_scores, anomaly_samples, semantic_clusters, "
                        "suspected_regions, visualization_refs, analysis_summary"
                    ),
                },
            },
            "required": ["image_dir"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        image_dir = kwargs.get("image_dir", "")
        data_kind_str = kwargs.get("data_kind", "unlabeled")
        depth = kwargs.get("depth", "full")
        labels = kwargs.get("labels")
        annotations = kwargs.get("annotations")
        model_inference = kwargs.get("model_inference")

        if not image_dir:
            return ToolResult(
                output={"error": "image_dir 参数必填"},
                error="missing image_dir",
                error_type="validation_error",
            )

        dir_path = Path(image_dir)
        if not dir_path.is_dir():
            return ToolResult(
                output={"error": f"目录不存在: {image_dir}"},
                error=f"directory not found: {image_dir}",
                error_type="validation_error",
            )

        data_kind = (
            DataKind.LABELED if data_kind_str == "labeled" else DataKind.UNLABELED
        )

        try:
            orchestrator = self._get_orchestrator()
            report = await orchestrator.analyze(
                image_dir=dir_path,
                data_kind=data_kind,
                labels=labels,
                annotations=annotations,
                depth=depth,
                model_inference=model_inference,
            )

            report_dict = report.to_dict()

            # 构建给 LLM 的简洁摘要
            summary_lines = [report.summary]
            if report.recommendations:
                summary_lines.append("\n建议:")
                for i, rec in enumerate(report.recommendations, 1):
                    summary_lines.append(f"  {i}. {rec}")

            return ToolResult(
                output={
                    "status": "ok",
                    "summary": "\n".join(summary_lines),
                    "report": report_dict,
                },
            )
        except Exception as e:
            logger.exception("[analyze_dataset] failed")
            return ToolResult(
                output={"error": str(e)},
                error=str(e),
                error_type="execution_error",
            )
