"""Tests for InMemoryBrainDecisionRepository.

Covers save, find_by_id, find_by_trigger_event, find_pending_decisions,
and clear for test isolation.
"""

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from cognitiveplane.shared.enums import (
    AggregatedValidationResult,
    BrainStateType,
    DecisionPointType,
    EventType,
    PersonaType,
    ReasoningMode,
)
from cognitiveplane.shared.dto_decision import BrainDecision
from cognitiveplane.shared.types import CaseId, DecisionId


# ---------------------------------------------------------------------------
# Helper: create a minimal BrainDecision for testing
# ---------------------------------------------------------------------------

def _make_decision(
    decision_id: DecisionId | None = None,
    case_id: CaseId | None = None,
    trigger_event_type: EventType = EventType.WORKFLOW_ENTERED,
    state: BrainStateType = BrainStateType.VALIDATION,
    published_at: datetime | None = None,
) -> BrainDecision:
    return BrainDecision(
        decision_id=decision_id or DecisionId(value=uuid4()),
        case_id=case_id or CaseId(value="case-001"),
        trigger_event_type=trigger_event_type,
        decision_point=DecisionPointType.DP1,
        persona=PersonaType.PLANNER,
        reasoning_mode=ReasoningMode.ROUTINE,
        state=state,
        outputs=[],
        confidence=0.9,
        created_at=datetime.now(timezone.utc),
        published_at=published_at,
    )


def _repo():
    from cognitiveplane.control.repositories.in_memory import InMemoryBrainDecisionRepository
    return InMemoryBrainDecisionRepository()


# ===================================================================
# save + find_by_id
# ===================================================================


class TestSaveAndFindById:

    @pytest.mark.asyncio
    async def test_save_stores_decision(self):
        repo = _repo()
        decision = _make_decision()
        await repo.save(decision)
        # If no exception, save succeeded

    @pytest.mark.asyncio
    async def test_find_by_id_returns_stored_decision(self):
        repo = _repo()
        decision_id = DecisionId(value=uuid4())
        decision = _make_decision(decision_id=decision_id)
        await repo.save(decision)

        result = await repo.find_by_id(decision_id)
        assert result is not None
        assert result.decision_id == decision_id

    @pytest.mark.asyncio
    async def test_find_by_id_returns_none_for_unknown(self):
        repo = _repo()
        result = await repo.find_by_id(DecisionId(value=uuid4()))
        assert result is None

    @pytest.mark.asyncio
    async def test_save_overwrites_existing(self):
        repo = _repo()
        decision_id = DecisionId(value=uuid4())
        d1 = _make_decision(decision_id=decision_id, state=BrainStateType.VALIDATION)
        await repo.save(d1)

        d2 = _make_decision(decision_id=decision_id, state=BrainStateType.PUBLICATION)
        await repo.save(d2)

        result = await repo.find_by_id(decision_id)
        assert result is not None
        assert result.state == BrainStateType.PUBLICATION


# ===================================================================
# find_by_trigger_event
# ===================================================================


class TestFindByTriggerEvent:

    @pytest.mark.asyncio
    async def test_find_by_trigger_event_returns_indexed(self):
        repo = _repo()
        case_id = CaseId(value="case-abc")
        event_type = EventType.IQA_COMPLETED

        d1 = _make_decision(case_id=case_id, trigger_event_type=event_type)
        d2 = _make_decision(case_id=case_id, trigger_event_type=event_type)
        d3 = _make_decision(trigger_event_type=EventType.PPA_COMPLETED)
        await repo.save(d1)
        await repo.save(d2)
        await repo.save(d3)

        results = await repo.find_by_trigger_event(event_type, case_id)
        assert len(results) == 2
        ids = {r.decision_id.value for r in results}
        assert d1.decision_id.value in ids
        assert d2.decision_id.value in ids
        assert d3.decision_id.value not in ids

    @pytest.mark.asyncio
    async def test_find_by_trigger_event_empty_for_no_match(self):
        repo = _repo()
        results = await repo.find_by_trigger_event(
            EventType.MEA_COMPLETED, CaseId(value="nonexistent")
        )
        assert results == []


# ===================================================================
# find_pending_decisions
# ===================================================================


class TestFindPendingDecisions:

    @pytest.mark.asyncio
    async def test_find_pending_returns_without_published_at(self):
        repo = _repo()
        d_pending = _make_decision(published_at=None)
        d_published = _make_decision(
            published_at=datetime.now(timezone.utc)
        )
        await repo.save(d_pending)
        await repo.save(d_published)

        results = await repo.find_pending_decisions()
        assert len(results) == 1
        assert results[0].decision_id == d_pending.decision_id

    @pytest.mark.asyncio
    async def test_find_pending_respects_max_results(self):
        repo = _repo()
        for _ in range(5):
            await repo.save(_make_decision(published_at=None))

        results = await repo.find_pending_decisions(max_results=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_find_pending_empty_when_none(self):
        repo = _repo()
        await repo.save(_make_decision(published_at=datetime.now(timezone.utc)))
        results = await repo.find_pending_decisions()
        assert results == []


# ===================================================================
# clear (test isolation)
# ===================================================================


class TestClear:

    @pytest.mark.asyncio
    async def test_clear_empties_repository(self):
        repo = _repo()
        await repo.save(_make_decision())
        await repo.save(_make_decision())
        repo.clear()

        result = await repo.find_by_id(DecisionId(value=uuid4()))
        assert result is None

    @pytest.mark.asyncio
    async def test_clear_resets_trigger_index(self):
        repo = _repo()
        case_id = CaseId(value="case-clear")
        event_type = EventType.WORKFLOW_ENTERED
        await repo.save(_make_decision(case_id=case_id, trigger_event_type=event_type))
        repo.clear()

        results = await repo.find_by_trigger_event(event_type, case_id)
        assert results == []
