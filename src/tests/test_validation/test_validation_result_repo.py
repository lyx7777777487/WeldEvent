"""Tests for InMemoryValidationResultRepository."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.shared.dto_validation import (
    SafetyValidationResult,
    StageResult,
    ValidationResult,
)
from src.shared.enums import (
    AggregatedValidationResult,
    SafetyStatus,
    ValidationStageStatus,
)
from src.shared.types import DecisionId, ValidationId
from src.validation.repositories import InMemoryValidationResultRepository


def _make_result(
    aggregated: AggregatedValidationResult = AggregatedValidationResult.APPROVED,
    decision_id: DecisionId | None = None,
) -> ValidationResult:
    now = datetime.now(timezone.utc)
    return ValidationResult(
        validation_id=ValidationId(value=uuid4()),
        decision_id=decision_id or DecisionId(value=uuid4()),
        safety_result=SafetyValidationResult(
            result=SafetyStatus.PASS,
            checked_rules=["test"],
            timestamp=now,
        ),
        aggregated_result=aggregated,
        stages=[
            StageResult(
                stage="safety",
                status=ValidationStageStatus.COMPLETED,
                duration_ms=1,
            )
        ],
        total_duration_ms=1,
        timestamp=now,
    )


@pytest.fixture
def repo() -> InMemoryValidationResultRepository:
    return InMemoryValidationResultRepository()


# -- 1. save() stores result -------------------------------------------------


async def test_save_stores_result(repo: InMemoryValidationResultRepository) -> None:
    result = _make_result()
    await repo.save(result)

    found = await repo.find_by_decision_id(result.decision_id)
    assert len(found) == 1
    assert found[0].validation_id == result.validation_id


# -- 2. find_by_decision_id() returns results for decision -------------------


async def test_find_by_decision_id_returns_matching_results(
    repo: InMemoryValidationResultRepository,
) -> None:
    decision_id = DecisionId(value=uuid4())
    r1 = _make_result(decision_id=decision_id)
    r2 = _make_result(decision_id=decision_id)
    await repo.save(r1)
    await repo.save(r2)

    found = await repo.find_by_decision_id(decision_id)
    assert len(found) == 2
    found_vids = [r.validation_id.value for r in found]
    assert r1.validation_id.value in found_vids
    assert r2.validation_id.value in found_vids


# -- 3. find_by_decision_id() returns [] for unknown decision ----------------


async def test_find_by_decision_id_returns_empty_for_unknown(
    repo: InMemoryValidationResultRepository,
) -> None:
    found = await repo.find_by_decision_id(DecisionId(value=uuid4()))
    assert found == []


# -- 4. find_recent_criticals() returns ESCALATED results ordered by timestamp


async def test_find_recent_criticals_returns_escalated_ordered(
    repo: InMemoryValidationResultRepository,
) -> None:
    since = datetime(2025, 1, 1, tzinfo=timezone.utc)

    r_escalated = _make_result(aggregated=AggregatedValidationResult.ESCALATED)
    r_approved = _make_result(aggregated=AggregatedValidationResult.APPROVED)

    await repo.save(r_escalated)
    await repo.save(r_approved)

    criticals = await repo.find_recent_criticals(since=since)
    assert len(criticals) == 1
    assert criticals[0].aggregated_result == AggregatedValidationResult.ESCALATED
    assert criticals[0].validation_id == r_escalated.validation_id
