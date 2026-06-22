"""Tests for web search tool and providers."""

import pytest

from cognitiveplane.capability.web_search import (
    DuckDuckGoSearchProvider,
    StubWebSearchProvider,
    WebSearchResult,
    WebSearchResponse,
)
from cognitiveplane.control.tools.web_search import WebSearchTool


class TestWebSearchTool:
    @pytest.mark.asyncio
    async def test_execute_with_stub_provider(self):
        stub = StubWebSearchProvider()
        tool = WebSearchTool(stub)
        result = await tool.execute(query="welding standard")
        assert result.error is not None
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_execute_with_results(self):
        from cognitiveplane.capability.web_search import WebSearchProvider

        class FakeProvider(WebSearchProvider):
            async def search(self, query, max_results=5):
                return WebSearchResponse(results=[
                    WebSearchResult(title="Test", url="https://example.com", snippet="A result"),
                ])

        tool = WebSearchTool(FakeProvider())
        result = await tool.execute(query="test query")
        assert result.error is None
        assert len(result.output["results"]) == 1
        assert result.output["results"][0]["title"] == "Test"

    def test_name_and_description(self):
        tool = WebSearchTool(StubWebSearchProvider())
        assert tool.name == "web_search"
        assert "web" in tool.description.lower()

    def test_parameters_schema(self):
        tool = WebSearchTool(StubWebSearchProvider())
        schema = tool.parameters_schema
        assert "query" in schema["properties"]
        assert schema["required"] == ["query"]


class TestStubWebSearchProvider:
    @pytest.mark.asyncio
    async def test_returns_error(self):
        provider = StubWebSearchProvider()
        r = await provider.search("test")
        assert r.error is not None
        assert len(r.results) == 0


class TestDuckDuckGoSearchProvider:
    @pytest.mark.asyncio
    async def test_returns_results(self):
        provider = DuckDuckGoSearchProvider()
        r = await provider.search("welding standard")
        # May have 0 results in CI, but should not error
        assert r.error is None
