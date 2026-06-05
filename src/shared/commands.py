"""L1 Cognitive Plane -- Command schemas.

Gateway Write Commands (Section 4.2), Memory Commands (Section 4.3),
and Validation Commands (Section 4.4).

Brain Commands (Section 4.1) are in ``src.brain.commands`` -- the
``PublishDecisionCommand`` there includes ``validation_result``, while
the Gateway version here takes only ``decision: BrainDecision``.

Source: L1_Port_and_Contract_Design.md (Phase 4, Sections 4.2--4.4).
"""

from pydantic import BaseModel, Field

from src.shared.enums import MemoryType, ReasoningMode
from src.shared.types import DecisionId, MemoryId
from src.shared.dto_context import ContextSnapshot
from src.shared.dto_decision import BrainDecision
from src.shared.dto_gateway import (
    ConsensusRequest,
    Escalation,
    Explanation,
    ParameterPatch,
    PromotionRequest,
    RecheckRequest,
    RiskAlert,
)
from src.shared.dto_collaboration import FeedbackContent
from src.shared.dto_memory import MemoryContent


# ---------------------------------------------------------------------------
# Section 4.2 -- Gateway Write Commands
# ---------------------------------------------------------------------------


class PublishDecisionCommand(BaseModel):
    decision: BrainDecision


class PublishExplanationCommand(BaseModel):
    explanation: Explanation


class PublishParameterPatchCommand(BaseModel):
    patch: ParameterPatch


class PublishRecheckCommand(BaseModel):
    request: RecheckRequest


class PublishConsensusCommand(BaseModel):
    request: ConsensusRequest


class PublishRiskAlertCommand(BaseModel):
    alert: RiskAlert


class PublishEscalationCommand(BaseModel):
    escalation: Escalation


class PublishFeedbackCommand(BaseModel):
    feedback: FeedbackContent


class PublishMemoryPromotionCommand(BaseModel):
    request: PromotionRequest


# ---------------------------------------------------------------------------
# Section 4.3 -- Memory Commands
# ---------------------------------------------------------------------------


class StoreMemoryCommand(BaseModel):
    memory_type: MemoryType
    content: MemoryContent
    source_decision_id: DecisionId


class RequestPromotionCommand(BaseModel):
    memory_id: MemoryId
    promotion_rationale: str = Field(min_length=1)


class ArchiveMemoryCommand(BaseModel):
    memory_id: MemoryId
    reason: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Section 4.4 -- Validation Commands
# ---------------------------------------------------------------------------


class ValidateDecisionCommand(BaseModel):
    context: ContextSnapshot
    decision: BrainDecision
    reasoning_mode: ReasoningMode


class ResetEscalationCounterCommand(BaseModel):
    reason: str = Field(min_length=1)
    requested_by: str
