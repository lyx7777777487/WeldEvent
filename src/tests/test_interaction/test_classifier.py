"""Tests for IntentClassifier — dual mode (keyword + LLM)."""

import pytest
from datetime import datetime, timezone

from src.interaction.base import IntentPattern, UserMessage
from src.interaction.classifier import IntentClassification, IntentClassifier
from src.interaction.context import ActiveContext
from src.interaction.llm import init_llm, reset_llm
from src.interaction.llm.mock_provider import MockLLMProvider
from src.interaction.registry import ModeRegistry
from src.shared.enums import MatchStrategy
from src.shared.types import CaseId


class _TestKnowledgeMode:
    mode_id = "cognitive.knowledge_query"
    display_name = "Knowledge Query"
    description = "Answer knowledge questions"
    intent_patterns = [
        IntentPattern(
            match_strategy=MatchStrategy.KEYWORD,
            patterns=["多少", "温度", "参数", "标准", "规定", "要求", "什么是"],
            priority=50,
            confidence_threshold=0.3,
        )
    ]
    required_ports = []
    creates_session = False
    session_data_schema = None

    async def handle(self, message, deps):
        pass


class _TestInterventionMode:
    mode_id = "cognitive.intervention"
    display_name = "Intervention"
    description = "Issue production line instructions"
    intent_patterns = [
        IntentPattern(
            match_strategy=MatchStrategy.KEYWORD,
            patterns=["加强", "检测", "从图", "跳过", "恢复"],
            priority=60,
            confidence_threshold=0.5,
        )
    ]
    required_ports = []
    creates_session = False
    session_data_schema = None

    async def handle(self, message, deps):
        pass


def _make_registry() -> ModeRegistry:
    registry = ModeRegistry()
    registry.register(type("M1", (), dict(_TestKnowledgeMode.__dict__)))
    registry.register(type("M2", (), dict(_TestInterventionMode.__dict__)))
    return registry


class TestIntentClassification:
    def test_create(self):
        classification = IntentClassification(
            primary_intent="cognitive.knowledge_query",
            confidence=0.85,
            extracted_entities={"material": "Q345R"},
        )
        assert classification.primary_intent == "cognitive.knowledge_query"
        assert classification.confidence == 0.85


class TestIntentClassifierKeywordMode:
    """Tests for keyword-based classification (no LLM initialized)."""

    def setup_method(self):
        reset_llm()

    def test_classify_knowledge_query(self):
        registry = _make_registry()
        classifier = IntentClassifier(registry)
        msg = UserMessage(
            raw_text="Q345R预热温度多少",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
        )
        ctx = ActiveContext(operator_id="zhangsan")
        result = classifier.classify(msg, ctx)
        assert result.primary_intent == "cognitive.knowledge_query"
        assert result.confidence > 0.0

    def test_classify_unknown_returns_low_confidence(self):
        registry = _make_registry()
        classifier = IntentClassifier(registry)
        msg = UserMessage(
            raw_text="今天天气怎么样",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
        )
        ctx = ActiveContext(operator_id="zhangsan")
        result = classifier.classify(msg, ctx)
        assert result.confidence < 0.3


class TestIntentClassifierLLMMode:
    """Tests for LLM-based classification (MockLLMProvider initialized)."""

    def setup_method(self):
        reset_llm()

    def test_llm_classification_uses_provider(self):
        init_llm(MockLLMProvider(
            canned_json={
                "primary_intent": "cognitive.knowledge_query",
                "confidence": 0.92,
                "extracted_entities": {"material": "Q345R", "parameter": "预热温度"},
            }
        ))
        registry = _make_registry()
        classifier = IntentClassifier(registry)
        msg = UserMessage(
            raw_text="Q345R预热温度多少",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
        )
        ctx = ActiveContext(operator_id="zhangsan")
        result = classifier.classify(msg, ctx)
        assert result.primary_intent == "cognitive.knowledge_query"
        assert result.confidence == 0.92

    def test_llm_fallback_to_keyword_on_error(self):
        init_llm(MockLLMProvider(default_response="error"))
        registry = _make_registry()
        classifier = IntentClassifier(registry)
        msg = UserMessage(
            raw_text="Q345R预热温度多少",
            operator_id="zhangsan",
            timestamp=datetime.now(timezone.utc),
        )
        ctx = ActiveContext(operator_id="zhangsan")
        # When LLM returns unparseable output, classifier falls back to keyword
        result = classifier.classify(msg, ctx)
        assert result.primary_intent == "cognitive.knowledge_query"
