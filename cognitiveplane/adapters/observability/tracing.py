"""Langfuse v3 OpenTelemetry-based tracing — Agent 可观测性.

Source: Langfuse Python SDK v3 文档 https://langfuse.com/docs/sdk/python/sdk-v3
设计原则:
  1. 无 Langfuse key 时自动降级为 no-op（不影响生产功能）
  2. 保留 setup_tracing()/get_tracer() API 兼容（已有测试依赖）
  3. 新增 observe 装饰器 re-export，供 react.py/openai_provider.py 使用
  4. 启动时调用 setup_tracing()，关闭时调用 flush()

集成方式（按 Langfuse v3 官方推荐）:
  - LLM 调用: @observe(as_type="generation") 自动记录 token/模型/耗时
  - 业务函数: @observe() 自动记录输入/输出/嵌套 span
  - Trace 上下文: 通过 OpenTelemetry context 自动传播父子关系
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Iterator, TypeVar

logger = logging.getLogger("tracing")

# ── Langfuse 可用性探测 ──
try:
    from langfuse import Langfuse, observe as _lf_observe, get_client as _lf_get_client
    _LANGFUSE_AVAILABLE = True
except ImportError:
    _LANGFUSE_AVAILABLE = False
    Langfuse = None  # type: ignore
    _lf_observe = None
    _lf_get_client = None

# 模块级就决定 Langfuse 是否真正启用：有包 + 有 key 才启用。
# 无 key 时 observe 装饰器直接透传，绝不触发 Langfuse client 初始化（避免 auth 警告）。
_LANGFUSE_ENABLED = (
    _LANGFUSE_AVAILABLE
    and bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))
    and bool(os.environ.get("LANGFUSE_SECRET_KEY"))
)
if not _LANGFUSE_ENABLED:
    os.environ["LANGFUSE_TRACING_ENABLED"] = "false"


@dataclass
class TracingConfig:
    """Langfuse 连接配置。

    字段对应 Langfuse v3 SDK 的环境变量：
      LANGFUSE_PUBLIC_KEY  → public_key
      LANGFUSE_SECRET_KEY  → secret_key
      LANGFUSE_HOST        → host（默认 cloud.langfuse.com）
      LANGFUSE_TRACING_ENABLED → enabled（无 key 时自动 False）
    """
    service_name: str = "cognitiveplane"
    otlp_endpoint: str = "http://localhost:4317"  # 保留兼容字段
    sample_rate: float = 1.0
    enabled: bool = True
    public_key: str | None = None
    secret_key: str | None = None
    host: str | None = None  # None -> 从 LANGFUSE_HOST env 读


# ── No-op 降级实现（无 langfuse 包或无 key 时使用）──

class _NoOpSpan:
    def set_attribute(self, key: str, value) -> None: ...
    def add_event(self, name: str, attributes: dict | None = None) -> None: ...
    def record_exception(self, exception: BaseException) -> None: ...
    def end(self) -> None: ...


class NoOpTracer:
    """无 Langfuse 时的降级 tracer，接口与 Langfuse client 一致。"""

    @contextmanager
    def start_as_current_span(
        self, name: str, attributes: dict | None = None
    ) -> Iterator[_NoOpSpan]:
        span = _NoOpSpan()
        try:
            yield span
        finally:
            span.end()

    def start_as_current_span_cm(self, name: str, **kwargs):
        return self.start_as_current_span(name)

    def flush(self) -> None: ...
    def shutdown(self) -> None: ...


# ── 全局 tracer 单例 ──
_tracer: Any = NoOpTracer()
_initialized: bool = False


def setup_tracing(config: TracingConfig | None = None) -> Any:
    """初始化 Langfuse tracing。进程级单例。

    无 Langfuse 包 / 无 key → 返回 NoOpTracer，所有 observe 装饰器变 no-op。
    有 key → 初始化 Langfuse client，返回真实 tracer。

    必须在应用启动时（lifespan startup）调用一次。
    """
    global _tracer, _initialized

    if _initialized:
        return _tracer

    cfg = config or TracingConfig()

    if not _LANGFUSE_AVAILABLE:
        logger.info("[tracing] langfuse package not installed — using NoOpTracer")
        _tracer = NoOpTracer()
        _initialized = True
        return _tracer

    # 从环境变量读取配置（Langfuse v3 SDK 自动读这些变量）
    public_key = cfg.public_key or os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = cfg.secret_key or os.environ.get("LANGFUSE_SECRET_KEY")

    if not public_key or not secret_key:
        logger.info("[tracing] no LANGFUSE keys — using NoOpTracer (set LANGFUSE_PUBLIC_KEY/SECRET_KEY to enable)")
        # 设 enabled=False 让 SDK 所有调用变 no-op
        os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
        # 抑制 langfuse SDK 在无 key 时的 "Authentication error" WARNING 噪音
        # （@observe 装饰器每次调用都会触发 client 初始化检查，无 key 时会打 warning）
        logging.getLogger("langfuse").setLevel(logging.ERROR)
        _tracer = NoOpTracer()
        _initialized = True
        return _tracer

    try:
        # Langfuse v3: 构造即初始化（自动读环境变量）
        client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=cfg.host or os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )
        _tracer = client
        logger.info("[tracing] Langfuse initialized: host=%s", cfg.host)
    except Exception as e:
        logger.warning("[tracing] Langfuse init failed (%s) — falling back to NoOpTracer", e)
        _tracer = NoOpTracer()

    _initialized = True
    return _tracer


def get_tracer(_name: str = "cognitiveplane") -> Any:
    """获取全局 tracer（Langfuse client 或 NoOpTracer）。"""
    if not _initialized:
        setup_tracing()
    return _tracer


def get_langfuse() -> Any:
    """获取 Langfuse client（等同 get_tracer，语义化别名）。"""
    return get_tracer()


def flush() -> None:
    """应用关闭时调用，确保所有 trace 已发送到 Langfuse。"""
    if _tracer and hasattr(_tracer, "flush"):
        try:
            _tracer.flush()
        except Exception as e:
            logger.warning("[tracing] flush failed: %s", e)


def shutdown() -> None:
    """应用关闭时调用（含 flush + 资源清理）。"""
    if _tracer and hasattr(_tracer, "shutdown"):
        try:
            _tracer.shutdown()
        except Exception as e:
            logger.warning("[tracing] shutdown failed: %s", e)


# ── observe 装饰器 re-export ──

F = TypeVar("F", bound=Callable[..., Any])


def observe(
    name: str | None = None,
    as_type: str | None = None,
    capture_input: bool = True,
    capture_output: bool = True,
):
    """Re-export Langfuse @observe 装饰器，无 langfuse 时降级为透传装饰器。

    用法（与 Langfuse v3 一致）:
        @observe()                        # 普通 span
        def business_fn(...): ...

        @observe(as_type="generation")   # LLM 调用 span（记录 token）
        async def llm_call(...): ...

    参数:
        name: 自定义 span 名称（默认用函数名）
        as_type: "generation" 表示 LLM 调用，None 表示普通 span
        capture_input: 是否捕获函数参数作为 span 输入
        capture_output: 是否捕获返回值作为 span 输出
    """
    if not _LANGFUSE_ENABLED or _lf_observe is None:
        # 未启用 Langfuse → 透传装饰器（不改变函数行为，不触发 client 初始化）
        def decorator(fn: F) -> F:
            return fn
        return decorator

    # 有 langfuse 且有 key → 用官方装饰器
    kwargs: dict[str, Any] = {}
    if name is not None:
        kwargs["name"] = name
    if as_type is not None:
        kwargs["as_type"] = as_type
    kwargs["capture_input"] = capture_input
    kwargs["capture_output"] = capture_output
    return _lf_observe(**kwargs)


__all__ = [
    "TracingConfig",
    "NoOpTracer",
    "setup_tracing",
    "get_tracer",
    "get_langfuse",
    "flush",
    "shutdown",
    "observe",
]
