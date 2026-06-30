"""ImageStore — 焊缝图片分层存储 (Thumbnail + 原图)。

Plan §A.3 "图像细节渐进式获取" 架构层硬性约束：
- 上传图统一生成 Thumbnail (~85 tokens) 注入消息层
- 原图保留在服务端 (内存 dict / 阶段 3+ 切 MinIO)
- LLM 默认看到 Thumbnail；通过 analyze_image / request_image_detail 工具
  显式请求原图

Plan §2.3 "多模态输入双轨"：
- 消息层: Thumbnail data URL 直接进 LLM content parts
- 工具层: analyze_image 只传 image_ref，工具内部从 ImageStore 取原图
- image_ref 格式: PENDING:{session_id}:{index} (plan §2.3 line 345-348)
  LLM 能从 ID 推断上传顺序，便于多图场景引用。

本模块是阶段 2 内存实现。阶段 3+ 接入 MinIO 时只需替换 _store/_fetch_original
为 MinIO client，接口不变。
"""

from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass, field


THUMBNAIL_MAX_SIZE = 512  # px — 约束 thumbnail ~85 tokens
THUMBNAIL_JPEG_QUALITY = 75

# image_ref prefix per plan §2.3 line 346: {"image_ref": "PENDING:session_id:0"}
PENDING_PREFIX = "PENDING:"

# P2-2 fix: TTL 默认 1 小时。launch_workflow 通常几秒内就解析 image_ref → 落盘，
# 之后 ImageStore 里的原图 bytes 不再被引用。1 小时足够覆盖 workflow 重试 + 人工延迟。
DEFAULT_TTL_SECONDS = 3600.0
# P2-2 fix: 清理阈值。store() 每次追加新图时检查，超过 _MAX_ENTRIES 才触发清理，
# 避免每次 store 都扫全表。设为 0 可禁用自动清理（测试用）。
DEFAULT_MAX_ENTRIES = 100


@dataclass
class StoredImage:
    """One stored image: original bytes + pre-computed thumbnail data URL."""

    image_id: str
    original_mime: str
    original_bytes: bytes
    thumbnail_data_url: str  # data:image/jpeg;base64,...
    session_id: str = ""
    index: int = 0
    # P2-2 fix: 创建时间（monotonic），用于 TTL 过期清理
    created_at: float = field(default_factory=time.monotonic)


class ImageStore:
    """内存实现。阶段 3+ 接入 MinIO 时替换为持久后端，接口保持不变。"""

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        """P2-2 fix: 添加 TTL + max_entries 防内存泄漏。

        Args:
            ttl_seconds: 单张图片在 store 中的最长存活时间（秒）。
                超过后 cleanup() 会移除。默认 1 小时。
            max_entries: 触发自动清理的条目阈值。store() 达到此值时
                调用 cleanup() 清理过期项。设为 0 禁用自动清理。
        """
        self._store: dict[str, StoredImage] = {}
        # Per-session upload counter — drives the {index} in PENDING:{session}:{index}
        self._session_counters: dict[str, int] = {}
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries

    def store(
        self, raw_bytes: bytes, mime_type: str, session_id: str = "default"
    ) -> StoredImage:
        """存原图 + 生成 Thumbnail，返回 StoredImage (含 image_id)。

        image_id 格式: PENDING:{session_id}:{index} (plan §2.3 line 345-348).
        index 是该 session 内的递增序号 (从 0 开始)，LLM 可从 ID 推断上传顺序。
        """
        index = self._session_counters.get(session_id, 0)
        image_id = f"{PENDING_PREFIX}{session_id}:{index}"
        self._session_counters[session_id] = index + 1

        thumbnail = _generate_thumbnail(raw_bytes)
        stored = StoredImage(
            image_id=image_id,
            original_mime=mime_type,
            original_bytes=raw_bytes,
            thumbnail_data_url=thumbnail,
            session_id=session_id,
            index=index,
        )
        self._store[image_id] = stored

        # P2-2 fix: 超过阈值时触发清理（惰性回收，不阻塞上传）
        if self._max_entries > 0 and len(self._store) > self._max_entries:
            self.cleanup()
        return stored

    def cleanup(self, now: float | None = None) -> int:
        """P2-2 fix: 移除超过 TTL 的过期图片。

        Args:
            now: 当前 monotonic 时间戳（测试可注入）。None 则用 time.monotonic()。

        Returns:
            移除的条目数。
        """
        if self._ttl_seconds <= 0:
            return 0
        if now is None:
            now = time.monotonic()
        expired_ids = [
            img_id for img_id, img in self._store.items()
            if (now - img.created_at) > self._ttl_seconds
        ]
        for img_id in expired_ids:
            del self._store[img_id]
        return len(expired_ids)

    def get_thumbnail(self, image_id: str) -> str | None:
        """Thumbnail data URL — 消息层使用。"""
        stored = self._store.get(image_id)
        return stored.thumbnail_data_url if stored else None

    def get_original(self, image_id: str) -> tuple[bytes, str] | None:
        """(original_bytes, mime_type) — 工具层 analyze_image 使用。

        plan §2.3 line 348: SessionStore 解析 PENDING:xxx → 取回真正的图片数据.
        本实现用 dict 直接查找；阶段 3+ SessionStore 可解析 PENDING 串路由到分片存储。
        """
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