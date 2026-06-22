"""Tests for StubKnowledgeAdapter and InMemoryKnowledgeRepository.

Covers: RAGQueryPort.query() returns [], RuleQueryPort.query() returns [],
StandardsQueryPort.query() returns [].
"""

import pytest
from uuid import uuid4

from cognitiveplane.shared.dto_knowledge import (
    CaseLibraryQuery,
    EquipmentKnowledgeQuery,
    KnowledgeResult,
    ProcessKnowledgeQuery,
    RAGQuery,
    RuleQuery,
    StandardsQuery,
)
from cognitiveplane.shared.ports.knowledge import (
    CaseLibraryQueryInput,
    EquipmentKnowledgeInput,
    ProcessKnowledgeInput,
    RAGQueryInput,
    RuleQueryInput,
    StandardsQueryInput,
)
from cognitiveplane.shared.enums import KnowledgeType
from cognitiveplane.shared.types import KnowledgeId
from cognitiveplane.knowledge.adapters.stub import StubKnowledgeAdapter
from cognitiveplane.knowledge.repositories.in_memory import InMemoryKnowledgeRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> StubKnowledgeAdapter:
    return StubKnowledgeAdapter()


@pytest.fixture
def repo() -> InMemoryKnowledgeRepository:
    return InMemoryKnowledgeRepository()


# ===================================================================
# Test cases — StubKnowledgeAdapter
# ===================================================================


class TestRAGQueryReturnsEmpty:
    """RAGQueryPort.query() returns []."""

    @pytest.mark.asyncio
    async def test_rag_query_returns_empty(self, adapter: StubKnowledgeAdapter):
        input_data = RAGQueryInput(
            query=RAGQuery(query_text="welding parameters")
        )
        output = await adapter.query(input_data)
        assert output.results == []


class TestRuleQueryReturnsEmpty:
    """RuleQueryPort.query() returns []."""

    @pytest.mark.asyncio
    async def test_rule_query_returns_empty(self, adapter: StubKnowledgeAdapter):
        input_data = RuleQueryInput(
            query=RuleQuery(rule_category="safety", context={})
        )
        output = await adapter.query(input_data)
        assert output.results == []


class TestStandardsQueryReturnsEmpty:
    """StandardsQueryPort.query() returns []."""

    @pytest.mark.asyncio
    async def test_standards_query_returns_empty(
        self, adapter: StubKnowledgeAdapter
    ):
        input_data = StandardsQueryInput(
            query=StandardsQuery(keyword="ISO")
        )
        output = await adapter.query(input_data)
        assert output.results == []


class TestSeedResponses:
    """StubKnowledgeAdapter returns seeded data when provided."""

    @pytest.mark.asyncio
    async def test_rag_query_with_seed(self):
        seeded = [
            KnowledgeResult(
                knowledge_id=KnowledgeId(value=uuid4()),
                knowledge_type=KnowledgeType.STANDARD,
                content="Test content",
                relevance_score=0.9,
                source_reference="ref-advice.md",
            )
        ]
        adapter = StubKnowledgeAdapter(seed_responses={"rag_query": seeded})
        input_data = RAGQueryInput(
            query=RAGQuery(query_text="test")
        )
        output = await adapter.query(input_data)
        assert len(output.results) == 1
        assert output.results[0].content == "Test content"


class TestClear:
    """clear() resets seed responses."""

    @pytest.mark.asyncio
    async def test_clear_resets_seed(self):
        seeded = [
            KnowledgeResult(
                knowledge_id=KnowledgeId(value=uuid4()),
                knowledge_type=KnowledgeType.STANDARD,
                content="Seeded",
                relevance_score=0.9,
                source_reference="ref-advice.md",
            )
        ]
        adapter = StubKnowledgeAdapter(seed_responses={"rag_query": seeded})
        adapter.clear()
        input_data = RAGQueryInput(
            query=RAGQuery(query_text="test")
        )
        output = await adapter.query(input_data)
        assert output.results == []
