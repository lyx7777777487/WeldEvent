"""Tests for the Phase 1 adapter stubs (database, cache, storage, observability, weldmap)."""

from __future__ import annotations

import pytest

from cognitiveplane.adapters.cache.redis_client import RedisClient, RedisConfig
from cognitiveplane.adapters.database.engine import AsyncDatabaseEngine, DatabaseConfig
from cognitiveplane.adapters.database.models import ALL_TABLES
from cognitiveplane.adapters.observability.metrics import MetricsConfig, get_metrics, setup_metrics
from cognitiveplane.adapters.observability.tracing import TracingConfig, get_tracer, setup_tracing
from cognitiveplane.adapters.storage.minio_client import MinioClient, MinioConfig
from cognitiveplane.adapters.weldmap.event_sourcing import (
    CAS_WRITE_LUA,
    EventSourcingStore,
)
from cognitiveplane.adapters.weldmap.weldmap_http import (
    WeldMapAdapterConfig,
    WeldMapHTTPAdapter,
)


def test_database_config_defaults_and_tables_exposed():
    config = DatabaseConfig()
    assert "postgresql+asyncpg" in config.dsn
    assert config.pool_size > 0
    assert ALL_TABLES == (
        "brain_decisions",
        "memory_records",
        "knowledge_entries",
        "workflow_states",
        "human_reviews",
        "audit_entries",
    )


@pytest.mark.asyncio
async def test_async_engine_session_commits_on_success():
    engine = AsyncDatabaseEngine(DatabaseConfig())
    async with engine.session() as session:
        await session.execute("SELECT 1")
    assert await engine.health_check() is True


@pytest.mark.asyncio
async def test_async_engine_session_rolls_back_on_error():
    engine = AsyncDatabaseEngine(DatabaseConfig())
    with pytest.raises(RuntimeError):
        async with engine.session():
            raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_redis_client_kv_round_trip():
    redis = RedisClient(RedisConfig())
    await redis.set("k", "v")
    assert await redis.get("k") == "v"
    assert await redis.delete("k") == 1
    assert await redis.get("k") is None


@pytest.mark.asyncio
async def test_minio_client_put_get_delete_round_trip():
    storage = MinioClient(MinioConfig())
    location = await storage.put_object("bucket", "key", b"payload")
    assert location == "bucket/key"
    assert await storage.get_object("bucket", "key") == b"payload"
    assert await storage.delete_object("bucket", "key") is True
    assert await storage.get_object("bucket", "key") is None


def test_observability_setup_returns_singletons():
    tracer1 = setup_tracing(TracingConfig())
    tracer2 = get_tracer()
    assert tracer1 is tracer2

    metrics1 = setup_metrics(MetricsConfig())
    metrics2 = get_metrics()
    assert metrics1 is metrics2

    counter = metrics1.counter("test.counter")
    counter.add(1.0, attributes={"a": "1"})
    counter.add(2.0, attributes={"a": "1"})
    assert counter.total() == pytest.approx(3.0)

    histogram = metrics1.histogram("test.hist")
    histogram.record(0.5)
    histogram.record(1.5)
    assert histogram.count() == 2

    with tracer1.start_as_current_span("op") as span:
        span.set_attribute("k", "v")
        span.add_event("evt")


@pytest.mark.asyncio
async def test_weldmap_http_adapter_delegates_to_client():
    adapter = WeldMapHTTPAdapter(WeldMapAdapterConfig(base_url="http://test", api_key="key"))
    write_result = await adapter.write("decision", "case-1", {"v": 1})
    assert write_result["domain"] == "decision"
    assert await adapter.read("decision", "case-1") is None
    assert await adapter.search("decision", {}) == []
    assert await adapter.health_check() is True


def test_event_sourcing_cas_write_creates_event_and_bumps_version():
    store = EventSourcingStore()
    result = store.cas_write(
        "decision", "case-1", expected_version=0,
        new_data={"status": "MADE"},
        event={"type": "DECISION_MADE", "decision_id": "d1"},
    )
    assert result.success is True
    assert result.new_version == 1
    current = store.read_current("decision", "case-1")
    assert current is not None
    data, version = current
    assert data == {"status": "MADE"}
    assert version == 1


def test_event_sourcing_cas_write_rejects_stale_version():
    store = EventSourcingStore()
    store.cas_write("decision", "case-1", 0, {"status": "MADE"}, {"type": "X"})
    stale = store.cas_write("decision", "case-1", 0, {"status": "OVERRIDDEN"}, {"type": "Y"})
    assert stale.success is False
    assert stale.current_version == 1
    # successful retry with the right version
    fresh = store.cas_write("decision", "case-1", 1, {"status": "OVERRIDDEN"}, {"type": "Y"})
    assert fresh.success is True
    assert fresh.new_version == 2


def test_event_sourcing_replay_returns_ordered_events_and_snapshot():
    store = EventSourcingStore()
    store.cas_write("decision", "c", 0, {"v": 1}, {"seq": 1})
    store.cas_write("decision", "c", 1, {"v": 2}, {"seq": 2})
    store.cas_write("decision", "c", 2, {"v": 3}, {"seq": 3})
    events = store.replay_events("decision", "c")
    assert [e.sequence for e in events] == [1, 2, 3]
    tail = store.replay_events("decision", "c", from_sequence=2)
    assert [e.sequence for e in tail] == [3]
    snap = store.snapshot("decision", "c")
    assert snap == {"v": 3}


def test_event_sourcing_lua_script_constant_is_present():
    assert "redis.call('SET', key_data, new_data)" in CAS_WRITE_LUA
    assert "XADD" in CAS_WRITE_LUA
