"""OpenTelemetry tracing setup — Phase 1 stub.

Phase 2 wires the OTLP exporter and a real `TracerProvider`. For now we
expose a no-op tracer that the rest of the codebase can call without
runtime dependencies on opentelemetry packages.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


@dataclass
class TracingConfig:
    service_name: str = "cognitiveplane"
    otlp_endpoint: str = "http://localhost:4317"
    sample_rate: float = 1.0
    enabled: bool = True


class _NoOpSpan:
    def set_attribute(self, key: str, value) -> None: ...
    def add_event(self, name: str, attributes: dict | None = None) -> None: ...
    def record_exception(self, exception: BaseException) -> None: ...
    def end(self) -> None: ...


class NoOpTracer:
    """Phase 1 placeholder. Same surface as opentelemetry.trace.Tracer."""

    @contextmanager
    def start_as_current_span(
        self, name: str, attributes: dict | None = None
    ) -> Iterator[_NoOpSpan]:
        span = _NoOpSpan()
        try:
            yield span
        finally:
            span.end()


_tracer: NoOpTracer = NoOpTracer()


def setup_tracing(config: TracingConfig | None = None) -> NoOpTracer:
    """Phase 1: returns a process-wide no-op tracer."""
    return _tracer


def get_tracer(_name: str = "cognitiveplane") -> NoOpTracer:
    return _tracer
