"""L1 Cognitive Plane — Memory port ABCs, Input/Output models, and Repository.

Source: L1_Port_and_Contract_Design.md (Phase 4, Sections 6.3, 8.advice.md–8.6).
"""

from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import BaseModel, Field

from cognitiveplane.shared.dto.context import ContextSnapshot
from cognitiveplane.shared.dto.memory import (
    MemoryContent,
    MemoryRecord,
    MemorySearchQuery,
    MemorySearchResult,
)
from cognitiveplane.shared.enums import MemoryType, PromotionStatus
from cognitiveplane.shared.types import DecisionId, MemoryId


# ---------------------------------------------------------------------------
# 6.3 MemoryRepository
# ---------------------------------------------------------------------------


class MemoryRepository(ABC):
    """Repository for persisting and querying MemoryRecord aggregates."""

    @abstractmethod
    async def store(self, record: MemoryRecord) -> MemoryId: ...

    @abstractmethod
    async def search(
        self, query: MemorySearchQuery
    ) -> list[MemorySearchResult]: ...

    @abstractmethod
    async def find_by_id(self, memory_id: MemoryId) -> MemoryRecord | None: ...

    @abstractmethod
    async def find_by_source_decision(
        self, decision_id: DecisionId
    ) -> list[MemoryRecord]: ...

    @abstractmethod
    async def update_promotion_status(
        self, memory_id: MemoryId, status: PromotionStatus
    ) -> None: ...

    @abstractmethod
    async def archive(
        self, memory_id: MemoryId, reason: str
    ) -> None: ...


# ---------------------------------------------------------------------------
# 8.advice.md MemoryWritePort
# ---------------------------------------------------------------------------


class MemoryWriteInput(BaseModel):
    memory_type: MemoryType
    content: MemoryContent
    source_decision_id: DecisionId


class MemoryWriteOutput(BaseModel):
    memory_id: MemoryId


class MemoryWritePort(ABC):

    @abstractmethod
    async def write(self, input_data: MemoryWriteInput) -> MemoryWriteOutput: ...


# ---------------------------------------------------------------------------
# 8.2 MemoryReadPort
# ---------------------------------------------------------------------------


class MemoryReadInput(BaseModel):
    memory_id: MemoryId


class MemoryReadOutput(BaseModel):
    record: MemoryRecord | None


class MemoryReadPort(ABC):

    @abstractmethod
    async def read(self, input_data: MemoryReadInput) -> MemoryReadOutput: ...


# ---------------------------------------------------------------------------
# 8.3 MemorySearchPort
# ---------------------------------------------------------------------------


class MemorySearchInput(BaseModel):
    query: MemorySearchQuery


class MemorySearchOutput(BaseModel):
    results: list[MemorySearchResult]


class MemorySearchPort(ABC):

    @abstractmethod
    async def search(self, input_data: MemorySearchInput) -> MemorySearchOutput: ...


# ---------------------------------------------------------------------------
# 8.4 MemoryPromotionPort
# ---------------------------------------------------------------------------


class MemoryPromotionInput(BaseModel):
    memory_id: MemoryId
    promotion_rationale: str = Field(min_length=1)


class MemoryPromotionOutput(BaseModel):
    memory_id: MemoryId
    new_status: PromotionStatus


class MemoryPromotionPort(ABC):

    @abstractmethod
    async def request_promotion(
        self, input_data: MemoryPromotionInput
    ) -> MemoryPromotionOutput: ...


# ---------------------------------------------------------------------------
# 8.5 MemoryArchivePort
# ---------------------------------------------------------------------------


class MemoryArchiveInput(BaseModel):
    memory_id: MemoryId
    reason: str = Field(min_length=1)


class MemoryArchiveOutput(BaseModel):
    memory_id: MemoryId
    archived_at: datetime


class MemoryArchivePort(ABC):

    @abstractmethod
    async def archive(
        self, input_data: MemoryArchiveInput
    ) -> MemoryArchiveOutput: ...


# ---------------------------------------------------------------------------
# 8.6 MemoryConfidencePort
# ---------------------------------------------------------------------------


class MemoryConfidenceInput(BaseModel):
    context: ContextSnapshot


class MemoryConfidenceOutput(BaseModel):
    memory_match_confidence: float = Field(ge=0.0, le=1.0)
    top_match_memory_id: MemoryId | None = None
    top_match_similarity: float = Field(ge=0.0, le=1.0)


class MemoryConfidencePort(ABC):

    @abstractmethod
    async def compute_confidence(
        self, input_data: MemoryConfidenceInput
    ) -> MemoryConfidenceOutput: ...
