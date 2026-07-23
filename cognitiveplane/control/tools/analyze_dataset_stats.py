"""数据集统计工具 — 轻量 CV 统计，主 Agent 可直接调用。

仅跑统计与 CV 特征分析（cv_only），不调视觉大模型。2 秒内返回。
用于轻量查询（"多少张模糊的""尺寸多大""分布怎样"）或深度分析前快速探查。
"""

from __future__ import annotations

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


class AnalyzeDatasetStatsTool(BrainTool):
    """数据集统计工具 — CV 统计快照。

    与 analyze_dataset（完整管线，subagent_only）互补：
    本工具只跑统计与 CV 特征分析，不调视觉大模型，主 Agent 直接可用。
    """

    phase = 2
    subagent_only = False

    def __init__(self, llm_provider: "LLMProvider | None" = None) -> None:
        self._llm = llm_provider
        self._orchestrator: DataUnderstandingOrchestrator | None = None

    def _get_orchestrator(self) -> DataUnderstandingOrchestrator:
        if self._orchestrator is None:
            self._orchestrator = DataUnderstandingOrchestrator(self._llm)
        return self._orchestrator

    @property
    def name(self) -> str:
        return "analyze_dataset_stats"

    @property
    def description(self) -> str:
        return (
            "分析数据集目录的统计与 CV 特征，快速返回可量化指标。"
            "仅跑确定性算子（不调视觉大模型），2 秒内返回。\n"
            "**何时使用**：用户问\"多少张图\"\"多少张模糊\"\"尺寸多大\""
            "\"数据分布怎样\"等轻量统计问题。\n"
            "**与 analyze_dataset 的关系**：本工具只跑统计指标；"
            "需要多模态语义理解（缺陷类型/数据偏差/预标注建议）时，"
            "由 data-understanding 子 agent 调 analyze_dataset。\n"
            "**用法**：传 image_dir（图片目录路径），可选 data_kind "
            "(unlabeled/labeled)。有标注数据可传 labels 和 annotations "
            "（会做前景/背景分离和标签统计）。\n"
            "返回：尺寸分布、质量分布、模糊/过暗/过亮/重复数量、"
            "异常样本清单、采集视角分布、数据来源分布。有标注数据额外返回"
            "类别分布、覆盖率、一致性、划分建议。"
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
                "labels": {
                    "type": "object",
                    "description": "标签映射 {文件名: 标签}，有标注数据时传入",
                    "additionalProperties": {"type": "string"},
                },
                "annotations": {
                    "type": "object",
                    "description": (
                        "结构化标注 {文件名: 标注对象}，支持 bbox/mask/OCR 等格式。"
                        "用于前景/背景分离分析。"
                    ),
                    "additionalProperties": {"type": "object"},
                },
            },
            "required": ["image_dir"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        image_dir = kwargs.get("image_dir", "")
        data_kind_str = kwargs.get("data_kind", "unlabeled")
        labels = kwargs.get("labels")
        annotations = kwargs.get("annotations")

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
                depth="cv_only",
            )

            report_dict = report.to_dict()

            return ToolResult(
                output={
                    "status": "ok",
                    "summary": report.summary,
                    "report": report_dict,
                },
            )
        except Exception as e:
            logger.exception("[analyze_dataset_stats] failed")
            return ToolResult(
                output={"error": str(e)},
                error=str(e),
                error_type="execution_error",
            )
