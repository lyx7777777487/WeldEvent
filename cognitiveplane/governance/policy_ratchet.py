"""PolicyRatchet — 失败沉淀为草稿规则 (plan §4.2.2 棘轮机制).

Plan §4.2.2 line 810-838:
  - 触发事件: Tier-A 连续 2 次 schema 失败 / Tier-B REASK 拒绝 / 工具超时 >30s / CAS 冲突
  - 草稿区: governance/policy_ratchet.yaml (append-only)
  - 字段: tool_name, failure_pattern, suggested_rule, ts, trigger_event
  - 草稿不立即生效, 等运维评审 (Phase 5+ 入口)
  - 正式规则永不自动删除 (Phase 5+ 才有正式规则生效)

Phase 2 范围 (plan line 844): 草稿区自动记录 (事件触发即写). 不实现:
  - 人工评审入口 (Phase 5+)
  - 正式规则提升 (Phase 5+)
  - LLM 通知 (Phase 5+)

设计约束 (plan §4.2.2 反例 line 847-850):
  - ❌ 失败立即自动生成正式规则 — Phase 2 只写草稿, 不影响 ToolPolicy
  - ❌ 规则自动 GC — 草稿区 append-only, 永不删除
  - ❌ LLM 自己写规则 — PolicyRatchet 由架构 (ReActEngine) 调用, LLM 不能直接写
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


# ── Trigger events (plan §4.2.2 line 819-822) ──
TRIGGER_SCHEMA_FAIL = "schema_fail"            # Tier-A 连续 2 次 schema 校验失败
TRIGGER_REASK_REJECT = "reask_reject"          # Tier-B 被 OnFailAction.REASK 拒绝
TRIGGER_TIMEOUT = "timeout"                    # 工具调用超时 (>30s)
TRIGGER_CAS_CONFLICT = "cas_conflict"          # 并发写 WeldMap CAS 失败

_VALID_TRIGGERS = frozenset({
    TRIGGER_SCHEMA_FAIL,
    TRIGGER_REASK_REJECT,
    TRIGGER_TIMEOUT,
    TRIGGER_CAS_CONFLICT,
})


@dataclass
class RatchetEntry:
    """One draft rule entry (草稿区一条记录)."""
    tool_name: str
    failure_pattern: str
    suggested_rule: str
    trigger_event: str
    ts: str
    # Optional context — helps Phase 5+ 评审
    arguments_preview: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "failure_pattern": self.failure_pattern,
            "suggested_rule": self.suggested_rule,
            "trigger_event": self.trigger_event,
            "ts": self.ts,
            "arguments_preview": self.arguments_preview,
            "session_id": self.session_id,
        }


class PolicyRatchet:
    """Append-only draft rule store. Phase 2: write-only, no promotion.

    Thread-safe via single lock. File writes are atomic (write-temp + rename).
    Phase 2 single-process MVP — no cross-process coordination needed.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        """Construct ratchet.

        path: YAML file location. None → governance/policy_ratchet.yaml (repo-relative).
              File is created on first record() call if it doesn't exist.
              Missing file is NOT an error — Phase 2 doesn't pre-create.
        """
        if path is None:
            # Default: repo root / governance / policy_ratchet.yaml
            # Resolve relative to this file's location (cognitiveplane/governance/)
            self._path = Path(__file__).parent / "policy_ratchet.yaml"
        else:
            self._path = Path(path)
        self._lock = threading.Lock()
        self._entries: list[dict[str, Any]] | None = None  # lazy load

    @property
    def path(self) -> Path:
        return self._path

    def record(
        self,
        tool_name: str,
        trigger_event: str,
        failure_pattern: str,
        suggested_rule: str,
        arguments_preview: dict[str, Any] | None = None,
        session_id: str = "",
    ) -> RatchetEntry:
        """Append one entry to draft store. Returns the entry.

        plan §4.2.2 line 824-828: 自动写入草稿区, 不立即生效.
        Errors during file write are swallowed — ratchet failure must never
        break the ReAct loop (plan §0.1: 架构替 LLM 做的看不见的事, 失败也安静).
        """
        if trigger_event not in _VALID_TRIGGERS:
            raise ValueError(
                f"Invalid trigger_event: {trigger_event!r}. Must be one of {sorted(_VALID_TRIGGERS)}"
            )

        entry = RatchetEntry(
            tool_name=tool_name,
            failure_pattern=failure_pattern,
            suggested_rule=suggested_rule,
            trigger_event=trigger_event,
            ts=datetime.now(timezone.utc).isoformat(),
            arguments_preview=arguments_preview or {},
            session_id=session_id,
        )

        with self._lock:
            try:
                self._append_to_file(entry.to_dict())
            except Exception:
                # Ratchet write failure must never break ReAct.
                # Caller already swallowed — but defense in depth: don't propagate.
                pass

        return entry

    def list_entries(self) -> list[dict[str, Any]]:
        """Return all draft entries (for Phase 5+ 评审 UI). Phase 2: read-only."""
        with self._lock:
            return list(self._load())

    def count(self) -> int:
        """Return entry count. Used by tests + observability."""
        with self._lock:
            return len(self._load())

    def clear(self) -> None:
        """Test-only: clear all entries. NOT exposed to production code path."""
        with self._lock:
            self._entries = []
            try:
                if self._path.exists():
                    self._path.unlink()
            except Exception:
                pass

    # ── internal ──

    def _load(self) -> list[dict[str, Any]]:
        """Lazy-load entries from file. Cached after first read."""
        if self._entries is not None:
            return self._entries
        if not self._path.exists():
            self._entries = []
            return self._entries
        try:
            data = yaml.safe_load(self._path.read_text(encoding="utf-8"))
            if data is None:
                self._entries = []
            elif isinstance(data, dict) and "entries" in data:
                self._entries = list(data["entries"])
            elif isinstance(data, list):
                # Legacy format — list of entries
                self._entries = list(data)
            else:
                self._entries = []
        except Exception:
            # Corrupt YAML — start fresh, don't crash the system
            self._entries = []
        return self._entries

    def _append_to_file(self, entry: dict[str, Any]) -> None:
        """Append entry to YAML file atomically.

        Format: {entries: [...]} — list under 'entries' key for forward-compat
        with Phase 5+ metadata fields (last_reviewed_at, etc).
        """
        entries = self._load()
        entries.append(entry)

        doc = {
            "entries": entries,
            # Phase 5+ will add: last_reviewed_at, reviewer, etc.
        }

        # Atomic write: temp file + rename
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(
            yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        tmp_path.replace(self._path)