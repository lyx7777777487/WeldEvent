"""Interaction modes -- plugin-based interaction patterns.

Importing this module triggers auto-registration of all built-in modes
via the global mode_registry.
"""

from src.interaction.modes.knowledge_query import KnowledgeQueryMode
from src.interaction.registry import mode_registry

if not mode_registry.get("cognitive.knowledge_query"):
    mode_registry.register(KnowledgeQueryMode)
