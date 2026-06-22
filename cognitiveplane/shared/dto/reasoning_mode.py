"""L1 Cognitive Plane -- Reasoning Mode Selection DTOs.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 2.13).
"""

from pydantic import BaseModel, Field

from cognitiveplane.shared.enums import NoveltyLevel, ReasoningMode


class ReasoningModeSelectionInput(BaseModel):
    memory_match_confidence: float = Field(ge=0.0, le=1.0)
    knowledge_coverage: float = Field(ge=0.0, le=1.0)
    event_novelty: NoveltyLevel
    validation_critical_count: int = Field(ge=0)


class ReasoningModeSelectionResult(BaseModel):
    selected_mode: ReasoningMode
    selection_rationale: str
    input_signals: ReasoningModeSelectionInput


__all__ = [
    "ReasoningModeSelectionInput",
    "ReasoningModeSelectionResult",
]
