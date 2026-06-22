"""AuditEntry repo — Phase 1 stub.

L5 audit memory: append-only log of every action that crosses a Plane
boundary. Phase 2 backs this with PG (audit_entries table) plus optional
forwarding to a SIEM.
"""

from __future__ import annotations

from cognitiveplane.adapters.database.engine import AsyncDatabaseEngine
from cognitiveplane.shared.dto_gateway import AuditEntry
from cognitiveplane.shared.types import CaseId


class PostgresAuditRepository:
    def __init__(self, engine: AsyncDatabaseEngine) -> None:
        self._engine = engine
        self._entries: list[AuditEntry] = []

    async def append(self, entry: AuditEntry) -> None:
        async with self._engine.session():
            self._entries.append(entry)

    async def find_by_case(self, case_id: CaseId) -> list[AuditEntry]:
        return [e for e in self._entries if e.case_id == case_id]

    async def find_all(self, limit: int = 100) -> list[AuditEntry]:
        return list(self._entries[-limit:])
