"""Tests for control/checkpoint.py."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from cognitiveplane.control.checkpoint import CheckpointManager, DecisionCheckpoint
from cognitiveplane.control.event_log import BrainEventType, EventLog
from cognitiveplane.control.exceptions import CheckpointNotFoundError
from cognitiveplane.shared.dto_decision import BrainDecision, DecisionOutput
from cognitiveplane.shared.dto_decision.outputs import (
    ParameterRecommendation,
    ParameterSet,
)
from cognitiveplane.shared.enums import (
    BrainStateType,
    DecisionPointType,
    EventType,
    PersonaType,
    ReasoningMode,
)
from cognitiveplane.shared.types import CaseId, DecisionId


def _make_decision(case_id: CaseId, confidence: float = 0.8) -> BrainDecision:
    return BrainDecision(
        decision_id=DecisionId(value=uuid4()),
        case_id=case_id,
        trigger_event_type=EventType.WORKFLOW_ENTERED,
        decision_point=DecisionPointType.DP0,
        persona=PersonaType.COPILOT,
        reasoning_mode=ReasoningMode.ROUTINE,
        state=BrainStateType.VALIDATION,
        outputs=[
            DecisionOutput(
                content=ParameterRecommendation(
                    parameters=ParameterSet(parameters={"current": "120A"}),
                    rationale="baseline",
                    confidence=confidence,
                    constraints_applied=[],
                ),
                confidence=confidence,
            )
        ],
        confidence=confidence,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_save_returns_uuid():
    case_id = CaseId(value="case-1")
    log = EventLog(case_id=case_id)
    log.emit(BrainEventType.STATE_TRANSITION, "orchestrator", {})
    mgr = CheckpointManager()
    cp_id = await mgr.save(case_id, BrainStateType.OBSERVING, None, log)
    assert isinstance(cp_id, str) and len(cp_id) > 10


@pytest.mark.asyncio
async def test_restore_returns_checkpoint():
    case_id = CaseId(value="case-1")
    log = EventLog(case_id=case_id)
    log.emit(BrainEventType.STATE_TRANSITION, "orchestrator", {})
    mgr = CheckpointManager()
    decision = _make_decision(case_id)
    cp_id = await mgr.save(case_id, BrainStateType.VALIDATION, decision, log)

    cp = await mgr.restore(cp_id)
    assert isinstance(cp, DecisionCheckpoint)
    assert cp.checkpoint_id == cp_id
    assert cp.state == BrainStateType.VALIDATION
    assert cp.decision is decision
    assert cp.event_log_length == 1


@pytest.mark.asyncio
async def test_restore_missing_raises():
    mgr = CheckpointManager()
    with pytest.raises(CheckpointNotFoundError):
        await mgr.restore("nope")


@pytest.mark.asyncio
async def test_replay_truncates_to_checkpoint_length():
    case_id = CaseId(value="case-1")
    log = EventLog(case_id=case_id)
    log.emit(BrainEventType.STATE_TRANSITION, "orchestrator", {"i": 1})
    log.emit(BrainEventType.STATE_TRANSITION, "orchestrator", {"i": 2})

    mgr = CheckpointManager()
    cp_id = await mgr.save(case_id, BrainStateType.OBSERVING, None, log)

    # Append more events after checkpoint
    log.emit(BrainEventType.VALIDATION, "governance", {"i": 3})
    cp = await mgr.restore(cp_id)
    truncated = await mgr.replay_event_log(log, cp)

    assert len(truncated) == 2


@pytest.mark.asyncio
async def test_apply_patch_confidence():
    case_id = CaseId(value="case-1")
    log = EventLog(case_id=case_id)
    decision = _make_decision(case_id, confidence=0.5)
    mgr = CheckpointManager()
    cp_id = await mgr.save(case_id, BrainStateType.VALIDATION, decision, log)

    patched = await mgr.apply_patch(cp_id, {"confidence": 0.9})
    assert patched.confidence == 0.9
    assert patched.outputs[0].confidence == 0.9
    # Original unchanged
    assert decision.confidence == 0.5


@pytest.mark.asyncio
async def test_apply_patch_confidence_clamped():
    case_id = CaseId(value="case-1")
    log = EventLog(case_id=case_id)
    decision = _make_decision(case_id, confidence=0.5)
    mgr = CheckpointManager()
    cp_id = await mgr.save(case_id, BrainStateType.VALIDATION, decision, log)

    patched = await mgr.apply_patch(cp_id, {"confidence": 1.5})
    assert patched.confidence == 1.0


@pytest.mark.asyncio
async def test_apply_patch_parameters_merged():
    case_id = CaseId(value="case-1")
    log = EventLog(case_id=case_id)
    decision = _make_decision(case_id)
    mgr = CheckpointManager()
    cp_id = await mgr.save(case_id, BrainStateType.VALIDATION, decision, log)

    patched = await mgr.apply_patch(cp_id, {"parameters": {"voltage": "24V"}})
    params = patched.outputs[0].content.parameters.parameters
    assert params.get("voltage") == "24V"
    assert params.get("current") == "120A"  # original preserved


@pytest.mark.asyncio
async def test_apply_patch_no_decision_raises():
    case_id = CaseId(value="case-1")
    log = EventLog(case_id=case_id)
    mgr = CheckpointManager()
    cp_id = await mgr.save(case_id, BrainStateType.OBSERVING, None, log)
    with pytest.raises(ValueError, match="No decision"):
        await mgr.apply_patch(cp_id, {"confidence": 0.9})
