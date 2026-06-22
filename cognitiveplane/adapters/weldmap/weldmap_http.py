"""WeldMap HTTP adapter — Phase 1 stub.

Wraps the gateway-level `WeldMapHTTPClient` so callers under `adapters/`
get a uniform construction surface (config + tracing hooks) for the L5
Data Plane. Phase 2 swaps the in-process delegate for a real httpx-based
client with retries, circuit breaking, and CAS-aware writes.
"""

from __future__ import annotations

from dataclasses import dataclass

from cognitiveplane.gateway.weldmap_client import WeldMapConfig, WeldMapHTTPClient


@dataclass
class WeldMapAdapterConfig:
    base_url: str = "http://localhost:8080"
    api_key: str = ""
    timeout_seconds: int = 30
    max_retries: int = 3


class WeldMapHTTPAdapter:
    """Adapter wrapping `WeldMapHTTPClient` for use from the composition root."""

    def __init__(self, config: WeldMapAdapterConfig | None = None) -> None:
        self._config = config or WeldMapAdapterConfig()
        self._client = WeldMapHTTPClient(
            WeldMapConfig(
                url=self._config.base_url,
                api_key=self._config.api_key,
                timeout=self._config.timeout_seconds,
            )
        )

    @property
    def config(self) -> WeldMapAdapterConfig:
        return self._config

    @property
    def client(self) -> WeldMapHTTPClient:
        return self._client

    async def write(self, domain: str, key: str, value: dict) -> dict:
        return await self._client.write(domain, key, value)

    async def read(self, domain: str, key: str) -> dict | None:
        return await self._client.read(domain, key)

    async def search(self, domain: str, query: dict) -> list[dict]:
        return await self._client.search(domain, query)

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None
