"""SQLAlchemy ORM models — Phase 1 schema declarations.

Six tables map to the six aggregate roots that need durable storage in the
Cognitive Plane:

| Table              | Aggregate                             |
|--------------------|---------------------------------------|
| brain_decisions    | BrainDecision                         |
| memory_records     | MemoryRecord                          |
| knowledge_entries  | Knowledge documents (RAG / standards) |
| workflow_states    | Read-only mirror of WeldMap workflow  |
| human_reviews      | HumanReviewRequest                    |
| audit_entries      | AuditEntry (L5 audit memory)          |

Phase 1: declared as plain dataclasses with the column names.
Phase 2: replace with SQLAlchemy 2.x DeclarativeBase + Mapped types and
generate alembic migrations from the resulting metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass
class BrainDecisionRow:
    decision_id: UUID
    case_id: str
    persona: str
    reasoning_mode: str
    decision_point: str
    state: str
    trigger_event_type: str
    confidence: float
    outputs: list[dict[str, Any]] = field(default_factory=list)  # JSONB
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class MemoryRecordRow:
    memory_id: UUID
    memory_type: str
    source_decision_id: UUID
    promotion_status: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    feature_vector: list[float] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    promoted_at: datetime | None = None
    archived_at: datetime | None = None


@dataclass
class KnowledgeEntryRow:
    knowledge_id: UUID
    knowledge_type: str
    content: str
    source_reference: str
    embedding: list[float] = field(default_factory=list)  # pgvector / Milvus
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class WorkflowStateRow:
    case_id: str
    status: str
    current_activity: str | None
    parameters: dict[str, Any]
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class HumanReviewRow:
    request_id: UUID
    decision_id: UUID
    collaboration_layer: str
    review_type: str
    status: str
    content: dict[str, Any]
    reviewer: str | None = None
    resolution: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = None


@dataclass
class AuditEntryRow:
    entry_id: str
    case_id: str
    action: str
    actor: str
    timestamp: datetime
    details: dict[str, Any] = field(default_factory=dict)


ALL_TABLES = (
    "brain_decisions",
    "memory_records",
    "knowledge_entries",
    "workflow_states",
    "human_reviews",
    "audit_entries",
)
