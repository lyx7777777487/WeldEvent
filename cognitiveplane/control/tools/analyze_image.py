"""AnalyzeImageTool — 焊缝图片质量分析工具。

Plan §2.3 双轨设计:
  - 消息层: Thumbnail 注入 LLM content (LLM 直接看缩略图)
  - 工具层: LLM 调 analyze_image(image_ref=...) 只传引用

本工具接收 image_ref (格式 PENDING:{session_id}:{index})，从 ImageStore
取原图送给 vision_complete。原图不进 tool_calls (token 限制)。

Plan §A.3:
  - ImageStore 分层存储 (thumbnail + original)
  - 工具内部用原图分析 (高清细节)，消息层用 thumbnail (成本闸门)

图片传输前自动缩放：工业焊缝图片通常不需要超高分辨率，
限制最大边 1024px + JPEG 80% 质量，确保 API 传输在 30s 内完成。
"""

import base64
import io
from typing import NamedTuple

from cognitiveplane.control.tools import BrainTool, ToolResult


class _ImageSize(NamedTuple):
    width: int
    height: int


# 发送给 vision API 的图片最大尺寸（最长边），超出等比缩放
# 1024px 足够看清焊缝细节，同时控制 base64 体积在 ~100KB 以内
MAX_DIM = 1024
JPEG_QUALITY = 80


