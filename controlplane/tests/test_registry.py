import pytest

from controlplane.adapter.base import ActivityAdapter
from controlplane.domain.activity import ActivityInput, ActivityOutput, ActivityStatus
from controlplane.domain.template import ActivityBinding
from controlplane.infrastructure.registry import ActivityRegistry


class MockAdapter(ActivityAdapter):
    def __init__(self, name: str, version: str, status: ActivityStatus = ActivityStatus.OK):
        self._name = name
        self._version = version
        self._status = status

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return self._version

    async def execute(self, input: ActivityInput) -> ActivityOutput:
        return ActivityOutput(status=self._status, data={"adapter": self._name})


def test_register_single_adapter():
    registry = ActivityRegistry()
    adapter = MockAdapter("iqa_activity", "v1")
    registry.register(adapter)
    found = registry.get("iqa_activity")
    assert found.name == "iqa_activity"
    assert found.version == "v1"


def test_register_multiple_versions():
    registry = ActivityRegistry()
    registry.register(MockAdapter("mea_activity", "v1"))
    registry.register(MockAdapter("mea_activity", "v2"))
    latest = registry.get("mea_activity")
    assert latest.version == "v2"


def test_get_specific_version():
    registry = ActivityRegistry()
    registry.register(MockAdapter("mea_activity", "v1"))
    registry.register(MockAdapter("mea_activity", "v2"))
    found = registry.get("mea_activity", "v1")
    assert found.version == "v1"


def test_get_nonexistent_raises():
    registry = ActivityRegistry()
    with pytest.raises(KeyError, match="not found"):
        registry.get("nonexistent")


def test_get_nonexistent_version_raises():
    registry = ActivityRegistry()
    registry.register(MockAdapter("mea_activity", "v1"))
    with pytest.raises(KeyError, match="version not found"):
        registry.get("mea_activity", "v99")


def test_bind_activity_binding():
    registry = ActivityRegistry()
    registry.register(MockAdapter("mea_activity", "v1"))
    registry.register(MockAdapter("mea_activity", "v2"))

    binding_v1 = ActivityBinding(activity_name="mea_activity", activity_version="v1")
    adapter = registry.bind(binding_v1)
    assert adapter.version == "v1"

    binding_latest = ActivityBinding(activity_name="mea_activity")
    adapter = registry.bind(binding_latest)
    assert adapter.version == "v2"


def test_bind_nonexistent_raises():
    registry = ActivityRegistry()
    binding = ActivityBinding(activity_name="nonexistent")
    with pytest.raises(KeyError):
        registry.bind(binding)


def test_all_returns_all_adapters():
    registry = ActivityRegistry()
    registry.register(MockAdapter("iqa_activity", "v1"))
    registry.register(MockAdapter("mea_activity", "v1"))
    registry.register(MockAdapter("mea_activity", "v2"))
    all_adapters = registry.all()
    assert len(all_adapters) == 3
    names = {a.name for a in all_adapters}
    assert names == {"iqa_activity", "mea_activity"}
