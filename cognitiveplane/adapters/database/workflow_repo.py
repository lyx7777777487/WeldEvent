"""Workflow state read-only mirror — Phase 1 stub.

The Cognitive Plane never *writes* workflow state directly; it only reads
the WeldMap mirror. Phase 1 keeps the latest snapshot in memory; Phase 2
backs the same interface with a PG read-replica fed from WeldMap events.
"""

from __future__ import annotations

from cognitiveplane.adapters.database.engine import AsyncDatabaseEngine
from cognitiveplane.shared.dto.gateway import WorkflowState
from cognitiveplane.shared.types import CaseId


class PostgresWorkflowStateRepository:
    def __init__(self, engine: AsyncDatabaseEngine) -> None:
        self._engine = engine
        self._states: dict[str, WorkflowState] = {}

    async def upsert(self, state: WorkflowState) -> None:
        self._states[state.case_id.value] = state

    async def get(self, case_id: CaseId) -> WorkflowState | None:
        return self._states.get(case_id.value)
