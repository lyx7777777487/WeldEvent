"""AnalyzeImageTool — 焊缝图片质量分析工具。

Plan §2.3 双轨设计:
  - 消息层: Thumbnail 注入 LLM content (LLM 直接看缩略图)
  - 工具层: LLM 调 analyze_image(image_id=...) 只传引用

本工具接收 image_id，从 ImageStore 取原图送给 vision_complete。
原图不进 tool_calls (token 限制)。

Plan §A.3:
  - ImageStore 分层存储 (thumbnail + original)
  - 工具内部用原图分析 (高清细节)，消息层用 thumbnail (成本闸门)
"""

from cognitiveplane.control.tools import BrainTool, ToolResult


class AnalyzeImageTool(BrainTool):
    """Analyze weld images using multimodal LLM for quality assessment.

    接收 image_id (ImageStore 引用)，工具内部取原图送 vision_complete。
    """

    def __init__(self, llm_provider=None, image_store=None) -> None:
        self._llm = llm_provider
        self._image_store = image_store

    @property
    def name(self) -> str:
        return "analyze_image"

    @property
    def description(self) -> str:
        return (
            "Analyze a weld seam image for quality assessment. "
            "Detects defects (porosity, slag inclusion, cracks, undercut, etc.), "
            "evaluates quality grade, and provides improvement suggestions. "
            "Pass image_id (UUID from the system prompt image list). "
            "The tool fetches the high-resolution original internally — "
            "do NOT pass base64 data URLs."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "image_id": {
                    "type": "string",
                    "description": "Image ID (UUID) from the system-provided image list. The tool fetches the original from the image store.",
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
            "required": ["image_id"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        image_id = kwargs.get("image_id", "")
        question = kwargs.get("question", "请对这张焊缝图片进行全面质量评估")
        material = kwargs.get("material", "")
        thickness = kwargs.get("thickness_mm", "")

        if not image_id:
            return ToolResult(error="No image_id provided")

        if self._llm is None or not hasattr(self._llm, "vision_complete"):
            return ToolResult(error="Multimodal model not available")

        if self._image_store is None:
            return ToolResult(error="ImageStore not available — cannot resolve image_id")

        # plan §2.3: 工具层从 ImageStore 取原图 (不进 tool_calls)
        original_data_url = self._image_store.get_original_data_url(image_id)
        if original_data_url is None:
            return ToolResult(
                error=f"image_id not found in ImageStore: {image_id}",
                error_type="invalid_image",
            )

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
                images=[original_data_url],
            )
            return ToolResult(output={
                "analysis": response.content,
                "model": getattr(response, "model", "unknown"),
                "image_id": image_id,
            })
        except Exception as e:
            error_type, message = self._classify_vision_error(e)
            return ToolResult(error=message, error_type=error_type)

    @staticmethod
    def _classify_vision_error(exc: Exception) -> tuple[str, str]:
        """Map vision_complete exceptions to structured error_type.

        Returns (error_type, user_message). error_type values:
        - "vision_unavailable": 404/401/invalid endpoint — not retriable, degrade to text
        - "vision_transient": network/timeout/5xx — retriable
        - "invalid_image": 400 image-related — not retriable with same input
        - "vision_unknown": unclassified — log + escalate
        """
        exc_name = type(exc).__name__
        exc_msg = str(exc).lower()

        if exc_name in ("NotFoundError", "AuthenticationError", "PermissionDeniedError"):
            return "vision_unavailable", f"Vision endpoint unavailable: {exc}"
        if exc_name in ("APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError"):
            return "vision_transient", f"Vision transient error: {exc}"
        if exc_name == "BadRequestError":
            if any(k in exc_msg for k in ("image", "format", "size", "resolution")):
                return "invalid_image", f"Invalid image: {exc}"
            return "vision_unavailable", f"Vision endpoint misconfigured: {exc}"
        if exc_name in ("ConnectError", "TimeoutError", "ConnectionError", "OSError"):
            return "vision_transient", f"Network error: {exc}"
        return "vision_unknown", f"Image analysis failed ({exc_name}): {exc}"