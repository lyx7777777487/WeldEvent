"""LLM configuration models — per-purpose model routing.

Source: L1-Interaction-Layer-Business-Requirements.md §3.6.4.
"""

from pydantic import BaseModel, Field


class OpenAIConfig(BaseModel):
    """OpenAI-compatible API configuration."""

    api_key: str = Field(min_length=1)
    base_url: str = "https://api.openai.com/v1"
    default_model: str = "gpt-4o"
    organization: str | None = None


class LLMConfig(BaseModel):
    """Global LLM configuration — loaded once at startup.

    Supports per-purpose model routing: different models for classification,
    reasoning, explanation, planning, embedding, and vision (multimodal).
    """

    primary: OpenAIConfig
    fallback: OpenAIConfig | None = None

    intent_classifier_model: str | None = None
    explanation_model: str | None = None
    reasoning_model: str | None = None
    planning_model: str | None = None
    embedding_model: str | None = None
    vision_model: str | None = None

    # 多模态模型独立配置（如火山引擎 Doubao-Vision）
    vision: OpenAIConfig | None = None

    def resolve_model(self, purpose: str) -> str:
        """Resolve the model name for a given purpose."""
        purpose_map = {
            "intent_classification": self.intent_classifier_model,
            "entity_extraction": self.intent_classifier_model,
            "reasoning": self.reasoning_model,
            "planning": self.planning_model,
            "explanation": self.explanation_model,
            "knowledge_query": self.explanation_model,
            "embedding": self.embedding_model,
            "vision": self.vision_model,
        }
        return purpose_map.get(purpose) or self.primary.default_model

    def resolve_vision_config(self) -> OpenAIConfig:
        """Resolve the config for vision (multimodal) calls."""
        if self.vision is not None:
            return self.vision
        return self.primary
