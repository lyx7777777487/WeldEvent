"""Tests for LLM prompt templates."""

from cognitiveplane.capability.prompts.intent_classifier import INTENT_CLASSIFIER_SYSTEM_PROMPT
from cognitiveplane.capability.prompts.knowledge_query import KNOWLEDGE_QUERY_SYSTEM_PROMPT
from cognitiveplane.capability.prompts.reasoning import REASONING_SYSTEM_PROMPT
from cognitiveplane.capability.prompts.planning import PLANNING_SYSTEM_PROMPT
from cognitiveplane.capability.prompts.explanation import EXPLANATION_SYSTEM_PROMPT


def test_intent_classifier_prompt_contains_placeholders():
    assert "{all_mode_descriptions}" in INTENT_CLASSIFIER_SYSTEM_PROMPT
    assert "{streaming_context_summary}" in INTENT_CLASSIFIER_SYSTEM_PROMPT


def test_knowledge_query_prompt_is_nonempty():
    assert len(KNOWLEDGE_QUERY_SYSTEM_PROMPT) > 50


def test_reasoning_prompt_is_nonempty():
    assert len(REASONING_SYSTEM_PROMPT) > 50


def test_planning_prompt_is_nonempty():
    assert len(PLANNING_SYSTEM_PROMPT) > 50


def test_explanation_prompt_is_nonempty():
    assert len(EXPLANATION_SYSTEM_PROMPT) > 50
