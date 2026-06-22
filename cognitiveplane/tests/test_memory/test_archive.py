"""Tests for memory/archive.py — ArchiveStore and InMemoryArchiveStore."""

import pytest

from cognitiveplane.memory.archive import InMemoryArchiveStore


class FakeRecord:
    def __init__(self, memory_id: str, content: str):
        self.memory_id = memory_id
        self.content = content


class TestInMemoryArchiveStore:
    @pytest.mark.asyncio
    async def test_store_and_retrieve(self):
        store = InMemoryArchiveStore()
        record = FakeRecord("rec-1", "Test archival content")
        record_id = await store.store(record)
        retrieved = await store.retrieve(record_id)
        assert retrieved is not None
        assert retrieved.content == "Test archival content"

    @pytest.mark.asyncio
    async def test_retrieve_nonexistent(self):
        store = InMemoryArchiveStore()
        result = await store.retrieve("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_search_by_content(self):
        store = InMemoryArchiveStore()
        await store.store(FakeRecord("1", "Welding defect pattern A"))
        await store.store(FakeRecord("2", "Inspection result B"))
        await store.store(FakeRecord("3", "Welding procedure C"))

        results = await store.search("welding", limit=10)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_search_case_insensitive(self):
        store = InMemoryArchiveStore()
        await store.store(FakeRecord("1", "WELDING DEFECT"))

        results = await store.search("welding", limit=10)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_respects_limit(self):
        store = InMemoryArchiveStore()
        for i in range(10):
            await store.store(FakeRecord(str(i), f"Welding record {i}"))

        results = await store.search("welding", limit=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_search_no_match(self):
        store = InMemoryArchiveStore()
        await store.store(FakeRecord("1", "Some content"))

        results = await store.search("nonexistent", limit=10)
        assert results == []

    @pytest.mark.asyncio
    async def test_store_returns_record_id(self):
        store = InMemoryArchiveStore()
        record = FakeRecord("my-id-123", "content")
        record_id = await store.store(record)
        assert record_id == "my-id-123"
