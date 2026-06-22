from abc import ABC, abstractmethod
from typing import Any


class CommandPort(ABC):
    @abstractmethod
    async def send(self, command: dict[str, Any]) -> dict[str, Any]: ...
