"""多模态语义理解器 — 2.2 视觉大模型深度理解。

通过视觉多模态模型完成传统指标不好量化的理解：
  - 主要对象识别
  - 缺陷类别理解
  - 场景语义理解
  - 目标与背景关系
  - 疑似缺陷区域
  - 数据中潜在类别
  - 复杂样本说明
  - 难以量化的质量判断
  - 可能的数据偏差
  - 预标注建议（无标注数据）
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

from cognitiveplane.control.data_understanding.models import (
    DataKind,
    ModelInferenceResult,
    SemanticUnderstanding,
)

if TYPE_CHECKING:
    from cognitiveplane.capability.ports import LLMProvider

logger = logging.getLogger(__name__)

# 采样上限（防止对大数据集逐张调 LLM）
MAX_IMAGES_FOR_SEMANTIC = 10
# 每张图缩放上限（减少 token 消耗）
MAX_IMAGE_SIZE_FOR_LLM = 1024


class MultimodalSemanticAnalyzer:
    """多模态语义理解器。

    用法：
        analyzer = MultimodalSemanticAnalyzer(llm_provider)
        result = await analyzer.analyze(
            image_dir=Path("/data/weld_images"),
            data_kind=DataKind.UNLABELED,
            cv_report=cv_report,  # 可选，提供 CV 统计上下文
        )
    """

    def __init__(self, llm_provider: "LLMProvider | None" = None) -> None:
        self._llm = llm_provider

    async def analyze(
        self,
        image_dir: Path | str,
        data_kind: DataKind = DataKind.UNLABELED,
        cv_report: Any | None = None,
        labels: dict[str, str] | None = None,
        annotations: dict[str, Any] | None = None,
        model_inference: ModelInferenceResult | None = None,
    ) -> SemanticUnderstanding:
        """对数据集做多模态语义理解。

        Args:
            image_dir: 图片目录
            data_kind: 数据类型（有标注/无标注），决定 prompt 方向
            cv_report: CV 统计报告（提供量化上下文给 LLM 参考）
            labels: 标签映射（有标注数据用）
            annotations: 结构化标注（bbox/mask/OCR）
            model_inference: 已有业务模型/SAM/开放语义模型推理结果
        """
        if self._llm is None:
            return SemanticUnderstanding(quality_assessment="LLM 不可用，跳过多模态语义理解")

        image_dir = Path(image_dir)
        image_files = sorted(self._find_images(image_dir))
        if not image_files:
            return SemanticUnderstanding(quality_assessment="未找到图片文件")

        # 采样：取代表性图片（均匀分布 + 异常样本优先）
        sampled = self._sample_representative(image_files, cv_report)
        logger.info("[DSA-Semantic] Sampling %d/%d images for LLM", len(sampled), len(image_files))

        # 构建 LLM 请求
        images_b64 = []
        for img_path in sampled:
            b64 = self._encode_image(img_path)
            if b64:
                images_b64.append(b64)

        if not images_b64:
            return SemanticUnderstanding(quality_assessment="图片编码失败")

        prompt = self._build_prompt(
            data_kind, cv_report, labels, annotations, model_inference, len(sampled)
        )

        try:
            llm_response = await self._llm.vision_complete(
                text=prompt,
                images=images_b64,
            )
            response_text = llm_response.content if hasattr(llm_response, "content") else str(llm_response)
            return self._parse_response(response_text)
        except Exception as e:
            logger.warning("[DSA-Semantic] LLM analysis failed: %s", e)
            return SemanticUnderstanding(quality_assessment=f"LLM 分析失败: {e}")

    # ------------------------------------------------------------------
    # Prompt 构建
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        data_kind: DataKind,
        cv_report: Any | None,
        labels: dict[str, str] | None,
        annotations: dict[str, Any] | None,
        model_inference: ModelInferenceResult | None,
        num_samples: int,
    ) -> str:
        """构建多模态理解的 LLM prompt。"""
        parts: list[str] = []

        parts.append("你是工业图像数据理解专家。请分析以下采样图片，从语义层面理解这批数据。")

        # CV 上下文
        if cv_report:
            parts.append("\n## CV 统计上下文（仅供参考，你需要做更深入的语义理解）")
            parts.append(f"- 总图片数: {cv_report.total_images}")
            parts.append(f"- 模糊样本: {cv_report.blurry_count}")
            parts.append(f"- 过暗样本: {cv_report.dark_count}")
            parts.append(f"- 过亮样本: {cv_report.bright_count}")
            parts.append(f"- 重复样本: {cv_report.duplicate_count}")
            if cv_report.brightness_stats:
                parts.append(f"- 亮度均值: {cv_report.brightness_stats.get('mean', '?')}")
            if cv_report.sharpness_stats:
                parts.append(f"- 清晰度均值: {cv_report.sharpness_stats.get('mean', '?')}")
            parts.append(f"- 尺寸分布: {cv_report.width_distribution}")

        # 标签上下文
        if data_kind == DataKind.LABELED and labels:
            parts.append("\n## 标签信息")
            unique_labels = set(labels.values())
            parts.append(f"- 标签类别: {', '.join(unique_labels)}")
            parts.append(f"- 标注样本数: {len(labels)}")
            if annotations:
                parts.append("- 已提供结构化标注（可能包含 bbox/mask/OCR），请结合图像判断标签与图像内容是否匹配。")

        if model_inference:
            parts.append("\n## 已有模型/SAM/开放语义模型推理上下文")
            parts.append(f"- 模型名称: {model_inference.model_name or 'unknown'}")
            if model_inference.model_type:
                parts.append(f"- 模型类型: {model_inference.model_type}")
            if model_inference.prompt:
                parts.append(f"- 推理 prompt: {model_inference.prompt}")
            if model_inference.inferred_labels:
                parts.append(f"- 推理标签样例: {dict(list(model_inference.inferred_labels.items())[:10])}")
            if model_inference.anomaly_samples:
                parts.append(f"- 模型认为异常的样本数: {len(model_inference.anomaly_samples)}")
            if model_inference.suspected_regions:
                parts.append("- 已有模型给出了疑似区域，请结合可视化/图像进一步拆解。")
            if model_inference.visualization_refs:
                parts.append("- 已有推理可视化结果可用于进一步解释。")
            if model_inference.analysis_summary:
                parts.append(f"- 模型分析摘要: {model_inference.analysis_summary}")

        parts.append(f"\n## 采样图片（共 {num_samples} 张，从完整数据集中代表性采样）")
        parts.append("请基于这些图片做以下分析（返回 JSON）：")

        if data_kind == DataKind.UNLABELED:
            parts.append("""
