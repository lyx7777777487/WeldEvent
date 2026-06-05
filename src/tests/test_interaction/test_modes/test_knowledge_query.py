"""Tests for Knowledge Query Mode (Mode A)."""

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from src.interaction.base import IntentPattern, ModeResponse, ResponseType, UserMessage
from src.interaction.dependencies import ModeDependencies
from src.interaction.modes.knowledge_query import KnowledgeQueryMode
from src.interaction.registry import ModeRegistry
from src.shared.enums import KnowledgeType, MatchStrategy
from src.shared.types import KnowledgeId, SessionId


class TestKnowledgeQueryMode:
    def test_mode_metadata(self):
        mode = KnowledgeQueryMode()
        assert mode.mode_id == "cognitive.knowledge_query"
        assert mode.creates_session is False
        assert "RAGQueryPort" in mode.required_ports

    def test_keyword_patterns(self):
        mode = KnowledgeQueryMode()
        keyword_pattern = next(
            p for p in mode.intent_patterns if p.match_strategy == MatchStrategy.KEYWORD
        )
        assert "多少" in keyword_pattern.patterns

    @pytest.mark.asyncio
    async def test_handle_with_rag_port(self):
        from src.shared.ports.knowledge import RAGQueryInput, RAGQueryOutput
        from src.shared.dto_knowledge import KnowledgeResult

        class FakeRAGPort:
            async def query(self, input_data: RAGQueryInput) -> RAGQueryOutput:
                return RAGQueryOutput(
                    results=[
                        KnowledgeResult(
                            knowledge_id=KnowledgeId(value=uuid4()),
                            knowledge_type=KnowledgeType.STANDARD,
                            content="Q345R 22mm 预热温度要求 >=100°C",
                            source_reference="NB/T47014",
                            relevance_score=0.95,
                        )
                    ]
                )

        mode = KnowledgeQueryMode()
        deps = ModeDependencies(RAGQueryPort=FakeRAGPort())
        msg = UserMessage(
            raw_text="Q345R预热温度多少",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
        )
        response = await mode.handle(msg, deps)
        assert response.response_type == ResponseType.TEXT_REPLY
        assert response.mode_id == "cognitive.knowledge_query"
        assert "RAGQueryPort" in response.ports_accessed
        assert "NB/T47014" in response.text_reply

    @pytest.mark.asyncio
    async def test_handle_no_results(self):
        from src.shared.ports.knowledge import RAGQueryInput, RAGQueryOutput

        class FakeEmptyRAGPort:
            async def query(self, input_data: RAGQueryInput) -> RAGQueryOutput:
                return RAGQueryOutput(results=[])

        mode = KnowledgeQueryMode()
        deps = ModeDependencies(RAGQueryPort=FakeEmptyRAGPort())
        msg = UserMessage(
            raw_text="不存在的知识",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
        )
        response = await mode.handle(msg, deps)
        assert response.response_type == ResponseType.TEXT_REPLY
        assert "未找到" in response.text_reply

    @pytest.mark.asyncio
    async def test_handle_without_rag_port_raises(self):
        mode = KnowledgeQueryMode()
        deps = ModeDependencies()
        msg = UserMessage(
            raw_text="test",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
        )
        with pytest.raises(KeyError):
            await mode.handle(msg, deps)

    def test_register_in_registry(self):
        registry = ModeRegistry()
        registry.register(KnowledgeQueryMode)
        mode = registry.get("cognitive.knowledge_query")
        assert mode is not None
        assert isinstance(mode, KnowledgeQueryMode)
