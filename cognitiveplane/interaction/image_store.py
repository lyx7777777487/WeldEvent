"""ImageStore — 焊缝图片分层存储 (Thumbnail + 原图)。

Plan §A.3 "图像细节渐进式获取" 架构层硬性约束：
- 上传图统一生成 Thumbnail (~85 tokens) 注入消息层
- 原图保留在服务端 (内存 dict / 阶段 3+ 切 MinIO)
- LLM 默认看到 Thumbnail；通过 analyze_image / request_image_detail 工具
  显式请求原图

Plan §2.3 "多模态输入双轨"：
- 消息层: Thumbnail data URL 直接进 LLM content parts
- 工具层: analyze_image 只传 image_id，工具内部从 ImageStore 取原图

本模块是阶段 2 内存实现。阶段 3+ 接入 MinIO 时只需替换 _store/_fetch_original
为 MinIO client，接口不变。
"""

from __future__ import annotations

import base64
import io
import uuid
from dataclasses import dataclass


THUMBNAIL_MAX_SIZE = 512  # px — 约束 thumbnail ~85 tokens
THUMBNAIL_JPEG_QUALITY = 75


@dataclass
class StoredImage:
    """One stored image: original bytes + pre-computed thumbnail data URL."""

    image_id: str
    original_mime: str
    original_bytes: bytes
    thumbnail_data_url: str  # data:image/jpeg;base64,...


class ImageStore:
    """内存实现。阶段 3+ 接入 MinIO 时替换为持久后端，接口保持不变。"""

    def __init__(self) -> None:
        self._store: dict[str, StoredImage] = {}

    def store(self, raw_bytes: bytes, mime_type: str) -> StoredImage:
        """存原图 + 生成 Thumbnail，返回 StoredImage (含 image_id)。"""
        image_id = str(uuid.uuid4())
        thumbnail = _generate_thumbnail(raw_bytes)
        stored = StoredImage(
            image_id=image_id,
            original_mime=mime_type,
            original_bytes=raw_bytes,
            thumbnail_data_url=thumbnail,
        )
        self._store[image_id] = stored
        return stored

    def get_thumbnail(self, image_id: str) -> str | None:
        """Thumbnail data URL — 消息层使用。"""
        stored = self._store.get(image_id)
        return stored.thumbnail_data_url if stored else None

    def get_original(self, image_id: str) -> tuple[bytes, str] | None:
        """(original_bytes, mime_type) — 工具层 analyze_image 使用。"""
        stored = self._store.get(image_id)
        if stored is None:
            return None
        return stored.original_bytes, stored.original_mime

    def get_original_data_url(self, image_id: str) -> str | None:
        """原图 data URL — 给 vision_complete 用。"""
        result = self.get_original(image_id)
        if result is None:
            return None
        original_bytes, mime = result
        b64 = base64.b64encode(original_bytes).decode()
        return f"data:{mime};base64,{b64}"

    def exists(self, image_id: str) -> bool:
        return image_id in self._store

    def __len__(self) -> int:
        return len(self._store)


def _generate_thumbnail(raw_bytes: bytes) -> str:
    """生成 ~512px JPEG thumbnail，返回 data URL。

    Pillow 未安装时 fallback: 降级返回原图 data URL (阶段 2 测试期可接受，
    生产环境必须装 Pillow)。降级时记 stderr 警告。
    """
    try:
        from PIL import Image
    except ImportError:
        import sys
        print(
            "WARN: Pillow not installed — thumbnail falls back to original. "
            "Install Pillow for production use.",
            file=sys.stderr,
        )
        b64 = base64.b64encode(raw_bytes).decode()
        return f"data:image/jpeg;base64,{b64}"

    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img = img.convert("RGB")
        max_dim = max(img.size)
        if max_dim > THUMBNAIL_MAX_SIZE:
            scale = THUMBNAIL_MAX_SIZE / max_dim
            new_size = (int(img.width * scale), int(img.height * scale))
            img = img.resize(new_size, Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=THUMBNAIL_JPEG_QUALITY)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        # 解码失败 (非图片或损坏) — fallback 到原图
        b64 = base64.b64encode(raw_bytes).decode()
        return f"data:image/jpeg;base64,{b64}"