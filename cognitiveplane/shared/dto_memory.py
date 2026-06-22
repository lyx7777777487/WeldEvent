"""Memory DTOs — backward-compat shim.

Canonical home is now ``cognitiveplane.shared.dto.memory``.
This module re-exports every public name so existing callers
(``from cognitiveplane.shared.dto_memory import ...``) keep working.
"""

from cognitiveplane.shared.dto.memory import (  # noqa: F401
    MemoryContent,
    MemoryRecord,
    MemorySearchQuery,
    MemorySearchResult,
)

__all__ = [
    "MemoryContent",
    "MemoryRecord",
    "MemorySearchQuery",
    "MemorySearchResult",
]
