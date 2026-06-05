"""Port adapters that delegate to InMemoryMemoryRepository and MemoryConfidenceService.

Source: L1_Port_and_Contract_Design.md (Phase 4, Sections 8.1--8.6).
"""

from datetime import datetime, timezone
from uuid import uuid4

from src.shared.enums import PromotionStatus
from src.shared.ports.memory import (
    MemoryArchiveInput,
    MemoryArchiveOutput,
    MemoryArchivePort,
    MemoryConfidenceInput,
    MemoryConfidenceOutput,
    MemoryConfidencePort,
    MemoryPromotionInput,
    MemoryPromotionOutput,
    MemoryPromotionPort,
    MemoryReadInput,
    MemoryReadOutput,
    MemoryReadPort,
    MemorySearchInput,
    MemorySearchOutput,
    MemorySearchPort,
    MemoryWriteInput,
    MemoryWriteOutput,
    MemoryWritePort,
)
from src.shared.types import MemoryId
from src.memory.repositories.in_memory import InMemoryMemoryRepository
from src.memory.services.confidence import MemoryConfidenceService


class MemoryWriteAdapter(MemoryWritePort):
    """Delegates write operations to InMemoryMemoryRepository.store()."""

    def __init__(self, repo: InMemoryMemoryRepository) -> None:
        self._repo = repo

    async def write(self, input_data: MemoryWriteInput) -> MemoryWriteOutput:
        from src.shared.dto_memory import MemoryRecord

        now = datetime.now(timezone.utc)
        memory_id = MemoryId(value=uuid4())
        record = MemoryRecord(
            memory_id=memory_id,
            memory_type=input_data.memory_type,
            content=input_data.content,
            source_decision_id=input_data.source_decision_id,
            promotion_status=PromotionStatus.RAW,
            created_at=now,
        )
        returned_id = await self._repo.store(record)
        return MemoryWriteOutput(memory_id=returned_id)


class MemoryReadAdapter(MemoryReadPort):
    """Delegates read operations to InMemoryMemoryRepository.find_by_id()."""

    def __init__(self, repo: InMemoryMemoryRepository) -> None:
        self._repo = repo

    async def read(self, input_data: MemoryReadInput) -> MemoryReadOutput:
        record = await self._repo.find_by_id(input_data.memory_id)
        return MemoryReadOutput(record=record)


class MemorySearchAdapter(MemorySearchPort):
    """Delegates search operations to InMemoryMemoryRepository.search()."""

    def __init__(self, repo: InMemoryMemoryRepository) -> None:
        self._repo = repo

    async def search(self, input_data: MemorySearchInput) -> MemorySearchOutput:
        results = await self._repo.search(input_data.query)
        return MemorySearchOutput(results=results)


class MemoryPromotionAdapter(MemoryPromotionPort):
    """Delegates promotion operations to InMemoryMemoryRepository.update_promotion_status()."""

    def __init__(self, repo: InMemoryMemoryRepository) -> None:
        self._repo = repo

    async def request_promotion(
        self, input_data: MemoryPromotionInput
    ) -> MemoryPromotionOutput:
        new_status = PromotionStatus.PROMOTION_PENDING
        await self._repo.update_promotion_status(input_data.memory_id, new_status)
        return MemoryPromotionOutput(
            memory_id=input_data.memory_id,
            new_status=new_status,
        )


class MemoryArchiveAdapter(MemoryArchivePort):
    """Delegates archive operations to InMemoryMemoryRepository.archive()."""

    def __init__(self, repo: InMemoryMemoryRepository) -> None:
        self._repo = repo

    async def archive(self, input_data: MemoryArchiveInput) -> MemoryArchiveOutput:
        await self._repo.archive(input_data.memory_id, input_data.reason)
        # Retrieve the record to get archived_at timestamp
        record = await self._repo.find_by_id(input_data.memory_id)
        archived_at = record.archived_at if record else datetime.now(timezone.utc)
        return MemoryArchiveOutput(
            memory_id=input_data.memory_id,
            archived_at=archived_at,
        )


class MemoryConfidenceAdapter(MemoryConfidencePort):
    """Delegates confidence computation to MemoryConfidenceService.compute()."""

    def __init__(self, confidence_service: MemoryConfidenceService) -> None:
        self._confidence_service = confidence_service

    async def compute_confidence(
        self, input_data: MemoryConfidenceInput
    ) -> MemoryConfidenceOutput:
        confidence = await self._confidence_service.compute(input_data.context)
        return MemoryConfidenceOutput(
            memory_match_confidence=confidence,
            top_match_memory_id=None,
            top_match_similarity=0.0,
        )