class AnalyzeImageTool(BrainTool):
    """Analyze weld images using multimodal LLM for quality assessment.

    接收 image_ref (PENDING:{session_id}:{index} 引用)，工具内部取原图送 vision_complete。
    """

    phase = 2

    def __init__(self, llm_provider=None, image_store=None) -> None:
        self._llm = llm_provider
        self._image_store = image_store
        self._result_cache: dict[str, ToolResult] = {}  # 会话级去重

    @property
    def name(self) -> str:
        return "analyze_image"

    @property
    def description(self) -> str:
        return (
            "分析工业图像，评估质量并检测缺陷。\n"
            "**注意**：此工具仅返回多模态模型的初步判断，不是最终答案。\n"
            "调用后必须用 search_cases / search_reasoning_knowledge / "
            "search_vision_knowledge 对照验证，如有矛盾需标注。\n"
            "**何时使用**：用户上传图片、要求看图、分析缺陷、评估质量、"
            "或任何涉及图像内容理解的请求。\n"
            "**用法**：传 image_ref（格式 PENDING:session_id:index），"
            "工具内部从 ImageStore 取原图，不要传 base64。\n"
            "**约束**：同一张图只调一次，多图时串行处理，禁止并发调用。"
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "image_ref": {
                    "type": "string",
                    "description": "Image reference (PENDING:session_id:index) from the system-provided image list. The tool fetches the original from the image store.",
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
            "required": ["image_ref"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        image_ref = kwargs.get("image_ref", "")
        question = kwargs.get("question", "请对这张焊缝图片进行全面质量评估")
        material = kwargs.get("material", "")
        thickness = kwargs.get("thickness_mm", "")

        if not image_ref:
            return ToolResult(error="No image_ref provided")

        # 会话级去重：同一张图已分析过直接返回缓存，避免重复调 vision API
        if image_ref in self._result_cache:
            return self._result_cache[image_ref]

        if self._llm is None or not hasattr(self._llm, "vision_complete"):
            return ToolResult(error="Multimodal model not available")

        if self._image_store is None:
            return ToolResult(error="ImageStore not available — cannot resolve image_ref")

        # plan §2.3: 工具层从 ImageStore 取原图 (不进 tool_calls)
        # plan §2.3 line 348: SessionStore 解析 PENDING:xxx → 取回真正的图片数据
        original_data_url = self._image_store.get_original_data_url(image_ref)
        if original_data_url is None:
            return ToolResult(
                error=f"image_ref not found in ImageStore: {image_ref}",
                error_type="invalid_image",
            )

        # 缩放大图，控制 API 传输体积。
        # 原始焊缝图片可能 4K+，base64 编码后数百 KB，导致 vision API
        # HTTP 上传 + 模型处理超过 TOOL_TIMEOUT_SECONDS。
        prepared_url, prep_info = self._prepare_for_vision(original_data_url)

        # 双轨设计（Hybrid Vision-Language, arXiv 2605.26533）：
        #   本工具只做"理解/观察"——识别焊缝类型、可见特征、疑似缺陷及位置；
        #   不直接给权威质量等级（等级判定须由确定性测量 MEA + 缺陷识别 RDA +
        #   标准数字化 search_standards 综合后给出，避免 MLLM 一把梭判级）。
        prompt_parts = [
            "你是焊接质检视觉分析专家。请对以下焊缝图片做【观察性分析】"
            "（识别与描述，不下最终判定）：\n",
            f"用户问题：{question}\n",
            f"（图片已缩放至最长边 {MAX_DIM}px，质量 {JPEG_QUALITY}%）\n",
        ]
        if material:
            prompt_parts.append(f"材料：{material}\n")
        if thickness:
            prompt_parts.append(f"板厚：{thickness}mm\n")

        prompt_parts.append(
            "\n请按以下结构输出（客观观察优先，疑似项标注【疑似】）：\n"
            "1. 焊缝类型与接头形式（如 T 型角焊缝/对接焊缝/搭接，单道/多道）\n"
            "2. 焊缝成形外观（成形是否均匀、余高/宽度直观印象、焊趾过渡）\n"
            "3. 可见疑似缺陷及位置（气孔/夹渣/裂纹/咬边/未熔合/未焊透/焊瘤/焊穿，"
            "逐项标【有/无/疑似】并给出在图中的大致位置；不确定的不要臆断）\n"
            "4. 图片本身质量（对焦/曝光/完整性，是否影响判读）\n"
            "5. 初步印象与后续建议（仅作参考；最终质量等级须结合确定性几何测量、"
            "缺陷检测与适用标准数字化后由系统综合判定）\n\n"
            "标准提示：角焊缝外观表面缺陷分级参照 GB/T 19418（焊缝缺陷质量分级）；"
            "GB/T 3323 适用于射线检测（射线底片缺陷），不用于外观判级，请勿混用。\n\n"
            "重要：以上是多模态模型的【非确定性估计】，不等于确定性测量结论。"
            "涉及判废/让步接收等不可逆决策时，必须以确定性测量结果为准并人工复核。"
        )

        try:
            response = await self._llm.vision_complete(
                text="".join(prompt_parts),
                images=[prepared_url],
            )
            result = ToolResult(output={
                "analysis": response.content,
                "model": getattr(response, "model", "unknown"),
                "image_ref": image_ref,
            })
            self._result_cache[image_ref] = result
            return result
        except Exception as e:
            error_type, message = self._classify_vision_error(e)
            return ToolResult(error=message, error_type=error_type)

    @staticmethod
    def _prepare_for_vision(data_url: str) -> tuple[str, str]:
        """缩放 + 压缩图片，控制 API 传输体积。

        返回 (prepared_data_url, info_string)。
        如果图片已经在尺寸限制内或 Pillow 不可用，返回原图。
        """
        try:
            from PIL import Image
        except ImportError:
            return data_url, "original (Pillow not installed)"

        # 解析 data URL → raw bytes
        header, b64 = data_url.split(",", 1)
        mime = "image/jpeg"
        if ":" in header:
            mime = header.split(":")[1].split(";")[0]
        raw = base64.b64decode(b64)

        try:
            img = Image.open(io.BytesIO(raw))
            img = img.convert("RGB")
            w, h = img.size
            max_dim = max(w, h)
            if max_dim <= MAX_DIM:
                return data_url, f"original {w}x{h} (within limit)"

            scale = MAX_DIM / max_dim
            new_size = (int(w * scale), int(h * scale))
            img = img.resize(new_size, Image.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=JPEG_QUALITY)
            compressed = buf.getvalue()
            new_b64 = base64.b64encode(compressed).decode()
            new_url = f"data:image/jpeg;base64,{new_b64}"
            info = (
                f"resized {w}x{h} → {new_size[0]}x{new_size[1]} "
                f"({len(raw) // 1024}KB → {len(compressed) // 1024}KB)"
            )
            return new_url, info
        except Exception:
            return data_url, "original (decode failed)"

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
