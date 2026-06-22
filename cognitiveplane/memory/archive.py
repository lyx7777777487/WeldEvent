"""Archival storage — agent-controlled, distinct from promotion.

Source: 7-plane redesign spec §10 Agent-Controlled Archival.
Inspired by Letta archival_memory_insert — agent decides what to archive.
No auto-promotion from working to archival.
"""

from typing import Any, Protocol


class ArchiveStore(Protocol):
    """Protocol for archival storage backends."""

    async def store(self, record: Any) -> str: ...
    async def retrieve(self, record_id: str) -> Any | None: ...
    async def search(self, query: str, limit: int = 10) -> list[Any]: ...


class InMemoryArchiveStore:
    """In-memory archival store for development and testing."""

    def __init__(self) -> None:
        self._records: dict[str, Any] = {}

    async def store(self, record: Any) -> str:
        record_id = str(getattr(record, 'memory_id', id(record)))
        self._records[record_id] = record
        return record_id

    async def retrieve(self, record_id: str) -> Any | None:
        return self._records.get(record_id)

    async def search(self, query: str, limit: int = 10) -> list[Any]:
        # Simple string match for development
        results = []
        for record in self._records.values():
            content = str(getattr(record, 'content', ''))
            if query.lower() in content.lower():
                results.append(record)
                if len(results) >= limit:
                    break
        return results
