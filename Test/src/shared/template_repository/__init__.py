"""shared.template_repository — WorkflowTemplate 仓库（worker 和 L1 共享）。"""

from .file_repository import FileTemplateRepository, TemplateRepository

__all__ = ["FileTemplateRepository", "TemplateRepository"]