"""Stub knowledge adapter that returns seeded or empty responses.

Source: L1_Port_and_Contract_Design.md (Phase 4, Sections 1.1, 6.4).
"""

from src.shared.ports.knowledge import (
    CaseLibraryQueryInput,
    CaseLibraryQueryOutput,
    CaseLibraryQueryPort,
    EquipmentKnowledgeInput,
    EquipmentKnowledgeOutput,
    EquipmentKnowledgePort,
    ProcessKnowledgeInput,
    ProcessKnowledgeOutput,
    ProcessKnowledgePort,
    RAGQueryInput,
    RAGQueryOutput,
    RAGQueryPort,
    RuleQueryInput,
    RuleQueryOutput,
    RuleQueryPort,
    StandardsQueryInput,
    StandardsQueryOutput,
    StandardsQueryPort,
)


# Map input type -> (seed key, output class)
_INPUT_DISPATCH: dict[type, tuple[str, type]] = {
    RAGQueryInput: ("rag_query", RAGQueryOutput),
    RuleQueryInput: ("rule_query", RuleQueryOutput),
    StandardsQueryInput: ("standards_query", StandardsQueryOutput),
    EquipmentKnowledgeInput: ("equipment_query", EquipmentKnowledgeOutput),
    ProcessKnowledgeInput: ("process_query", ProcessKnowledgeOutput),
    CaseLibraryQueryInput: ("case_library_query", CaseLibraryQueryOutput),
}


class StubKnowledgeAdapter(
    RAGQueryPort,
    RuleQueryPort,
    StandardsQueryPort,
    EquipmentKnowledgePort,
    ProcessKnowledgePort,
    CaseLibraryQueryPort,
):
    """Stub adapter returning empty lists for all queries by default.

    All 6 knowledge ports share the same method name ``query()`` with
    different Input/Output types.  Python does not support method
    overloading, so a single ``query`` implementation dispatches on
    the runtime type of *input_data*.

    Accepts optional ``seed_responses`` dict for Phase 5B harness
    testability.  Keys are seed keys (e.g. ``"rag_query"``), values
    are the lists to return.
    """

    def __init__(
        self, seed_responses: dict[str, list] | None = None
    ) -> None:
        self._seed = seed_responses or {}

    def clear(self) -> None:
        """Reset seed responses. Required for test isolation."""
        self._seed = {}

    # ------------------------------------------------------------------
    # Unified query dispatcher
    # ------------------------------------------------------------------

    async def query(self, input_data):
        """Dispatch to the correct output type based on *input_data* type."""
        input_type = type(input_data)
        entry = _INPUT_DISPATCH.get(input_type)
        if entry is None:
            raise TypeError(
                f"StubKnowledgeAdapter: unrecognised input type {input_type}"
            )
        seed_key, output_cls = entry
        results = self._seed.get(seed_key, [])
        return output_cls(results=results)
