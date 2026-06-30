"""DecisionCheckpoint + CheckpointManager — decision rollback (spec §5 lines 804-893).

Inspired by Cline shadow-git (concept of recoverable version points + patch)
and OpenHands EventLog View projection (replay events up to checkpoint length).

Unlike Cline (file-system based), operates on structured BrainDecision objects.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from cognitiveplane.control.event_log import EventLog
from cognitiveplane.control.exceptions import CheckpointNotFoundError
from cognitiveplane.shared.dto_decision import BrainDecision
from cognitiveplane.shared.dto.memory import MemoryContent
from cognitiveplane.shared.enums import BrainStateType, MemoryType
from cognitiveplane.shared.ports.memory import MemoryWriteInput, MemoryWritePort
from cognitiveplane.shared.types import CaseId, DecisionId


@dataclass(frozen=True)
class DecisionCheckpoint:
    """Immutable snapshot of decision pipeline state at a point in time.

    Unlike Cline's shadow-git (file-system based), this operates on structured
    decision objects — WeldEvent doesn't have workspace files to roll back.
    """

    checkpoint_id: str
    case_id: CaseId
    state: BrainStateType
    decision: BrainDecision | None
    event_log_length: int
    created_at: datetime


class CheckpointManager:
    """Save/restore decision pipeline state for rollback.

    - save: snapshot (state, decision, event_log_length) under a fresh checkpoint_id
    - restore: fetch by checkpoint_id (raises CheckpointNotFoundError if absent)
    - replay_event_log: rebuild EventLog truncated to checkpoint length
    - apply_patch: structured field-level diff on the checkpointed BrainDecision
    """

    def __init__(self, memory_write: MemoryWritePort | None = None) -> None:
        self._memory = memory_write
        self._checkpoints: dict[str, DecisionCheckpoint] = {}

    async def save(
        self,
        case_id: CaseId,
        state: BrainStateType,
        decision: BrainDecision | None,
        event_log: EventLog,
    ) -> str:
        cp_id = str(uuid4())
        checkpoint = DecisionCheckpoint(
            checkpoint_id=cp_id,
            case_id=case_id,
            state=state,
            decision=decision,
            event_log_length=len(event_log),
            created_at=datetime.now(timezone.utc),
        )
        self._checkpoints[cp_id] = checkpoint

        if self._memory is not None:
            decision_id = (
                decision.decision_id
                if decision is not None
                else DecisionId(value=uuid4())
            )
            await self._memory.write(
                MemoryWriteInput(
                    memory_type=MemoryType.CHECKPOINT,
                    content=MemoryContent(
                        summary=f"checkpoint:{cp_id} state={state.value}",
                        details={
                            "checkpoint_id": cp_id,
                            "state": state.value,
                            "event_log_length": checkpoint.event_log_length,
                        },
                        feature_vector=[0.0],
                    ),
                    source_decision_id=decision_id,
                )
            )
        return cp_id

    async def restore(self, checkpoint_id: str) -> DecisionCheckpoint:
        cp = self._checkpoints.get(checkpoint_id)
        if cp is None:
            raise CheckpointNotFoundError(checkpoint_id)
        return cp

    async def replay_event_log(
        self, event_log: EventLog, checkpoint: DecisionCheckpoint
    ) -> EventLog:
        """Truncated copy of EventLog up to checkpoint length.

        Borrowed from OpenHands View.from_events() projection pattern.
        """
        truncated = EventLog(case_id=checkpoint.case_id)
        for event in event_log._events[: checkpoint.event_log_length]:
            truncated.append(event)
        return truncated

    async def apply_patch(self, checkpoint_id: str, patch: dict[str, Any]) -> BrainDecision:
        """Apply structured diff to a checkpointed decision.

        Field-level structured diff — does not mutate the original.
        Supported patch keys:
        - "confidence": float in [0,1]
        - "outputs": list[DecisionOutput] replacement
        - "parameters": dict[str, str] merged into the first output's ParameterSet
        """
        cp = await self.restore(checkpoint_id)
        if cp.decision is None:
            raise ValueError(f"No decision in checkpoint {checkpoint_id}")
        return self._apply_structured_diff(cp.decision, patch)

    @staticmethod
    def _apply_structured_diff(
        decision: BrainDecision, patch: dict[str, Any]
    ) -> BrainDecision:
        data = decision.model_dump()

        if "confidence" in patch:
            confidence = float(patch["confidence"])
            data["confidence"] = max(0.0, min(1.0, confidence))
            for out in data.get("outputs", []):
                out["confidence"] = data["confidence"]

        if "outputs" in patch:
            data["outputs"] = deepcopy(patch["outputs"])

        if "parameters" in patch:
            new_params = patch["parameters"]
            outputs = data.get("outputs") or []
            for out in outputs:
                content = out.get("content")
                if isinstance(content, dict) and "parameters" in content:
                    inner = content["parameters"]
                    if isinstance(inner, dict) and "parameters" in inner:
                        merged = {**inner["parameters"], **new_params}
                        inner["parameters"] = merged

        return BrainDecision.model_validate(data)
