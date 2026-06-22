"""Memory plane — port ABCs (spec §4 lines 549-553).

Re-exports the canonical Phase 1 contracts. Existing memory code lives
under `shared.ports.memory` / `shared.ports.learning`; this module is the
final home those imports will be migrated to.
"""

from __future__ import annotations

from cognitiveplane.shared.ports.learning import LearningEventRepository
from cognitiveplane.shared.ports.memory import (
    MemoryReadPort,
    MemorySearchPort,
    MemoryWritePort,
)


# Spec uses the name `LearningEventWriterPort`; the existing repo ABC
# already covers persistence, so we publish it under both names while the
# legacy import path lives on.
LearningEventWriterPort = LearningEventRepository


__all__ = [
    "LearningEventRepository",
    "LearningEventWriterPort",
    "MemoryReadPort",
    "MemorySearchPort",
    "MemoryWritePort",
]
