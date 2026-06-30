"""Tests for MockLLMProvider."""

import pytest

from cognitiveplane.capability.mock import MockLLMProvider
from cognitiveplane.capability.provider import LLMRequest, LLMResponse


class TestMockLLMProvider:
    @pytest.mark.asyncio
    async def test_complete_returns_canned_response(self):
        provider = MockLLMProvider(
            canned_responses={"hello": "world"}
        )
        req = LLMRequest(messages=[{"role": "user", "content": "hello"}])
        resp = await provider.complete(req)
        assert resp.content == "world"

    @pytest.mark.asyncio
    async def test_complete_returns_default_when_no_match(self):
        provider = MockLLMProvider(default_response="I don't know")
        req = LLMRequest(messages=[{"role": "user", "content": "anything"}])
        resp = await provider.complete(req)
        assert resp.content == "I don't know"

    @pytest.mark.asyncio
    async def test_complete_with_response_format_parses(self):
        from pydantic import BaseModel

        class TestFormat(BaseModel):
            primary_intent: str
            confidence: float

        provider = MockLLMProvider(
            canned_json={
                "primary_intent": "cognitive.knowledge_query",
                "confidence": 0.9,
            }
        )
        req = LLMRequest(
            messages=[{"role": "user", "content": "test"}],
            response_format=TestFormat,
        )
        resp = await provider.complete(req)
        assert resp.parsed_object is not None
        assert resp.parsed_object.primary_intent == "cognitive.knowledge_query"

    @pytest.mark.asyncio
    async def test_embed_returns_fixed_vectors(self):
        provider = MockLLMProvider(embedding_dim=4)
        vectors = await provider.embed(["hello", "world"])
        assert len(vectors) == 2
        assert len(vectors[0]) == 4

    def test_health_check_returns_true(self):
        provider = MockLLMProvider()
        assert provider.health_check() is True

    @pytest.mark.asyncio
    async def test_stream_yields_chunks(self):
        provider = MockLLMProvider(default_response="hello world")
        req = LLMRequest(messages=[{"role": "user", "content": "test"}])
        chunks = []
        async for chunk in provider.stream(req):
            chunks.append(chunk)
        assert len(chunks) > 0
