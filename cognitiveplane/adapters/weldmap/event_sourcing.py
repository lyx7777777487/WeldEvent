"""WeldMap Event Sourcing + CAS — Phase 1 stub.

Spec §17.7 mandates Redis-backed Event Sourcing with optimistic CAS via a
Lua script and materialized views for fast reads. Phase 2 wires the script
against a real Redis client (and NATS for downstream notification).

Phase 1 ships:
- `CAS_WRITE_LUA`: the canonical Lua script body, kept verbatim from the spec
  so future phases can register it without touching call sites.
- `EventSourcingStore`: an in-memory implementation of the contract
  (`cas_write`, `read_current`, `replay_events`, `snapshot`) so Brain code
  paths can exercise the API without Redis.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


CAS_WRITE_LUA = """
local key_data = KEYS[1]
local key_version = KEYS[2]
local key_events = KEYS[3]
local expected_version = tonumber(ARGV[1])
local new_data = ARGV[2]
local new_version = tonumber(ARGV[3])
local event_data = ARGV[4]

if expected_version >= 0 then
    local current = tonumber(redis.call('GET', key_version) or '0')
    if current ~= expected_version then
        return {0, current}
    end
end

redis.call('SET', key_data, new_data)
redis.call('SET', key_version, new_version)
redis.call('XADD', key_events, '*', 'data', event_data)
return {1, new_version}
"""


@dataclass(frozen=True)
class CASWriteResult:
    success: bool
    new_version: int
    current_version: int


@dataclass(frozen=True)
class StoredEvent:
    sequence: int
    timestamp_ms: int
    data: dict[str, Any]


@dataclass
class _DomainKey:
    data: dict[str, Any] = field(default_factory=dict)
    version: int = 0
    events: list[StoredEvent] = field(default_factory=list)
    snapshot: dict[str, Any] | None = None


class EventSourcingStore:
    """In-memory Event Sourcing store mimicking the Redis Lua CAS contract.

    Phase 1 keeps a single `(domain, key)` map so callers can exercise the
    write/read/replay surface in tests without standing up Redis.
    """

    def __init__(self) -> None:
        self._domains: dict[tuple[str, str], _DomainKey] = {}

    def _slot(self, domain: str, key: str) -> _DomainKey:
        slot = self._domains.get((domain, key))
        if slot is None:
            slot = _DomainKey()
            self._domains[(domain, key)] = slot
        return slot

    def cas_write(
        self,
        domain: str,
        key: str,
        expected_version: int,
        new_data: dict[str, Any],
        event: dict[str, Any],
    ) -> CASWriteResult:
        slot = self._slot(domain, key)
        if expected_version >= 0 and slot.version != expected_version:
            return CASWriteResult(success=False, new_version=slot.version, current_version=slot.version)

        slot.data = dict(new_data)
        slot.version += 1
        slot.events.append(
            StoredEvent(
                sequence=slot.version,
                timestamp_ms=int(time.time() * 1000),
                data=dict(event),
            )
        )
        return CASWriteResult(success=True, new_version=slot.version, current_version=slot.version)

    def read_current(self, domain: str, key: str) -> tuple[dict[str, Any], int] | None:
        slot = self._domains.get((domain, key))
        if slot is None or slot.version == 0:
            return None
        return dict(slot.data), slot.version

    def replay_events(
        self,
        domain: str,
        key: str,
        from_sequence: int = 0,
    ) -> list[StoredEvent]:
        slot = self._domains.get((domain, key))
        if slot is None:
            return []
        return [event for event in slot.events if event.sequence > from_sequence]

    def snapshot(self, domain: str, key: str) -> dict[str, Any] | None:
        slot = self._domains.get((domain, key))
        if slot is None:
            return None
        slot.snapshot = dict(slot.data)
        return slot.snapshot

    def serialize_event(self, event: dict[str, Any]) -> str:
        """Helper exposing the JSON encoding the Lua script would receive."""
        return json.dumps(event, sort_keys=True)
