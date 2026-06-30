"""L1 Cognitive Plane — Knowledge port ABCs, Input/Output models, and Repository.

Source: L1_Port_and_Contract_Design.md (Phase 4, Sections advice.md.advice.md, 6.4).
Knowledge port method signatures follow the pattern established in Sections 7–10.
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from cognitiveplane.shared.dto.knowledge import (
    CaseLibraryQuery,
    CaseLibraryResult,
    EquipmentKnowledgeQuery,
    EquipmentKnowledgeResult,
    KnowledgeResult,
    ProcessKnowledgeQuery,
    ProcessKnowledgeResult,
    RAGQuery,
    RuleQuery,
    RuleResult,
    StandardsQuery,
    StandardsResult,
)
from cognitiveplane.shared.enums import KnowledgeType
from cognitiveplane.shared.types import KnowledgeId


# ---------------------------------------------------------------------------
# 6.4 KnowledgeRepository
# ---------------------------------------------------------------------------


class KnowledgeRepository(ABC):
    """Repository for persisting and querying Knowledge documents."""

    @abstractmethod
    async def rag_query(self, query: RAGQuery) -> list[KnowledgeResult]: ...

    @abstractmethod
    async def find_by_id(
        self, knowledge_id: KnowledgeId
    ) -> dict | None: ...

    @abstractmethod
    async def find_by_type(
        self, knowledge_type: KnowledgeType
    ) -> list[dict]: ...


# ---------------------------------------------------------------------------
# Knowledge port advice.md — RAGQueryPort
# ---------------------------------------------------------------------------


class RAGQueryInput(BaseModel):
    query: RAGQuery


class RAGQueryOutput(BaseModel):
    results: list[KnowledgeResult]


class RAGQueryPort(ABC):

    @abstractmethod
    async def query(self, input_data: RAGQueryInput) -> RAGQueryOutput: ...


# ---------------------------------------------------------------------------
# Knowledge port 2 — RuleQueryPort
# ---------------------------------------------------------------------------


class RuleQueryInput(BaseModel):
    query: RuleQuery


class RuleQueryOutput(BaseModel):
    results: list[RuleResult]


class RuleQueryPort(ABC):

    @abstractmethod
    async def query(self, input_data: RuleQueryInput) -> RuleQueryOutput: ...


# ---------------------------------------------------------------------------
# Knowledge port 3 — StandardsQueryPort
# ---------------------------------------------------------------------------


class StandardsQueryInput(BaseModel):
    query: StandardsQuery


class StandardsQueryOutput(BaseModel):
    results: list[StandardsResult]


class StandardsQueryPort(ABC):

    @abstractmethod
    async def query(self, input_data: StandardsQueryInput) -> StandardsQueryOutput: ...


# ---------------------------------------------------------------------------
# Knowledge port 4 — EquipmentKnowledgePort
# ---------------------------------------------------------------------------


class EquipmentKnowledgeInput(BaseModel):
    query: EquipmentKnowledgeQuery


class EquipmentKnowledgeOutput(BaseModel):
    results: list[EquipmentKnowledgeResult]


class EquipmentKnowledgePort(ABC):

    @abstractmethod
    async def query(self, input_data: EquipmentKnowledgeInput) -> EquipmentKnowledgeOutput: ...


# ---------------------------------------------------------------------------
# Knowledge port 5 — ProcessKnowledgePort
# ---------------------------------------------------------------------------


class ProcessKnowledgeInput(BaseModel):
    query: ProcessKnowledgeQuery


class ProcessKnowledgeOutput(BaseModel):
    results: list[ProcessKnowledgeResult]


class ProcessKnowledgePort(ABC):

    @abstractmethod
    async def query(self, input_data: ProcessKnowledgeInput) -> ProcessKnowledgeOutput: ...


# ---------------------------------------------------------------------------
# Knowledge port 6 — CaseLibraryQueryPort
# ---------------------------------------------------------------------------


class CaseLibraryQueryInput(BaseModel):
    query: CaseLibraryQuery


class CaseLibraryQueryOutput(BaseModel):
    results: list[CaseLibraryResult]


class CaseLibraryQueryPort(ABC):

    @abstractmethod
    async def query(self, input_data: CaseLibraryQueryInput) -> CaseLibraryQueryOutput: ...
