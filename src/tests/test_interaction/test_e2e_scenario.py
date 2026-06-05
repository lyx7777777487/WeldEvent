"""End-to-end scenario tests for the Interaction Layer.

Validates the full pipeline: UserMessage -> ContextResolver ->
IntentClassifier -> SessionRouter -> KnowledgeQueryMode -> ModeResponse.
Tests both keyword-only and LLM-enhanced flows.
"""

import asyncio

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from src.interaction.classifier import IntentClassifier
from src.interaction.context import ContextResolver
from src.interaction.dependencies import PortProvider
from src.interaction.base import UserMessage
from src.interaction.llm import init_llm, reset_llm
from src.interaction.llm.mock_provider import MockLLMProvider
from src.interaction.modes.knowledge_query import KnowledgeQueryMode
from src.interaction.registry import ModeRegistry
from src.interaction.router import SessionRouter

from src.shared.dto_knowledge import KnowledgeResult, KnowledgeType
from src.shared.ports.knowledge import RAGQueryInput, RAGQueryOutput
from src.shared.types import KnowledgeId


class FakeRAGPort:
    """Fake RAG port that returns a canned knowledge result."""

    def __init__(self, answer: str = "Q345R 22mm 预热温度要求 >=100°C"):
        self._answer = answer

    async def query(self, input_data: RAGQueryInput) -> RAGQueryOutput:
        return RAGQueryOutput(
            results=[
                KnowledgeResult(
                    knowledge_id=KnowledgeId(value=uuid4()),
                    knowledge_type=KnowledgeType.STANDARD,
                    content=self._answer,
                    source_reference="NB/T47014",
                    relevance_score=0.95,
                )
            ]
        )


class TestE2EKeywordMode:
    """E2E with keyword-only IntentClassifier (no LLM)."""

    @pytest.mark.asyncio
    async def test_knowledge_query_keyword_flow(self):
        reset_llm()

        registry = ModeRegistry()
        registry.register(KnowledgeQueryMode)

        provider = PortProvider()
        provider.register("RAGQueryPort", FakeRAGPort())

        context_resolver = ContextResolver()
        classifier = IntentClassifier(registry)
        router = SessionRouter(registry, provider, context_resolver)

        msg = UserMessage(
            raw_text="Q345R预热温度多少",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
        )

        # Classify
        ctx = context_resolver.resolve("zhangsan")
        classification = classifier.classify(msg, ctx)
        assert classification.primary_intent == "cognitive.knowledge_query"

        # Enrich + route
        classified_msg = msg.model_copy(
            update={
                "intent_label": classification.primary_intent,
                "intent_confidence": classification.confidence,
            }
        )
        response = await router.route(classified_msg)
        assert response.mode_id == "cognitive.knowledge_query"
        assert "100°C" in response.text_reply

        reset_llm()


class TestE2ELLMMode:
    """E2E with LLM-enhanced IntentClassifier (MockLLMProvider).

    Uses synchronous test methods because IntentClassifier._classify_llm
    creates its own event loop via asyncio.new_event_loop().run_until_complete(),
    which conflicts with the already-running pytest-asyncio event loop.
    The async routing step is invoked via asyncio.run() instead.
    """

    def setup_method(self):
        reset_llm()

    def teardown_method(self):
        reset_llm()

    def test_knowledge_query_llm_flow(self):
        init_llm(MockLLMProvider(
            canned_json={
                "primary_intent": "cognitive.knowledge_query",
                "confidence": 0.95,
                "extracted_entities": {"material": "Q345R", "parameter": "预热温度"},
            }
        ))

        registry = ModeRegistry()
        registry.register(KnowledgeQueryMode)

        provider = PortProvider()
        provider.register("RAGQueryPort", FakeRAGPort())

        context_resolver = ContextResolver()
        classifier = IntentClassifier(registry)
        router = SessionRouter(registry, provider, context_resolver)

        msg = UserMessage(
            raw_text="Q345R预热温度多少",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
        )

        # Classify (LLM mode) — synchronous, works outside async context
        ctx = context_resolver.resolve("zhangsan")
        classification = classifier.classify(msg, ctx)
        assert classification.primary_intent == "cognitive.knowledge_query"
        assert classification.confidence == 0.95

        # Enrich + route — async, run in a fresh event loop
        classified_msg = msg.model_copy(
            update={
                "intent_label": classification.primary_intent,
                "intent_confidence": classification.confidence,
                "extracted_entities": classification.extracted_entities,
            }
        )
        response = asyncio.run(router.route(classified_msg))
        assert response.mode_id == "cognitive.knowledge_query"
        assert "100°C" in response.text_reply
