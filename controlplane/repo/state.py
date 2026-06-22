from abc import ABC, abstractmethod
from typing import Any


class StateRepository(ABC):
    @abstractmethod
    async def get(self, key: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def put(self, key: str, value: dict[str, Any]) -> None: ...

    @abstractmethod
    async def query(self, criteria: dict[str, Any]) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...