```json
{
  "main_objects": ["识别到的主要对象，如焊缝/母材/热影响区/工件等"],
  "object_background_notes": ["目标与背景关系的细化说明"],
  "defect_categories": ["识别到的缺陷类型"],
  "suspected_defect_regions": [{"type": "缺陷类型", "confidence": 0.0-1.0, "location": "描述位置"}],
  "scene_type": "场景类型，如焊缝射线检测/焊缝表面检测/焊接件外观检测等",
  "target_background_relation": "目标与背景的关系描述",
  "potential_categories": ["数据中可能存在但未定义的类别"],
  "data_bias": ["数据偏差描述，如视角单一/光照不均/缺陷类型分布不均等"],
  "complex_samples": [{"reason": "复杂原因描述，如多缺陷重叠/边界模糊等"}],
  "quality_assessment": "整体质量评语（难以量化的判断）",
  "preannotation_suggestions": [{"suggested_label": "建议标签", "confidence": 0.0-1.0}],
  "needs_manual_annotation": ["需要人工标注的样本特征描述"]
}
```""")
        else:
            parts.append("""
```json
{
  "main_objects": ["识别到的主要对象"],
  "object_background_notes": ["目标与背景关系的细化说明"],
  "defect_categories": ["识别到的缺陷类型"],
  "suspected_defect_regions": [{"type": "缺陷类型", "confidence": 0.0-1.0, "location": "描述位置"}],
  "scene_type": "场景类型",
  "target_background_relation": "目标与背景的关系描述",
  "potential_categories": ["数据中可能存在但未定义的类别"],
  "data_bias": ["数据偏差描述"],
  "complex_samples": [{"reason": "复杂原因描述"}],
  "quality_assessment": "整体质量评语",
  "label_image_match_issues": ["标签与图像内容不匹配的问题"],
  "foreground_background_findings": ["结合标注可视化后的前景/背景发现"]
}
```""")

        parts.append("\n只返回 JSON，不要其他内容。")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # 响应解析
    # ------------------------------------------------------------------

    def _parse_response(self, response: str) -> SemanticUnderstanding:
        """解析 LLM 返回的 JSON。"""
        text = response.strip()
        # 处理 markdown 代码块包裹
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        try:
            data = json.loads(text)
            return SemanticUnderstanding(
                main_objects=data.get("main_objects", []),
                object_background_notes=data.get("object_background_notes", []),
                defect_categories=data.get("defect_categories", []),
                suspected_defect_regions=data.get("suspected_defect_regions", []),
                scene_type=data.get("scene_type", ""),
                target_background_relation=data.get("target_background_relation", ""),
                potential_categories=data.get("potential_categories", []),
                data_bias=data.get("data_bias", []),
                complex_samples=data.get("complex_samples", []),
                quality_assessment=data.get("quality_assessment", ""),
                preannotation_suggestions=data.get("preannotation_suggestions", []),
                needs_manual_annotation=data.get("needs_manual_annotation", []),
                label_image_match_issues=data.get("label_image_match_issues", []),
                foreground_background_findings=data.get("foreground_background_findings", []),
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("[DSA-Semantic] Failed to parse LLM response: %s", e)
            return SemanticUnderstanding(
                quality_assessment=f"LLM 返回解析失败，原始回复: {response[:500]}"
            )

    # ------------------------------------------------------------------
    # 采样策略
    # ------------------------------------------------------------------

    def _sample_representative(
        self,
        image_files: list[Path],
        cv_report: Any | None,
    ) -> list[Path]:
        """采样代表性图片：异常样本优先 + 均匀分布。

        策略：
          1. 如果有 CV 报告，优先取异常样本（模糊/过暗/过亮）
          2. 再从正常样本中均匀采样补足
          3. 总数不超过 MAX_IMAGES_FOR_SEMANTIC
        """
        if len(image_files) <= MAX_IMAGES_FOR_SEMANTIC:
            return image_files

        sampled: list[Path] = []

        # 异常样本优先
        if cv_report and cv_report.anomaly_files:
            anomaly_set = set(cv_report.anomaly_files)
            anomaly_paths = [p for p in image_files if p.name in anomaly_set]
            sampled.extend(anomaly_paths[:MAX_IMAGES_FOR_SEMANTIC // 2])

        # 均匀采样补足
        remaining = MAX_IMAGES_FOR_SEMANTIC - len(sampled)
        if remaining > 0:
            step = max(1, len(image_files) // remaining)
            for i in range(0, len(image_files), step):
                if len(sampled) >= MAX_IMAGES_FOR_SEMANTIC:
                    break
                if image_files[i] not in sampled:
                    sampled.append(image_files[i])

        return sampled[:MAX_IMAGES_FOR_SEMANTIC]

    # ------------------------------------------------------------------
    # 图片编码
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_image(img_path: Path) -> str | None:
        """读取图片并编码为 base64 data URL。"""
        try:
            import cv2
            img = cv2.imread(str(img_path))
            if img is None:
                return None
            # 缩放到合理尺寸
            h, w = img.shape[:2]
            if max(h, w) > MAX_IMAGE_SIZE_FOR_LLM:
                scale = MAX_IMAGE_SIZE_FOR_LLM / max(h, w)
                img = cv2.resize(img, (int(w * scale), int(h * scale)))
            # 编码为 JPEG
            _, buffer = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            b64 = base64.b64encode(buffer).decode("utf-8")
            return f"data:image/jpeg;base64,{b64}"
        except Exception as e:
            logger.warning("[DSA-Semantic] Failed to encode %s: %s", img_path.name, e)
            return None

    @staticmethod
    def _find_images(directory: Path) -> list[Path]:
        """查找目录下的图片文件。"""
        extensions = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}
        return [
            p for p in directory.rglob("*")
            if p.suffix.lower() in extensions and p.is_file()
        ]
