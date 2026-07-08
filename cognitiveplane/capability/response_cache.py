"""LLM 响应缓存 — 进程内 LRU + 可选 TTL。

设计原则:
  1. 只缓存 temperature == 0 的确定性请求（高温度每次采样不同，不缓存）
  2. 不缓存 tool_calls 响应（function calling 应实时反映 LLM 当前判断）
  3. stream() 不走缓存（流式本身就是为了实时性）
  4. key 由 messages + model + temperature + max_tokens + top_p +
     response_format(schema) + tools 组合 hash 而成；caller/case_id
     是 metadata，不影响输出，不参与 key

容量: 默认 256 条（按平均 2KB response 估算约 512KB 内存）
TTL: 默认 None（永不过期，靠 LRU 淘汰）；可设秒数

单进程方案 — 跨进程共享需升级到 Redis（adapters/cache/redis_client.py 已预留）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Any

from cognitiveplane.capability.provider import LLMRequest, LLMResponse

logger = logging.getLogger("llm_cache")


class LLMResponseCache:
    """进程内 LLM 响应缓存（LRU + 可选 TTL）。

    线程安全说明：依赖 GIL 保护 OrderedDict 操作。asyncio 单线程
    事件循环下无竞争。多线程同步调用需自行加锁（当前无此场景）。
    """

    def __init__(
        self,
        maxsize: int = 256,
        ttl_seconds: float | None = None,
    ) -> None:
        self._maxsize = maxsize
        self._ttl = ttl_seconds
        self._store: OrderedDict[str, tuple[float, LLMResponse]] = OrderedDict()
        # 统计
        self._hits = 0
        self._misses = 0

    # ── key 计算 ──

    @staticmethod
    def _response_format_key(rf: Any) -> str:
        """response_format 可能是 type（pydantic model）、dict 或 None。"""
        if rf is None:
            return "none"
        if isinstance(rf, dict):
            return json.dumps(rf, sort_keys=True, default=str)
        if isinstance(rf, type):
            # pydantic model class — 用其 schema 作为指纹
            try:
                schema = rf.model_json_schema()  # type: ignore[attr-defined]
                return json.dumps(schema, sort_keys=True, default=str)
            except Exception:
                return rf.__name__
        return str(rf)

    @classmethod
    def make_key(cls, request: LLMRequest) -> str:
        """根据 request 算出稳定缓存 key。

        排除字段：caller / case_id / purpose（metadata，不影响输出）。
        包含字段：messages / model / temperature / max_tokens / top_p /
                  response_format / tools
        """
        payload = {
            "messages": request.messages,
            "model": request.model,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "top_p": request.top_p,
            "response_format": cls._response_format_key(request.response_format),
            "tools": request.tools,
        }
        # 用 sort_keys + default=str 保证序列化稳定且可处理非 JSON 类型
        raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # ── 可缓存性判定 ──

    @staticmethod
    def is_cacheable_request(request: LLMRequest) -> bool:
        """只有 temperature==0 的请求才缓存（确定性输出）。

        temperature > 0 时每次采样不同，缓存会破坏随机性期望。
        """
        return request.temperature == 0.0

    @staticmethod
    def is_cacheable_response(response: LLMResponse) -> bool:
        """tool_calls 响应不缓存（function calling 应实时反映 LLM 判断）。"""
        return not response.tool_calls

    # ── 读写 ──

    def get(self, key: str) -> LLMResponse | None:
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None
        ts, response = entry
        # TTL 过期检查
        if self._ttl is not None and (time.monotonic() - ts) > self._ttl:
            self._store.pop(key, None)
            self._misses += 1
            logger.debug("[llm_cache] key=%s.. expired", key[:12])
            return None
        # LRU: 命中时移到末尾（最近使用）
        self._store.move_to_end(key)
        self._hits += 1
        logger.debug("[llm_cache] HIT key=%s..", key[:12])
        return response

    def set(self, key: str, response: LLMResponse) -> None:
        self._store[key] = (time.monotonic(), response)
        self._store.move_to_end(key)
        # LRU 淘汰
        while len(self._store) > self._maxsize:
            evicted_key, _ = self._store.popitem(last=False)
            logger.debug("[llm_cache] EVICT key=%s..", evicted_key[:12])

    def get_or_compute_key(self, request: LLMRequest) -> str | None:
        """若 request 可缓存，返回其 key；否则返回 None。"""
        if not self.is_cacheable_request(request):
            return None
        return self.make_key(request)

    # ── 统计 ──

    def stats(self) -> dict[str, int | float]:
        total = self._hits + self._misses
        return {
            "size": len(self._store),
            "maxsize": self._maxsize,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": (self._hits / total) if total else 0.0,
        }

    def clear(self) -> None:
        self._store.clear()
        self._hits = 0
        self._misses = 0


__all__ = ["LLMResponseCache"]
