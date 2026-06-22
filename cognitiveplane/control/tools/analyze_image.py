"""AnalyzeImageTool — 焊缝图片质量分析工具。

调用多模态LLM对上传的焊缝图片进行质量评估，
包括缺陷检测、质量评级和改进建议。
"""

from cognitiveplane.control.tools import BrainTool, ToolResult


class AnalyzeImageTool(BrainTool):
    """Analyze weld images using multimodal LLM for quality assessment."""

    def __init__(self, llm_provider=None) -> None:
        self._llm = llm_provider

    @property
    def name(self) -> str:
        return "analyze_image"

    @property
    def description(self) -> str:
        return (
            "Analyze weld seam images for quality assessment. "
            "Detects defects (porosity, slag inclusion, cracks, undercut, etc.), "
            "evaluates quality grade, and provides improvement suggestions. "
            "image_data can be a base64 data URL (data:image/...;base64,...) "
            "or a pending reference (PENDING:session_id:index)."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "image_data": {
                    "type": "string",
                    "description": "Image to analyze. Can be a base64 data URL (data:image/...;base64,...) or a pending reference (PENDING:session_id:index)",
                },
                "question": {
                    "type": "string",
                    "description": "Specific question about the image (e.g. '是否有气孔缺陷', '焊缝质量等级')",
                },
                "material": {
                    "type": "string",
                    "description": "Material type if known (e.g. Q345R, 304SS)",
                },
                "thickness_mm": {
                    "type": "number",
                    "description": "Plate thickness in mm if known",
                },
            },
            "required": ["image_data"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        image_data = kwargs.get("image_data", "")
        question = kwargs.get("question", "请对这张焊缝图片进行全面质量评估")
        material = kwargs.get("material", "")
        thickness = kwargs.get("thickness_mm", "")

        if not image_data:
            return ToolResult(error="No image data provided")

        # Resolve PENDING:session_id:index references
        resolved_data = self._resolve_image_ref(image_data)
        if resolved_data is None:
            return ToolResult(error=f"Image reference not found: {image_data}")

        if self._llm is None or not hasattr(self._llm, "vision_complete"):
            return ToolResult(error="Multimodal model not available")

        # Build analysis prompt
        prompt_parts = [
            "你是焊接质检专家，请对以下焊缝图片进行质量分析：\n",
            f"用户问题：{question}\n",
        ]
        if material:
            prompt_parts.append(f"材料：{material}\n")
        if thickness:
            prompt_parts.append(f"板厚：{thickness}mm\n")

        prompt_parts.append(
            "\n请按以下格式输出：\n"
            "1. 焊缝外观评价（成形、余高、宽度）\n"
            "2. 缺陷检测（气孔/夹渣/裂纹/咬边/未熔合/未焊透/焊瘤）\n"
            "3. 质量等级评估（I/II/III/IV级，参照GB/T3323或NB/T47014）\n"
            "4. 改进建议（如有缺陷）"
        )

        try:
            response = await self._llm.vision_complete(
                text="".join(prompt_parts),
                images=[resolved_data],
            )
            return ToolResult(output={
                "analysis": response.content,
                "model": getattr(response, "model", "unknown"),
            })
        except Exception as e:
            return ToolResult(error=f"Image analysis failed: {e}")

    @staticmethod
    def _resolve_image_ref(image_data: str) -> str | None:
        """Resolve PENDING:session_id:index reference to actual data URL.

        Format: PENDING:<session_id>:<index>
        Returns the actual base64 data URL from the pending images cache.
        """
        if not image_data.startswith("PENDING:"):
            return image_data  # Already a data URL

        parts = image_data.split(":")
        if len(parts) != 3:
            return None

        _, session_id, index_str = parts
        try:
            index = int(index_str)
        except ValueError:
            return None

        # Import the pending images cache from chat module
        from cognitiveplane.interaction.api.chat import _pending_images
        images = _pending_images.get(session_id, [])
        if 0 <= index < len(images):
            return images[index]
        return None
