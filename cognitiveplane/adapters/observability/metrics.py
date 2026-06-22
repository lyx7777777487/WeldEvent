"""OpenTelemetry metrics setup — Phase 1 stub.

Phase 2 wires real `Meter` instruments against the OTLP metrics exporter.
For now we expose simple in-memory counters/histograms that satisfy the
call sites without runtime OTel dependencies.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass
class MetricsConfig:
    service_name: str = "cognitiveplane"
    otlp_endpoint: str = "http://localhost:4317"
    enabled: bool = True


class InMemoryCounter:
    def __init__(self, name: str) -> None:
        self.name = name
        self._values: dict[tuple, float] = defaultdict(float)

    def add(self, value: float, attributes: dict | None = None) -> None:
        key = tuple(sorted((attributes or {}).items()))
        self._values[key] += value

    def total(self) -> float:
        return sum(self._values.values())


class InMemoryHistogram:
    def __init__(self, name: str) -> None:
        self.name = name
        self._observations: list[float] = []

    def record(self, value: float, attributes: dict | None = None) -> None:
        self._observations.append(value)

    def count(self) -> int:
        return len(self._observations)


class MetricsRegistry:
    def __init__(self) -> None:
        self._counters: dict[str, InMemoryCounter] = {}
        self._histograms: dict[str, InMemoryHistogram] = {}

    def counter(self, name: str) -> InMemoryCounter:
        if name not in self._counters:
            self._counters[name] = InMemoryCounter(name)
        return self._counters[name]

    def histogram(self, name: str) -> InMemoryHistogram:
        if name not in self._histograms:
            self._histograms[name] = InMemoryHistogram(name)
        return self._histograms[name]


_registry = MetricsRegistry()


def setup_metrics(config: MetricsConfig | None = None) -> MetricsRegistry:
    return _registry


def get_metrics() -> MetricsRegistry:
    return _registry
