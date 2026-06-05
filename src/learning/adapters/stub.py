"""Stub learning adapter that returns seeded or empty responses.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 6.6).
Placeholder for future learning ports.
"""


class StubLearningAdapter:
    """Stub adapter returning empty results for all queries by default.

    Accepts optional ``seed_responses`` dict for Phase 5B harness
    testability.  Keys map to method names, values are the lists to
    return.
    """

    def __init__(
        self, seed_responses: dict[str, list] | None = None
    ) -> None:
        self._seed = seed_responses or {}

    def clear(self) -> None:
        """Reset seed responses. Required for test isolation."""
        self._seed = {}

    async def query_learning_events(self) -> list:
        """Return seeded or empty list of learning events."""
        return self._seed.get("query_learning_events", [])

    async def query_pending_learning(self) -> list:
        """Return seeded or empty list of pending learning items."""
        return self._seed.get("query_pending_learning", [])

    async def query_promotion_candidates(self) -> list:
        """Return seeded or empty list of promotion candidates."""
        return self._seed.get("query_promotion_candidates", [])
