"""UploadImageToDatasetTool — L1 包装工具：image_ref → Label Studio 数据集。

解决 LLM 有 image_ref (PENDING:session_id:index) 但 MCP upload_images 需要 base64
的断层。LLM 调本工具传 image_ref + version_id，工具内部:
  1. 从 ImageStore 取原图
  2. 压缩 + base64 编码
  3. 通过 ToolRegistry 调 MCP upload_images 工具
  4. 透传结果

phase=3 — 和标注 MCP 工具同期可见。
"""

import base64
import io
import logging

from cognitiveplane.control.tools import BrainTool, ToolResult

logger = logging.getLogger(__name__)

# 压缩参数 — 与 launch_workflow._compress_image_for_workflow 一致
MAX_DIM = 1024
JPEG_QUALITY = 80


def _compress_image(raw_bytes: bytes) -> bytes:
    """等比缩放至最长边 MAX_DIM，JPEG 80% 质量。"""
    try:
        from PIL import Image
    except ImportError:
        return raw_bytes  # Pillow 不可用时直接返回原图
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > MAX_DIM:
            ratio = MAX_DIM / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY)
        return buf.getvalue()
    except Exception as e:
        logger.warning("compress image failed: %s", e)
        return raw_bytes


class UploadImageToDatasetTool(BrainTool):
    """把 image_ref 对应的图片上传到 Label Studio 数据集版本。

    LLM 只需提供 image_ref（系统注入的图片引用）和 version_id（从 get_dataset 拿到），
    工具内部从 ImageStore 取图、压缩、转 base64，再调 MCP upload_images。
    """

    phase = 3

    def __init__(self, image_store=None, tool_registry=None) -> None:
        self._image_store = image_store
        self._tool_registry = tool_registry

    @property
    def name(self) -> str:
        return "upload_image_to_dataset"

    @property
    def description(self) -> str:
        return (
            "上传当前会话的图片到标注平台数据集。\n"
            "**标注流程中的位置**：第 3 步（在 list_datasets → get_dataset 之后，create_job 之前）。\n"
            "**用法**：传 image_ref（格式 PENDING:session_id:index）+ "
            "version_id（从上一步 get_dataset 的 latestVersionId 获取）。\n"
            "**注意**：不要直接调 MCP upload_images（需要 base64，你拿不到），用本工具即可。"
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "image_ref": {
                    "type": "string",
                    "description": "Image reference (PENDING:session_id:index) from the system-provided image list.",
                },
                "version_id": {
                    "type": "string",
                    "description": "Dataset version ID to upload to. Get it from get_dataset tool's latestVersionId field.",
                },
                "filename": {
                    "type": "string",
                    "description": "Filename for the uploaded image (optional, default: weld_<timestamp>.jpg).",
                },
            },
            "required": ["image_ref", "version_id"],
        }

    async def execute(
        self,
        image_ref: str,
        version_id: str,
        filename: str | None = None,
        **kwargs,
    ) -> ToolResult:
        if self._image_store is None:
            return ToolResult(error="ImageStore not available — cannot resolve image_ref")
        if self._tool_registry is None:
            return ToolResult(error="ToolRegistry not available — cannot call upload_images")

        # 1. 从 ImageStore 取原图
        try:
            result = self._image_store.get_original(image_ref)
        except Exception as e:
            return ToolResult(error=f"image_store.get_original failed: {e}")

        if result is None:
            # 尝试 fuzzy match
            try:
                result = self._image_store_fuzzy_match(image_ref)
            except Exception:
                result = None
            if result is None:
                return ToolResult(
                    error=f"image_ref not found in ImageStore: {image_ref}",
                    error_type="invalid_image",
                )

        raw_bytes, _mime = result

        # 2. 压缩 + base64 编码
        compressed = _compress_image(raw_bytes)
        b64_data = base64.b64encode(compressed).decode("ascii")

        # 3. 构造文件名
        import time
        fname = filename or f"weld_{int(time.time())}.jpg"

        # 4. 通过 ToolRegistry 调 MCP upload_images
        upload_args = {
            "versionId": version_id,
            "images": [{"filename": fname, "data": b64_data}],
        }

        try:
            upload_result = await self._tool_registry.execute("upload_images", upload_args)
        except Exception as e:
            return ToolResult(error=f"upload_images call failed: {e}")

        if upload_result.error:
            return ToolResult(
                output=upload_result.output,
                error=f"upload_images failed: {upload_result.error}",
                error_type=upload_result.error_type,
            )

        return ToolResult(output={
            "status": "ok",
            "version_id": version_id,
            "filename": fname,
            "image_ref": image_ref,
            "upload_result": upload_result.output,
            "message": f"图片已上传到数据集版本 {version_id}（文件名: {fname}）",
        })

    def _image_store_fuzzy_match(self, ref: str):
        """Fuzzy match image_ref — 复用 ImageStore 的 fuzzy 逻辑。"""
        if self._image_store is None:
            return None
        try:
            # 尝试解析 session_id 和 index
            parts = ref.split(":")
            if len(parts) >= 3:
                session_id = parts[1]
                # 遍历 ImageStore 找匹配的 key
                for key in getattr(self._image_store, "_store", {}):
                    if session_id in key:
                        return self._image_store.get_original(key)
        except Exception:
            pass
        return None
