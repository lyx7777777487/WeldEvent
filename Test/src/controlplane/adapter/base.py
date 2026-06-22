from abc import ABC, abstractmethod

from controlplane.domain.activity import ActivityInput, ActivityOutput


class ActivityAdapter(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def version(self) -> str: ...

    @abstractmethod
    async def execute(self, input: ActivityInput) -> ActivityOutput: ...
