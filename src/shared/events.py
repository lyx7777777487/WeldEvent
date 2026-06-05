"""L1 Cognitive Plane -- Event schemas.

Re-exports domain events and brain-internal events from dto_context for
convenient access via ``src.shared.events``.

Source: L1_Port_and_Contract_Design.md (Phase 4, Sections 3.1--3.3).
"""

from src.shared.dto_context import (  # noqa: F401
    BrainInternalEvent,
    BrainInternalEventType,
    DomainEvent,
    IQACompletedPayload,
    MEACompletedPayload,
    MemoryPromotionRequestedPayload,
    PPACompletedPayload,
    RDAVDACompletedPayload,
    HumanFeedbackReceivedPayload,
    ValidationCriticalPayload,
    WorkflowEnteredPayload,
)

__all__ = [
    "DomainEvent",
    "WorkflowEnteredPayload",
    "IQACompletedPayload",
    "PPACompletedPayload",
    "MEACompletedPayload",
    "RDAVDACompletedPayload",
    "HumanFeedbackReceivedPayload",
    "MemoryPromotionRequestedPayload",
    "ValidationCriticalPayload",
    "BrainInternalEventType",
    "BrainInternalEvent",
]
