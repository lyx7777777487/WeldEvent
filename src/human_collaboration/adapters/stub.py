"""Stub human collaboration adapter that returns seeded or empty responses.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 6.5).
Placeholder for future collaboration ports.
"""


class StubCollaborationAdapter:
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

    async def query_pending_reviews(self) -> list:
        """Return seeded or empty list of pending reviews."""
        return self._seed.get("query_pending_reviews", [])

    async def query_feedback(self) -> list:
        """Return seeded or empty list of feedback."""
        return self._seed.get("query_feedback", [])

    async def query_conversations(self) -> list:
        """Return seeded or empty list of conversations."""
        return self._seed.get("query_conversations", [])
