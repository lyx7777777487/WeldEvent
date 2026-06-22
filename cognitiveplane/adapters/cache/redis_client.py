"""Redis client adapter — Phase 1 stub.

Hosts:
- L0/L1 memory cache (hot scratch + reusable summaries)
- Per-operator session state (BlockManager block storage)
- DistributedLock primitives for cross-process coordination

Phase 2 swaps the in-memory dict for `redis.asyncio.Redis`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class RedisConfig:
    url: str = "redis://localhost:6379/0"
    socket_timeout: float = 2.0
    decode_responses: bool = True


class RedisClient:
    """Phase 1: in-memory dict implementation.

    The interface intentionally mirrors a small subset of redis-py's async
    client (``get``/``set``/``delete``/``hset``/``hget``/``hgetall``) so the
    Phase 2 swap is line-for-line.
    """

    def __init__(self, config: RedisConfig | None = None) -> None:
        self._config = config or RedisConfig()
        self._kv: dict[str, str] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._streams: dict[str, list[dict[str, Any]]] = {}

    @property
    def config(self) -> RedisConfig:
        return self._config

    # --- KV ------------------------------------------------------------

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._kv[key] = value

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._kv:
                del self._kv[k]
                count += 1
        return count

    # --- Hash ----------------------------------------------------------

    async def hset(self, name: str, field: str, value: str) -> None:
        self._hashes.setdefault(name, {})[field] = value

    async def hget(self, name: str, field: str) -> str | None:
        return self._hashes.get(name, {}).get(field)

    async def hgetall(self, name: str) -> dict[str, str]:
        return dict(self._hashes.get(name, {}))

    # --- Stream (used by EventSourcing CAS) ---------------------------

    async def xadd(self, stream: str, fields: dict[str, Any]) -> str:
        self._streams.setdefault(stream, []).append(dict(fields))
        return f"{stream}-{len(self._streams[stream])}"

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None
