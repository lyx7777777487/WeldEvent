"""L1 Cognitive Plane — In-memory BrainDecisionRepository.

Suitable for testing and single-process scenarios.  Not thread-safe.
Source: L1_Port_and_Contract_Design.md (Phase 4, Section 6.advice.md).
"""

from cognitiveplane.shared.enums import EventType
from cognitiveplane.shared.dto_decision import BrainDecision
from cognitiveplane.shared.types import CaseId, DecisionId
from cognitiveplane.control.ports import BrainDecisionRepository


class InMemoryBrainDecisionRepository(BrainDecisionRepository):
    """In-memory implementation backed by plain dicts.

    Keyed by decision_id.value for O(advice.md) lookups.  A secondary index maps
    (trigger_event_type, case_id.value) to a list of decision_id.values for
    efficient trigger-event queries.
    """

    def __init__(self) -> None:
        self._store: dict[str, BrainDecision] = {}
        self._trigger_index: dict[tuple[str, str], list[str]] = {}

    # ------------------------------------------------------------------
    # Repository contract
    # ------------------------------------------------------------------

    async def save(self, decision: BrainDecision) -> None:
        key = decision.decision_id.value
        self._store[str(key)] = decision

        # Update the trigger index
        idx_key = (decision.trigger_event_type.value, decision.case_id.value)
        if idx_key not in self._trigger_index:
            self._trigger_index[idx_key] = []
        # Append if not already indexed (idempotent on overwrite)
        if str(key) not in self._trigger_index[idx_key]:
            self._trigger_index[idx_key].append(str(key))

    async def find_by_id(self, decision_id: DecisionId) -> BrainDecision | None:
        return self._store.get(str(decision_id.value))

    async def find_by_trigger_event(
        self, event_type: EventType, case_id: CaseId
    ) -> list[BrainDecision]:
        idx_key = (event_type.value, case_id.value)
        ids = self._trigger_index.get(idx_key, [])
        results: list[BrainDecision] = []
        for did in ids:
            decision = self._store.get(did)
            if decision is not None:
                results.append(decision)
        return results

    async def find_pending_decisions(
        self, max_results: int = 10
    ) -> list[BrainDecision]:
        pending = [
            d for d in self._store.values() if d.published_at is None
        ]
        return pending[:max_results]

    # ------------------------------------------------------------------
    # Test isolation
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Drop all stored decisions and index entries.  For test isolation."""
        self._store.clear()
        self._trigger_index.clear()
