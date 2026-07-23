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


class TestCacheControl:
    """Op 36: prompt-prefix caching 按后端门控."""

    def _provider(self):
        return OpenAIProvider(_make_config())

    def test_deepseek_skips_cache_control(self):
        """DeepSeek 服务端自动缓存: cache_prefix_tokens>0 也不注入 cache_control."""
        p = self._provider()
        req = LLMRequest(
            messages=[
                {"role": "system", "content": "x" * 200},
                {"role": "user", "content": "hi"},
            ],
            purpose="reasoning",
            cache_prefix_tokens=40,
        )
        msgs = p._build_messages(req, "deepseek-chat")
        assert all("cache_control" not in m for m in msgs)

    def test_claude_injects_cache_control_on_prefix(self):
        """Claude 后端: cache_prefix_tokens>0 时前缀消息打 cache_control."""
        p = self._provider()
        sys_msg = "stable system prompt " * 50
        req = LLMRequest(
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": "q"},
            ],
            purpose="reasoning",
            cache_prefix_tokens=40,
        )
        msgs = p._build_messages(req, "claude-3-5-sonnet")
        assert msgs[0].get("cache_control") == {"type": "ephemeral"}

    def test_no_cache_prefix_tokens_skips(self):
        """cache_prefix_tokens=0 时无论模型都不注入."""
        p = self._provider()
        req = LLMRequest(
            messages=[{"role": "user", "content": "hi"}],
            purpose="reasoning",
            cache_prefix_tokens=0,
        )
        msgs_claude = p._build_messages(req, "claude-3-5-sonnet")
        msgs_ds = p._build_messages(req, "deepseek-chat")
        assert all("cache_control" not in m for m in msgs_claude)
        assert all("cache_control" not in m for m in msgs_ds)
