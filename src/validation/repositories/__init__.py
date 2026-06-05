"""In-memory implementation of ValidationResultRepository."""

from datetime import datetime

from src.shared.dto_validation import ValidationResult
from src.shared.enums import AggregatedValidationResult
from src.shared.ports.validation import ValidationResultRepository
from src.shared.types import DecisionId


class InMemoryValidationResultRepository(ValidationResultRepository):
    """In-memory repository for ValidationResult records.

    Suitable for unit tests and local development.  Not thread-safe.
    """

    def __init__(self) -> None:
        self._store: dict[str, ValidationResult] = {}
        self._decision_index: dict[str, list[str]] = {}

    async def save(self, result: ValidationResult) -> None:
        key = str(result.validation_id.value)
        self._store[key] = result

        dec_key = str(result.decision_id.value)
        self._decision_index.setdefault(dec_key, []).append(key)

    async def find_by_decision_id(
        self, decision_id: DecisionId
    ) -> list[ValidationResult]:
        dec_key = str(decision_id.value)
        keys = self._decision_index.get(dec_key, [])
        return [self._store[k] for k in keys if k in self._store]

    async def find_recent_criticals(
        self, since: datetime, max_results: int = 10
    ) -> list[ValidationResult]:
        criticals = [
            r
            for r in self._store.values()
            if r.aggregated_result == AggregatedValidationResult.ESCALATED
            and r.timestamp >= since
        ]
        criticals.sort(key=lambda r: r.timestamp, reverse=True)
        return criticals[:max_results]

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Reset all internal storage. Required for test isolation."""
        self._store.clear()
        self._decision_index.clear()
