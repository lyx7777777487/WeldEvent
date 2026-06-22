"""Governance plane — port ABCs (spec §4 lines 546-548).

Canonical Phase 1 surface. Existing governance code currently imports
from `shared.ports.validation` and `shared.ports.collaboration`; this
module re-exports the same contracts under their final home so new
callers can adopt the spec layout without waiting on the legacy delete.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from cognitiveplane.shared.ports.collaboration import HumanReviewRequestRepository
from cognitiveplane.shared.ports.validation import (
    ValidationResultRepository,
)


class ValidationPipelinePort(ABC):
    """Pure-computation validation orchestrator (spec §9 lines 1555-1562).

    Pre-fetched memory and gateway context are passed in; the pipeline
    must NOT perform side-effectful retrieval.
    """

    @abstractmethod
    async def validate(
        self,
        decision: Any,
        context: Any,
        memory_context: Any | None = None,
        gateway_context: Any | None = None,
    ) -> Any:
        ...


class ApprovalServicePort(ABC):
    """Approval routing — synchronous for CRITICAL/URGENT, async for ROUTINE."""

    @abstractmethod
    async def request_approval(self, request: Any) -> Any: ...


__all__ = [
    "ApprovalServicePort",
    "HumanReviewRequestRepository",
    "ValidationPipelinePort",
    "ValidationResultRepository",
]
