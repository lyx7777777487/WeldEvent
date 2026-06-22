"""Stub knowledge adapter with keyword-based filtering.

Returns seeded responses filtered by query keywords when possible,
falling back to full seed list for non-keyword queries.
"""

from cognitiveplane.shared.ports.knowledge import (
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
    """Stub adapter with keyword-based filtering on seeded data.

    All 6 knowledge ports share the same method name ``query()`` with
    different Input/Output types.  Python does not support method
    overloading, so a single ``query`` implementation dispatches on
    the runtime type of *input_data*.

    When seed data contains text fields, results are filtered by
    keyword matching against the query.  If no keywords match or the
    query is empty, all seed results are returned (capped by
    max_results).
    """

    def __init__(
        self, seed_responses: dict[str, list] | None = None
    ) -> None:
        self._seed = seed_responses or {}

    def clear(self) -> None:
        """Reset seed responses. Required for test isolation."""
        self._seed = {}

    # ------------------------------------------------------------------
    # Keyword-based filtering
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_keywords(query_text: str) -> list[str]:
        """Extract search keywords from a query string."""
        # Common stopwords to skip
        stopwords = {
            "的", "了", "是", "在", "有", "和", "与", "或", "不", "也",
            "都", "这", "那", "个", "一", "我", "你", "他", "她", "它",
            "什么", "怎么", "如何", "为什么", "哪", "哪些", "多少",
            "请", "问", "查", "看", "找", "搜", "能", "可以", "会",
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "do", "does", "did", "will", "would", "can", "could",
            "what", "how", "why", "which", "when", "where", "who",
            "and", "or", "not", "in", "on", "at", "to", "for",
        }
        # Split on whitespace and common delimiters
        import re
        tokens = re.split(r'[\s,，。、；;：:！!？?（）()\[\]【】]+', query_text.lower())
        return [t for t in tokens if t and t not in stopwords and len(t) > 0]

    @staticmethod
    def _match_score(item, keywords: list[str]) -> float:
        """Score an item against keywords by checking all string fields."""
        if not keywords:
            return 1.0
        text = ""
        if hasattr(item, 'content'):
            text += str(item.content) + " "
        if hasattr(item, 'text'):
            text += str(item.text) + " "
        if hasattr(item, 'clause'):
            text += str(item.clause) + " "
        if hasattr(item, 'defect_description'):
            text += str(item.defect_description) + " "
        if hasattr(item, 'resolution'):
            text += str(item.resolution) + " "
        if hasattr(item, 'source_reference'):
            text += str(item.source_reference) + " "
        if hasattr(item, 'rule_text'):
            text += str(item.rule_text) + " "
        if hasattr(item, 'specifications'):
            text += str(item.specifications) + " "
        if hasattr(item, 'common_defects'):
            text += str(item.common_defects) + " "
        if hasattr(item, 'quality_criteria'):
            text += str(item.quality_criteria) + " "
        text_lower = text.lower()
        hits = sum(1 for kw in keywords if kw.lower() in text_lower)
        return hits / len(keywords) if keywords else 0.0

    def _filter_results(self, results: list, keywords: list[str], max_results: int = 10) -> list:
        """Filter and rank results by keyword relevance."""
        if not keywords:
            return results[:max_results]
        scored = [(self._match_score(r, keywords), r) for r in results]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for score, r in scored if score > 0][:max_results]

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
        all_results = self._seed.get(seed_key, [])

        # Extract query text for keyword filtering
        query_text = ""
        max_results = 10
        if hasattr(input_data, 'query'):
            q = input_data.query
            if hasattr(q, 'query_text'):
                query_text = q.query_text or ""
            if hasattr(q, 'keyword'):
                query_text = (query_text + " " + (q.keyword or "")).strip()
            if hasattr(q, 'defect_type') and q.defect_type:
                query_text = (query_text + " " + q.defect_type).strip()
            if hasattr(q, 'process_type') and q.process_type:
                query_text = (query_text + " " + q.process_type).strip()
            if hasattr(q, 'standard_id') and q.standard_id:
                query_text = (query_text + " " + q.standard_id).strip()
            if hasattr(q, 'max_results'):
                max_results = q.max_results

        keywords = self._extract_keywords(query_text)
        filtered = self._filter_results(all_results, keywords, max_results)
        return output_cls(results=filtered)
