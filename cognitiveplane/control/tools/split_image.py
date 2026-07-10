"""SplitImageTool — 大图分块工具（九宫格/自定义网格）。

工业焊缝图像常为高分辨率大图（4K+），整张送标注平台会导致：
  - 标注员视野过大，漏标小缺陷
  - 单张 task 耗时长，无法多人并行
  - LLM 视觉 API 因 token 限制看不清细节

本工具将大图按九宫格（3×3）或自定义网格切分为小图，每块独立存入
ImageStore 生成新 image_ref，供后续逐块标注/分析。

典型流程（多轮对话标注）：
  1. LLM 调 split_image(image_ref, grid=3) → 返回 9 个子图 image_ref
  2. LLM 逐块调 analyze_image 评估每块质量
  3. 用户确认后，逐块上传到 Label Studio 创建分块 task
  4. 标注完成后，LLM 合并各块标注结果（坐标偏移还原到原图）
"""

from __future__ import annotations

import io
import logging
from typing import Any

from cognitiveplane.control.tools import BrainTool, ToolResult

logger = logging.getLogger(__name__)

# 分块后单块最大边 — 控制 ImageStore 内存占用
_MAX_TILE_DIM = 1024
# 默认网格 — 九宫格 3×3
_DEFAULT_GRID = 3


class SplitImageTool(BrainTool):
    """大图分块工具 — 九宫格/自定义网格切分。

    将高分辨率大图切分为多个小图块，每块独立存入 ImageStore，
    返回子图 image_ref 列表 + 坐标偏移信息（用于后续标注合并）。
    """

    phase = 2

    def __init__(self, image_store=None) -> None:
        self._image_store = image_store

    @property
    def name(self) -> str:
        return "split_image"

    @property
    def description(self) -> str:
        return (
            "将大图按九宫格或自定义网格切分为小图块。\n"
            "**何时使用**：用户上传高分辨率大图（焊缝全景/拼接图）需要分块标注、"
            "分块分析、或图太大标注员看不清细节时。\n"
            "**用法**：传 image_ref 和 grid（默认3=九宫格，也可传2=四宫格、4=16宫格）。"
            "返回子图 image_ref 列表 + 每块在原图中的坐标偏移（用于标注合并）。\n"
            "**约束**：切分后子图按 row_col 命名（如 tile_0_0=左上角），"
            "后续可逐块 analyze_image 或 upload_images 到标注平台。"
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "image_ref": {
                    "type": "string",
                    "description": "要切分的大图引用（PENDING:session_id:index 格式）",
                },
                "grid": {
                    "type": "integer",
                    "description": "网格划分数（grid=3 → 3×3=9块九宫格，grid=2 → 2×2=4块，grid=4 → 4×4=16块）。默认3",
                    "default": 3,
                },
            },
            "required": ["image_ref"],
        }

    async def execute(
        self,
        image_ref: str = "",
        grid: int = _DEFAULT_GRID,
        **kwargs: Any,
    ) -> ToolResult:
        """执行大图分块。

        Args:
            image_ref: 大图引用（PENDING:session_id:index）
            grid: 网格划分数（3=九宫格）
        """
        if self._image_store is None:
            return ToolResult(error="image_store not configured — split_image unavailable")

        if not image_ref:
            return ToolResult(error="image_ref is required")

        if grid < 2 or grid > 8:
            return ToolResult(
                error=f"grid must be between 2 and 8, got {grid}",
                error_type="invalid_argument",
            )

        # 从 ImageStore 取原图
        original = self._image_store.get_original(image_ref)
        if original is None:
            return ToolResult(
                error=f"image_ref '{image_ref}' not found in ImageStore",
                error_type="invalid_image",
            )

        original_bytes, mime_type = original

        # 用 Pillow 切分
        try:
            from PIL import Image
        except ImportError:
            return ToolResult(
                error="Pillow not installed — cannot split image",
                error_type="dependency_missing",
            )

        try:
            img = Image.open(io.BytesIO(original_bytes))
        except Exception as e:
            return ToolResult(
                error=f"failed to open image: {e}",
                error_type="invalid_image",
            )

        width, height = img.size
        # 确保是 RGB（RGBA/P 模式无法直接 JPEG）
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        tile_w = width // grid
        tile_h = height // grid

        # 如果 grid 不能整除，最后一块补齐剩余区域
        tiles: list[dict[str, Any]] = []
        session_id = _extract_session_id(image_ref)

        for row in range(grid):
            for col in range(grid):
                left = col * tile_w
                upper = row * tile_h
                # 最后一块取到边缘
                right = width if col == grid - 1 else (col + 1) * tile_w
                lower = height if row == grid - 1 else (row + 1) * tile_h

                tile = img.crop((left, upper, right, lower))

                # 缩放过大的块（控制内存）
                tile = _resize_if_needed(tile, _MAX_TILE_DIM)

                # 转 JPEG bytes
                buf = io.BytesIO()
                tile.save(buf, format="JPEG", quality=85)
                tile_bytes = buf.getvalue()

                # 存入 ImageStore，生成新 image_ref
                stored = self._image_store.store(
                    raw_bytes=tile_bytes,
                    mime_type="image/jpeg",
                    session_id=session_id,
                )

                tiles.append({
                    "tile_ref": stored.image_id,
                    "row": row,
                    "col": col,
                    "position": _position_name(row, col, grid),
                    "offset": {
                        "x": left,
                        "y": upper,
                        "width": right - left,
                        "height": lower - upper,
                    },
                })

        return ToolResult(output={
            "status": "split_complete",
            "source_ref": image_ref,
            "original_size": {"width": width, "height": height},
            "grid": f"{grid}x{grid}",
            "total_tiles": len(tiles),
            "tiles": tiles,
            "message": (
                f"大图已切分为 {grid}×{grid}={len(tiles)} 块。"
                f"每块已存入 ImageStore，可逐块 analyze_image 或 upload_images 标注。"
                f"标注合并时用 offset 坐标还原到原图位置。"
            ),
        })


def _resize_if_needed(img, max_dim: int):
    """单块超过 max_dim 时等比缩放。"""
    w, h = img.size
    if max(w, h) <= max_dim:
        return img
    ratio = max_dim / max(w, h)
    new_size = (int(w * ratio), int(h * ratio))
    return img.resize(new_size, Image.LANCZOS)


def _position_name(row: int, col: int, grid: int) -> str:
    """给人看的方位名（九宫格为例）。"""
    if grid == 3:
        names = [
            ["左上", "上中", "右上"],
            ["左中", "中心", "右中"],
            ["左下", "下中", "右下"],
        ]
        if 0 <= row < 3 and 0 <= col < 3:
            return names[row][col]
    return f"第{row+1}行第{col+1}列"


def _extract_session_id(image_ref: str) -> str:
    """从 PENDING:session_id:index 提取 session_id。"""
    parts = image_ref.split(":")
    if len(parts) >= 3:
        return parts[1]
    return "default"
