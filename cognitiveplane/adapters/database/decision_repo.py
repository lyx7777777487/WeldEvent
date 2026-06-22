"""BrainDecision Postgres repo — Phase 1 stub.

Wraps an in-memory dict for now; Phase 2 replaces with real DB calls using
`AsyncDatabaseEngine`. The interface is `BrainDecisionRepository`.
"""

from __future__ import annotations

from cognitiveplane.adapters.database.engine import AsyncDatabaseEngine
from cognitiveplane.control.ports import BrainDecisionRepository
from cognitiveplane.shared.dto_decision import BrainDecision
from cognitiveplane.shared.enums import EventType
from cognitiveplane.shared.types import CaseId, DecisionId


class PostgresBrainDecisionRepository(BrainDecisionRepository):
    def __init__(self, engine: AsyncDatabaseEngine) -> None:
        self._engine = engine
        self._cache: dict[str, BrainDecision] = {}

    async def save(self, decision: BrainDecision) -> None:
        async with self._engine.session():
            self._cache[str(decision.decision_id.value)] = decision

    async def find_by_id(self, decision_id: DecisionId) -> BrainDecision | None:
        return self._cache.get(str(decision_id.value))

    async def find_by_trigger_event(
        self, event_type: EventType, case_id: CaseId
    ) -> list[BrainDecision]:
        return [
            d
            for d in self._cache.values()
            if d.trigger_event_type == event_type and d.case_id == case_id
        ]

    async def find_pending_decisions(
        self, max_results: int = 10
    ) -> list[BrainDecision]:
        return list(self._cache.values())[:max_results]
