"""Cross-Plane shared DTO package (spec §骨架 lines 1011-1018).

Subpackage mandated by the 7-plane redesign. The context, knowledge,
memory, and validation submodules are the canonical implementation
locations. The legacy flat ``shared/dto_*.py`` files are backward-compat
shims that re-export from here. Other submodules (decision, escalation,
etc.) still re-export from their legacy locations and will be migrated
later.
"""

from cognitiveplane.shared.dto import (
    collaboration,
    context,
    decision,
    deepagents,
    escalation,
    gateway,
    knowledge,
    learning,
    memory,
    persona,
    reasoning_mode,
    validation,
    weldmap_events,
)

__all__ = [
    "collaboration",
    "context",
    "decision",
    "deepagents",
    "escalation",
    "gateway",
    "knowledge",
    "learning",
    "memory",
    "persona",
    "reasoning_mode",
    "validation",
    "weldmap_events",
]
