"""MinIO / S3 client — Phase 1 stub for image and report storage.

Phase 2 wires `aioboto3` against a MinIO endpoint or AWS S3.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MinioConfig:
    endpoint: str = "http://localhost:9000"
    access_key: str = "minio"
    secret_key: str = "minio123"
    bucket_default: str = "weldevent"
    secure: bool = False


class MinioClient:
    """Phase 1 stub: in-memory map of (bucket, key) → bytes."""

    def __init__(self, config: MinioConfig | None = None) -> None:
        self._config = config or MinioConfig()
        self._objects: dict[tuple[str, str], bytes] = {}

    @property
    def config(self) -> MinioConfig:
        return self._config

    async def put_object(
        self, bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        self._objects[(bucket, key)] = data
        return f"{bucket}/{key}"

    async def get_object(self, bucket: str, key: str) -> bytes | None:
        return self._objects.get((bucket, key))

    async def delete_object(self, bucket: str, key: str) -> bool:
        return self._objects.pop((bucket, key), None) is not None

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None
