"""Tests for LLMProvider ABC and request/response models."""

import pytest

from cognitiveplane.capability.mock import MockLLMProvider
from cognitiveplane.capability.provider import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
)


class TestLLMRequest:
    def test_create_minimal(self):
        req = LLMRequest(messages=[{"role": "user", "content": "hello"}])
        assert len(req.messages) == 1
        assert req.temperature == 0.3
        assert req.max_tokens == 8192
        assert req.model is None
        assert req.caller == ""
        assert req.purpose == ""

    def test_create_full(self):
        req = LLMRequest(
            messages=[{"role": "system", "content": "test"}],
            model="gpt-4o",
            temperature=0.1,
            max_tokens=1024,
            caller="IntentClassifier",
            purpose="intent_classification",
            case_id="CASE-001",
        )
        assert req.model == "gpt-4o"
        assert req.temperature == 0.1
        assert req.caller == "IntentClassifier"

    def test_response_format_accepts_type(self):
        from pydantic import BaseModel
        class TestFormat(BaseModel):
            answer: str
        req = LLMRequest(
            messages=[],
            response_format=TestFormat,
        )
        assert req.response_format is TestFormat


class TestLLMResponse:
    def test_create_minimal(self):
        resp = LLMResponse(content="hello")
        assert resp.content == "hello"
        assert resp.parsed_object is None
        assert resp.tokens_prompt == 0
        assert resp.cost_usd == 0.0

    def test_create_with_tracking(self):
        resp = LLMResponse(
            content="result",
            model_used="gpt-4o",
            tokens_prompt=100,
            tokens_completion=50,
            latency_ms=500,
            cost_usd=0.003,
        )
        assert resp.model_used == "gpt-4o"
        assert resp.latency_ms == 500


class TestLLMProviderABC:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            LLMProvider()


class TestGetLlm:
    def test_get_llm_raises_when_not_initialized(self):
        from cognitiveplane.capability import reset_llm
        reset_llm()
        from cognitiveplane.capability import get_llm
        with pytest.raises(RuntimeError, match="未初始化"):
            get_llm()

    def test_init_llm_with_mock(self):
        from cognitiveplane.capability import init_llm, get_llm, reset_llm
        reset_llm()
        init_llm(MockLLMProvider(default_response="test"))
        provider = get_llm()
        assert provider.health_check() is True
        reset_llm()

    def test_reset_llm_clears_provider(self):
        from cognitiveplane.capability import init_llm, get_llm, reset_llm
        reset_llm()
        init_llm(MockLLMProvider())
        assert get_llm() is not None
        reset_llm()
        with pytest.raises(RuntimeError):
            get_llm()
