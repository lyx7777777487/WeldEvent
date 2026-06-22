from abc import ABC, abstractmethod
from typing import Any


class AuditRepository(ABC):
    @abstractmethod
    async def record(self, entry: dict[str, Any]) -> None: ...

    @abstractmethod
    async def query(self, criteria: dict[str, Any]) -> list[dict[str, Any]]: ...
