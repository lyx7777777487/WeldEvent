"""Tests for LLMConfig and OpenAIConfig."""

from cognitiveplane.capability.config import LLMConfig, OpenAIConfig


class TestOpenAIConfig:
    def test_create_with_defaults(self):
        cfg = OpenAIConfig(api_key="sk-test")
        assert cfg.api_key == "sk-test"
        assert cfg.base_url == "https://api.openai.com/v1"
        assert cfg.default_model == "gpt-4o"
        assert cfg.organization is None


class TestLLMConfig:
    def test_create_minimal(self):
        cfg = LLMConfig(
            primary=OpenAIConfig(api_key="sk-test")
        )
        assert cfg.primary.api_key == "sk-test"
        assert cfg.fallback is None
        assert cfg.intent_classifier_model is None
        assert cfg.reasoning_model is None

    def test_create_with_routing(self):
        cfg = LLMConfig(
            primary=OpenAIConfig(api_key="sk-test"),
            fallback=OpenAIConfig(api_key="sk-fallback", default_model="gpt-4o-mini"),
            intent_classifier_model="gpt-4o-mini",
            reasoning_model="o1",
            explanation_model="gpt-4o",
            planning_model="gpt-4o",
            embedding_model="text-embedding-3-small",
        )
        assert cfg.intent_classifier_model == "gpt-4o-mini"
        assert cfg.fallback.default_model == "gpt-4o-mini"

    def test_resolve_model_with_purpose(self):
        cfg = LLMConfig(
            primary=OpenAIConfig(api_key="sk-test"),
            reasoning_model="o1",
        )
        assert cfg.resolve_model("reasoning") == "o1"
        assert cfg.resolve_model("unknown") == "gpt-4o"

    def test_resolve_model_fallback_to_default(self):
        cfg = LLMConfig(primary=OpenAIConfig(api_key="sk-test"))
        assert cfg.resolve_model("intent_classification") == "gpt-4o"
