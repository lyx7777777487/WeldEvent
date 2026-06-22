from controlplane.adapter.base import ActivityAdapter
from controlplane.domain.template import ActivityBinding


class ActivityRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, dict[str, ActivityAdapter]] = {}

    def register(self, adapter: ActivityAdapter) -> None:
        if adapter.name not in self._adapters:
            self._adapters[adapter.name] = {}
        self._adapters[adapter.name][adapter.version] = adapter

    def get(self, name: str, version: str = "latest") -> ActivityAdapter:
        versions = self._adapters.get(name)
        if not versions:
            raise KeyError(f"Activity adapter not found: {name}")
        if version == "latest":
            return max(versions.values(), key=lambda a: a.version)
        adapter = versions.get(version)
        if not adapter:
            raise KeyError(f"Activity adapter version not found: {name}@{version}")
        return adapter

    def bind(self, binding: ActivityBinding) -> ActivityAdapter:
        return self.get(binding.activity_name, binding.activity_version)

    def all(self) -> list[ActivityAdapter]:
        result = []
        for versions in self._adapters.values():
            result.extend(versions.values())
        return result
