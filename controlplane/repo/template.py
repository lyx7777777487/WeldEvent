from abc import ABC, abstractmethod

from controlplane.domain.template import WorkflowTemplate


class TemplateRepository(ABC):
    @abstractmethod
    async def save(self, template: WorkflowTemplate) -> None: ...

    @abstractmethod
    async def load(self, template_id: str, version: str) -> WorkflowTemplate | None: ...

    @abstractmethod
    async def list_versions(self, template_id: str) -> list[str]: ...
