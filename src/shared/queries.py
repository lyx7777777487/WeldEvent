"""L1 Cognitive Plane -- Query schemas.

Gateway Read Queries (Section 5.1), Memory Queries (Section 5.2),
Knowledge Queries (Section 5.3), Validation Queries (Section 5.4),
and Brain Queries (Section 5.5).

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 5).
"""

from datetime import datetime

from pydantic import BaseModel, Field

from src.shared.enums import EventType
from src.shared.types import CaseId, DecisionId, MemoryId
from src.shared.dto_context import ContextSnapshot
from src.shared.dto_knowledge import (
    CaseLibraryQuery,
    EquipmentKnowledgeQuery,
    ProcessKnowledgeQuery,
    RAGQuery,
    RuleQuery,
    StandardsQuery,
)
from src.shared.dto_memory import MemorySearchQuery


# ---------------------------------------------------------------------------
# Section 5.1 -- Gateway Read Queries
# ---------------------------------------------------------------------------


class ReadWorkflowStateQuery(BaseModel):
    case_id: CaseId


class ReadCaseQuery(BaseModel):
    case_id: CaseId


class ReadMeasurementsQuery(BaseModel):
    case_id: CaseId
    parameter_filter: list[str] | None = None
    since: datetime | None = None


class ReadEventsQuery(BaseModel):
    case_id: CaseId
    since: datetime | None = None
    event_type_filter: list[EventType] | None = None


class ReadAuditTrailQuery(BaseModel):
    case_id: CaseId
    since: datetime | None = None


# ---------------------------------------------------------------------------
# Section 5.2 -- Memory Queries
# ---------------------------------------------------------------------------


class SearchMemoryQuery(BaseModel):
    query: MemorySearchQuery


class ReadMemoryQuery(BaseModel):
    memory_id: MemoryId


class ComputeMemoryConfidenceQuery(BaseModel):
    context: ContextSnapshot


class FindMemoryByDecisionQuery(BaseModel):
    source_decision_id: DecisionId


# ---------------------------------------------------------------------------
# Section 5.3 -- Knowledge Queries
# ---------------------------------------------------------------------------


class QueryRAGRequest(BaseModel):
    query: RAGQuery


class QueryRulesRequest(BaseModel):
    query: RuleQuery


class QueryStandardsRequest(BaseModel):
    query: StandardsQuery


class QueryEquipmentKnowledgeRequest(BaseModel):
    query: EquipmentKnowledgeQuery


class QueryProcessKnowledgeRequest(BaseModel):
    query: ProcessKnowledgeQuery


class QueryCaseLibraryRequest(BaseModel):
    query: CaseLibraryQuery


# ---------------------------------------------------------------------------
# Section 5.4 -- Validation Queries
# ---------------------------------------------------------------------------


class FindValidationByDecisionQuery(BaseModel):
    decision_id: DecisionId


class FindRecentCriticalsQuery(BaseModel):
    since: datetime
    max_results: int = Field(default=10, ge=1, le=100)


class GetEscalationStateQuery(BaseModel):
    """No parameters -- singleton state."""

    pass


# ---------------------------------------------------------------------------
# Section 5.5 -- Brain Queries
# ---------------------------------------------------------------------------


class GetBrainStateQuery(BaseModel):
    """No parameters -- singleton state."""

    pass


class FindDecisionByEventQuery(BaseModel):
    event_type: EventType
    case_id: CaseId


class FindPendingDecisionsQuery(BaseModel):
    max_results: int = Field(default=10, ge=1, le=100)
