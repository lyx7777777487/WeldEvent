from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any


class EventPort(ABC):
    @abstractmethod
    async def publish(self, event: dict[str, Any]) -> None: ...

    @abstractmethod
    async def subscribe(self, event_type: str) -> AsyncIterator[dict[str, Any]]: ...
