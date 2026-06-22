"""Tests for memory/blocks.py — MemoryBlock, BlockVersion, BlockManager."""

from datetime import datetime, timezone

import pytest

from cognitiveplane.memory.blocks import BlockManager, BlockVersion, MemoryBlock


class TestMemoryBlock:
    def test_create_block_defaults(self):
        block = MemoryBlock(label="test", value="hello", limit=2000)
        assert block.label == "test"
        assert block.value == "hello"
        assert block.limit == 2000
        assert block.read_only is False
        assert block.description == ""
        assert block.tags == []
        assert block._version == 0

    def test_create_block_with_all_fields(self):
        block = MemoryBlock(
            label="persona",
            value="You are a welding inspector",
            limit=500,
            read_only=True,
            description="System persona block",
            tags=["system", "persona"],
        )
        assert block.read_only is True
        assert block.description == "System persona block"
        assert "system" in block.tags

    def test_block_timestamps(self):
        block = MemoryBlock(label="test", value="", limit=100)
        assert isinstance(block.created_at, datetime)
        assert isinstance(block.updated_at, datetime)


class TestBlockManager:
    def test_create_block(self):
        mgr = BlockManager()
        block = mgr.create_block("persona", "You are an inspector", 500, tags=["system"])
        assert block.label == "persona"
        assert block.value == "You are an inspector"
        assert "persona" in mgr.list_blocks()

    def test_create_block_defaults(self):
        mgr = BlockManager()
        block = mgr.create_block("test")
        assert block.value == ""
        assert block.limit == 2000
        assert block.tags == []

    def test_get_block(self):
        mgr = BlockManager()
        mgr.create_block("test", "value", 100)
        assert mgr.get_block("test") is not None
        assert mgr.get_block("nonexistent") is None

    def test_update_block(self):
        mgr = BlockManager()
        mgr.create_block("test", "old", 100)
        updated = mgr.update("test", "new value")
        assert updated.value == "new value"
        assert updated._version == 1

    def test_update_block_truncates_to_limit(self):
        mgr = BlockManager()
        mgr.create_block("test", "", limit=5)
        updated = mgr.update("test", "abcdefghij")
        assert updated.value == "abcde"

    def test_update_read_only_block_raises(self):
        mgr = BlockManager()
        mgr.create_block("readonly", "val", 100, read_only=True)
        with pytest.raises(PermissionError, match="read-only"):
            mgr.update("readonly", "new")

    def test_update_nonexistent_block_raises(self):
        mgr = BlockManager()
        with pytest.raises(KeyError, match="No block"):
            mgr.update("missing", "val")

    def test_checkpoint_and_undo(self):
        mgr = BlockManager()
        mgr.create_block("test", "v0", 100)

        mgr.update("test", "v1")
        mgr.checkpoint("test", reason="saved v1")

        mgr.update("test", "v2")
        assert mgr.get_block("test").value == "v2"

        undone = mgr.undo("test")
        assert undone.value == "v1"

    def test_undo_without_checkpoint_raises(self):
        mgr = BlockManager()
        mgr.create_block("test", "val", 100)
        with pytest.raises(ValueError, match="No checkpoint history"):
            mgr.undo("test")

    def test_checkpoint_nonexistent_raises(self):
        mgr = BlockManager()
        with pytest.raises(KeyError, match="No block"):
            mgr.checkpoint("missing")

    def test_query_by_tags(self):
        mgr = BlockManager()
        mgr.create_block("a", "", 100, tags=["system", "core"])
        mgr.create_block("b", "", 100, tags=["user"])
        mgr.create_block("c", "", 100, tags=["system", "helper"])

        results = mgr.query_by_tags(["system"])
        assert len(results) == 2
        labels = {b.label for b in results}
        assert labels == {"a", "c"}

    def test_query_by_tags_no_match(self):
        mgr = BlockManager()
        mgr.create_block("a", "", 100, tags=["system"])
        assert mgr.query_by_tags(["nonexistent"]) == []

    def test_compile_to_prompt(self):
        mgr = BlockManager()
        mgr.create_block("persona", "You are an inspector", 500, description="System persona")
        mgr.create_block("context", "Current case: W-001", 1000)

        xml = mgr.compile_to_prompt()
        assert "<memory_blocks>" in xml
        assert "</memory_blocks>" in xml
        assert "<persona>" in xml
        assert "<description>System persona</description>" in xml
        assert "You are an inspector" in xml
        assert "<context>" in xml

    def test_compile_to_prompt_escapes_xml(self):
        mgr = BlockManager()
        mgr.create_block("test", "<script>alert('xss')</script>", 100)
        xml = mgr.compile_to_prompt()
        assert "&lt;script&gt;" in xml
        assert "<script>" not in xml.split("<value>")[1].split("</value>")[0]

    def test_compile_to_prompt_read_only_metadata(self):
        mgr = BlockManager()
        mgr.create_block("readonly", "fixed", 100, read_only=True)
        xml = mgr.compile_to_prompt()
        assert "read_only=true" in xml

    def test_list_blocks(self):
        mgr = BlockManager()
        mgr.create_block("a", "", 100)
        mgr.create_block("b", "", 100)
        assert set(mgr.list_blocks()) == {"a", "b"}


class TestBlockVersion:
    def test_block_version_creation(self):
        now = datetime.now(timezone.utc)
        bv = BlockVersion(version=1, value="test", timestamp=now, change_reason="initial")
        assert bv.version == 1
        assert bv.value == "test"
        assert bv.change_reason == "initial"

    def test_block_version_no_reason(self):
        bv = BlockVersion(version=2, value="updated", timestamp=datetime.now(timezone.utc))
        assert bv.change_reason is None
