"""Tests for OpenAIProvider — static method coverage without live API."""

from cognitiveplane.capability.openai_provider import OpenAIProvider
from cognitiveplane.capability.config import LLMConfig, OpenAIConfig
from cognitiveplane.capability.provider import LLMRequest


def _make_config() -> LLMConfig:
    return LLMConfig(
        primary=OpenAIConfig(
            api_key="test-key",
            base_url="https://api.example.com/v1",
            default_model="test-model",
        )
    )


class TestOpenAIProviderStatic:
    def test_inject_json_hint_without_format(self):
        """When no response_format, messages pass through unchanged."""
        request = LLMRequest(
            messages=[{"role": "user", "content": "Hello"}],
            purpose="test",
        )
        result = OpenAIProvider._inject_json_hint(request)
        assert len(result) == 1
        assert result[0]["content"] == "Hello"

    def test_inject_json_hint_with_format(self):
        """When response_format is set, a JSON hint is appended."""
        from pydantic import BaseModel

        class TestSchema(BaseModel):
            answer: str

        request = LLMRequest(
            messages=[{"role": "user", "content": "Extract info"}],
            purpose="test",
            response_format=TestSchema,
        )
        result = OpenAIProvider._inject_json_hint(request)
        assert len(result) == 2  # original + hint
        assert "json" in result[-1]["content"].lower()

    def test_inject_json_hint_already_has_json(self):
        """When messages already mention JSON, no extra hint added."""
        request = LLMRequest(
            messages=[{"role": "user", "content": "Return JSON data"}],
            purpose="test",
            response_format=dict,
        )
        result = OpenAIProvider._inject_json_hint(request)
        assert len(result) == 1

    def test_try_parse_none_format(self):
        """_try_parse returns None when no format specified."""
        result = OpenAIProvider._try_parse('{"key": "value"}', None)
        assert result is None

    def test_try_parse_valid_json(self):
        """_try_parse parses valid JSON into the pydantic model."""
        from pydantic import BaseModel

        class TestSchema(BaseModel):
            answer: str

        result = OpenAIProvider._try_parse('{"answer": "hello"}', TestSchema)
        assert result is not None
        assert result.answer == "hello"

    def test_try_parse_invalid_json(self):
        """_try_parse returns None for unparseable content."""
        from pydantic import BaseModel

        class TestSchema(BaseModel):
            answer: str

        result = OpenAIProvider._try_parse("not json at all", TestSchema)
        assert result is None

    def test_try_parse_code_block(self):
        """_try_parse strips markdown code blocks."""
        from pydantic import BaseModel

        class TestSchema(BaseModel):
            value: int

        content = '```json\n{"value": 42}\n```'
        result = OpenAIProvider._try_parse(content, TestSchema)
        assert result is not None
        assert result.value == 42

    def test_health_check_false_before_init(self):
        """health_check returns False when client not initialized."""
        provider = OpenAIProvider(_make_config())
        assert provider.health_check() is False
