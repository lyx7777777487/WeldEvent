from abc import ABC, abstractmethod
from typing import Any


class DataPort(ABC):
    @abstractmethod
    async def read(self, key: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def write(self, key: str, value: dict[str, Any]) -> None: ...
