"""Governance plane — port ABCs (spec §4 lines 546-548).

Canonical Phase 1 surface. Existing governance code currently imports
from `shared.ports.validation` and `shared.ports.collaboration`; this
module re-exports the same contracts under their final home so new
callers can adopt the spec layout without waiting on the legacy delete.

ValidationPipelinePort lives in shared/ports/validation.py — the Pipeline
implementation moved to gateway/pipeline.py per spec v5.1 §9.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from cognitiveplane.shared.ports.collaboration import HumanReviewRequestRepository
from cognitiveplane.shared.ports.validation import (
    ValidationResultRepository,
)


class ApprovalServicePort(ABC):
    """Approval routing — synchronous for CRITICAL/URGENT, async for ROUTINE."""

    @abstractmethod
    async def request_approval(self, request: Any) -> Any: ...


__all__ = [
    "ApprovalServicePort",
    "HumanReviewRequestRepository",
    "ValidationResultRepository",
]
