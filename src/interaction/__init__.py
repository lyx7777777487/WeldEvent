"""Interaction Layer — Intent-aware user interaction with the Cognitive Plane.

Source: L1-Interaction-Layer-Business-Requirements.md.
"""

from src.interaction.base import (
    BaseSessionData,
    ContextRequirement,
    IntentPattern,
    ModeProtocol,
    ModeResponse,
    ResponseType,
    UserAction,
    UserMessage,
)
from src.interaction.classifier import IntentClassification, IntentClassifier
from src.interaction.context import ActiveContext, ContextResolver, StreamingContext
from src.interaction.dependencies import ModeDependencies, PortProvider
from src.interaction.entities.image import ImageEntity
from src.interaction.llm import get_llm, init_llm, reset_llm
from src.interaction.llm.config import LLMConfig, OpenAIConfig
from src.interaction.llm.mock_provider import MockLLMProvider
from src.interaction.llm.openai_provider import OpenAIProvider
from src.interaction.llm.provider import LLMProvider, LLMRequest, LLMResponse
from src.interaction.llm.tracking import LLMCallRecord, LLMCallTracker
from src.interaction.modes.knowledge_query import KnowledgeQueryMode
from src.interaction.registry import ModeRegistry, mode_registry
from src.interaction.router import SessionRouter
from src.interaction.signals.event_bus import InMemoryEventBus
from src.interaction.signals.instruction import LiveInstruction

__all__ = [
    "ActiveContext",
    "BaseSessionData",
    "ContextRequirement",
    "ImageEntity",
    "InMemoryEventBus",
    "IntentClassification",
    "IntentClassifier",
    "IntentPattern",
    "KnowledgeQueryMode",
    "LLMCallRecord",
    "LLMCallTracker",
    "LLMConfig",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "LiveInstruction",
    "MockLLMProvider",
    "OpenAIProvider",
    "ModeDependencies",
    "ModeProtocol",
    "ModeRegistry",
    "ModeResponse",
    "OpenAIConfig",
    "PortProvider",
    "ResponseType",
    "SessionRouter",
    "StreamingContext",
    "UserAction",
    "UserMessage",
    "get_llm",
    "init_llm",
    "mode_registry",
    "reset_llm",
]
