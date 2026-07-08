"""LLM bootstrap — provider wiring & cache construction.

从 app.py 拆出的 LLM 装配逻辑：
  probe_chat       — 探测 OpenAI 兼容端点连通性
  bootstrap_llm    — 根据环境变量装配真实/mock LLM provider
  build_llm_cache  — 构造进程内 LLM 响应缓存
"""

from __future__ import annotations

import logging
import os

from cognitiveplane.capability import init_llm
from cognitiveplane.capability.config import LLMConfig, OpenAIConfig
from cognitiveplane.capability.mock import MockLLMProvider
from cognitiveplane.capability.openai_provider import OpenAIProvider
from cognitiveplane.capability.response_cache import LLMResponseCache

logger = logging.getLogger(__name__)


def probe_chat(base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    import json as _json
    import urllib.request

    url = f"{base_url}/chat/completions"
    payload = _json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30):
            return True, ""
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode()[:200]
        except Exception:
            logger.debug("health-check body read failed", exc_info=True)
        return False, f"HTTP {exc.code}: {body}"
    except Exception as exc:
        return False, str(exc)[:200]


def bootstrap_llm() -> bool:
    """Bootstrap LLM provider. Returns True if real LLM wired."""
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    volc_key = os.getenv("VOLC_API_KEY")  # 火山引擎 API Key

    if deepseek_key:
        base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        cfg = LLMConfig(
            primary=OpenAIConfig(api_key=deepseek_key, base_url=base, default_model=model),
            intent_classifier_model=model,
            reasoning_model=model,
            planning_model=model,
            explanation_model=model,
        )
        # 火山引擎多模态模型配置
        if volc_key:
            volc_base = os.getenv("VOLC_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
            volc_model = os.getenv("VOLC_VISION_MODEL", "doubao-vision-pro-32k")
            cfg.vision = OpenAIConfig(
                api_key=volc_key,
                base_url=volc_base,
                default_model=volc_model,
            )
            cfg.vision_model = volc_model
        probe_ok, probe_err = probe_chat(base, deepseek_key, model)
        if probe_ok:
            init_llm(OpenAIProvider(cfg, cache=build_llm_cache()))
            return True
        if probe_err:
            print(f"  [LLM] DeepSeek 连接失败 — {probe_err}")
        init_llm(MockLLMProvider(default_response="(mock LLM response)"))
        return False

    if openai_key:
        cfg = LLMConfig(
            primary=OpenAIConfig(
                api_key=openai_key,
                base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                default_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            )
        )
        # 火山引擎多模态模型配置
        if volc_key:
            volc_base = os.getenv("VOLC_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
            volc_model = os.getenv("VOLC_VISION_MODEL", "doubao-vision-pro-32k")
            cfg.vision = OpenAIConfig(
                api_key=volc_key,
                base_url=volc_base,
                default_model=volc_model,
            )
            cfg.vision_model = volc_model
        init_llm(OpenAIProvider(cfg, cache=build_llm_cache()))
        return True

    init_llm(MockLLMProvider(default_response="(mock LLM response)"))
    return False


def build_llm_cache() -> LLMResponseCache:
    """构造进程内 LLM 响应缓存。

    配置通过环境变量：
      LLM_CACHE_MAXSIZE — 最大条目数（默认 256）
      LLM_CACHE_TTL     — TTL 秒（默认 0=永不过期，靠 LRU 淘汰）
    """
    maxsize = int(os.environ.get("LLM_CACHE_MAXSIZE", "256"))
    ttl_raw = os.environ.get("LLM_CACHE_TTL", "0")
    ttl = float(ttl_raw) if ttl_raw and float(ttl_raw) > 0 else None
    return LLMResponseCache(maxsize=maxsize, ttl_seconds=ttl)
