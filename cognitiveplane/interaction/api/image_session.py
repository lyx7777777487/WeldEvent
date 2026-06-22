"""ImageSessionRegistry — session_id -> image_ids 映射。

Plan §2.3 工具层只传 image_id 引用，工具需要通过 image_id 找到原图。
ImageStore 是全局存储 (image_id → bytes)，ImageSessionRegistry 跟踪
每个 session 持有哪些 image_id (用于审计、清理、批量操作)。

阶段 2 内存实现，阶段 3+ 切 Redis 时接口不变。
"""

from __future__ import annotations

from cognitiveplane.interaction.image_store import ImageStore


class ImageSessionRegistry:
    """session_id -> [image_id] 映射。ImageStore 是底层存储，本类只跟踪归属。"""

    def __init__(self, image_store: ImageStore) -> None:
        self._store = image_store
        self._sessions: dict[str, list[str]] = {}

    def register(self, session_id: str, image_ids: list[str]) -> None:
        existing = self._sessions.setdefault(session_id, [])
        for image_id in image_ids:
            if image_id not in existing:
                existing.append(image_id)

    def list_images(self, session_id: str) -> list[str]:
        return list(self._sessions.get(session_id, []))

    def get_original(self, image_id: str) -> tuple[bytes, str] | None:
        return self._store.get_original(image_id)

    def get_original_data_url(self, image_id: str) -> str | None:
        return self._store.get_original_data_url(image_id)

    def get_thumbnail(self, image_id: str) -> str | None:
        return self._store.get_thumbnail(image_id)

    def exists(self, image_id: str) -> bool:
        return self._store.exists(image_id)