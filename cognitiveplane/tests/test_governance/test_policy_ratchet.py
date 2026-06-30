"""Tests for PolicyRatchet — plan §4.2.2 棘轮机制草稿区.

Phase 2 scope (plan line 844): 草稿区自动记录. 不测 Phase 5+ 评审入口.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cognitiveplane.governance.policy_ratchet import (
    PolicyRatchet,
    RatchetEntry,
    TRIGGER_CAS_CONFLICT,
    TRIGGER_REASK_REJECT,
    TRIGGER_SCHEMA_FAIL,
    TRIGGER_TIMEOUT,
)


@pytest.fixture
def ratchet(tmp_path: Path) -> PolicyRatchet:
    """Isolated ratchet writing to temp file."""
    return PolicyRatchet(path=tmp_path / "test_ratchet.yaml")


class TestRecord:
    """record() appends entries to draft store."""

    def test_record_writes_one_entry(self, ratchet: PolicyRatchet) -> None:
        entry = ratchet.record(
            tool_name="adjust_parameter",
            trigger_event=TRIGGER_REASK_REJECT,
            failure_pattern="PolicyHook DENY: reason required",
            suggested_rule="Pre-validate reason field",
        )
        assert isinstance(entry, RatchetEntry)
        assert entry.tool_name == "adjust_parameter"
        assert entry.trigger_event == TRIGGER_REASK_REJECT
        assert entry.ts  # ISO timestamp populated
        assert ratchet.count() == 1

    def test_record_appends_multiple(self, ratchet: PolicyRatchet) -> None:
        for i in range(3):
            ratchet.record(
                tool_name=f"tool_{i}",
                trigger_event=TRIGGER_SCHEMA_FAIL,
                failure_pattern=f"fail {i}",
                suggested_rule="rule",
            )
        assert ratchet.count() == 3
        entries = ratchet.list_entries()
        assert [e["tool_name"] for e in entries] == ["tool_0", "tool_1", "tool_2"]

    def test_record_persists_across_instances(self, tmp_path: Path) -> None:
        path = tmp_path / "persist.yaml"
        r1 = PolicyRatchet(path=path)
        r1.record(
            tool_name="web_search",
            trigger_event=TRIGGER_SCHEMA_FAIL,
            failure_pattern="bad json",
            suggested_rule="tighten schema",
        )
        # New instance, same path — should see the entry
        r2 = PolicyRatchet(path=path)
        assert r2.count() == 1
        assert r2.list_entries()[0]["tool_name"] == "web_search"

    def test_record_with_all_triggers(self, ratchet: PolicyRatchet) -> None:
        for trigger in [TRIGGER_SCHEMA_FAIL, TRIGGER_REASK_REJECT, TRIGGER_TIMEOUT, TRIGGER_CAS_CONFLICT]:
            ratchet.record(
                tool_name="t",
                trigger_event=trigger,
                failure_pattern="x",
                suggested_rule="y",
            )
        assert ratchet.count() == 4
        triggers = {e["trigger_event"] for e in ratchet.list_entries()}
        assert triggers == {TRIGGER_SCHEMA_FAIL, TRIGGER_REASK_REJECT, TRIGGER_TIMEOUT, TRIGGER_CAS_CONFLICT}

    def test_record_invalid_trigger_raises(self, ratchet: PolicyRatchet) -> None:
        with pytest.raises(ValueError, match="Invalid trigger_event"):
            ratchet.record(
                tool_name="t",
                trigger_event="bogus",
                failure_pattern="x",
                suggested_rule="y",
            )

    def test_record_includes_arguments_preview(self, ratchet: PolicyRatchet) -> None:
        ratchet.record(
            tool_name="adjust_parameter",
            trigger_event=TRIGGER_REASK_REJECT,
            failure_pattern="x",
            suggested_rule="y",
            arguments_preview={"parameter_name": "voltage", "proposed_value": "12V"},
            session_id="s001",
        )
        entry = ratchet.list_entries()[0]
        assert entry["arguments_preview"]["parameter_name"] == "voltage"
        assert entry["session_id"] == "s001"


class TestAppendOnly:
    """草稿区 append-only — plan §4.2.2 反例 line 849: 不能自动 GC."""

    def test_clear_is_test_only(self, ratchet: PolicyRatchet) -> None:
        """clear() exists for tests but is not on any production code path."""
        ratchet.record(
            tool_name="t",
            trigger_event=TRIGGER_SCHEMA_FAIL,
            failure_pattern="x",
            suggested_rule="y",
        )
        ratchet.clear()
        assert ratchet.count() == 0

    def test_file_grows_monotonically(self, ratchet: PolicyRatchet) -> None:
        for i in range(5):
            ratchet.record(
                tool_name="t",
                trigger_event=TRIGGER_SCHEMA_FAIL,
                failure_pattern=f"fail {i}",
                suggested_rule="rule",
            )
        # All 5 entries present, none dropped
        assert ratchet.count() == 5
        patterns = [e["failure_pattern"] for e in ratchet.list_entries()]
        assert patterns == ["fail 0", "fail 1", "fail 2", "fail 3", "fail 4"]


class TestFileFormat:
    """YAML file format — {entries: [...]} for Phase 5+ forward-compat."""

    def test_file_has_entries_key(self, ratchet: PolicyRatchet) -> None:
        ratchet.record(
            tool_name="t",
            trigger_event=TRIGGER_SCHEMA_FAIL,
            failure_pattern="x",
            suggested_rule="y",
        )
        import yaml
        data = yaml.safe_load(ratchet.path.read_text(encoding="utf-8"))
        assert "entries" in data
        assert len(data["entries"]) == 1

    def test_missing_file_treated_as_empty(self, tmp_path: Path) -> None:
        r = PolicyRatchet(path=tmp_path / "nonexistent.yaml")
        assert r.count() == 0
        assert r.list_entries() == []

    def test_corrupt_yaml_treated_as_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "corrupt.yaml"
        path.write_text("{{{{not valid yaml", encoding="utf-8")
        r = PolicyRatchet(path=path)
        assert r.count() == 0  # graceful — doesn't crash


class TestDefaultPath:
    """Default path = governance/policy_ratchet.yaml."""

    def test_default_path_resolves_to_governance_dir(self) -> None:
        r = PolicyRatchet()
        assert r.path.name == "policy_ratchet.yaml"
        assert r.path.parent.name == "governance"