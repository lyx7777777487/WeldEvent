"""Interaction Layer — ReAct-based user interaction with the Cognitive Plane.

Source: 7-plane redesign spec §7 Interaction — ReAct.
"""

from cognitiveplane.interaction.base import (
    BaseSessionData,
    ContextRequirement,
    IntentPattern,
    ModeProtocol,
    ModeResponse,
    ResponseType,
    UserAction,
    UserMessage,
)
from cognitiveplane.interaction.context import ActiveContext, ContextResolver, StreamingContext
from cognitiveplane.interaction.session import SessionManager
from cognitiveplane.interaction.multimodal import MultiModalParser, MultiModalMessage
from cognitiveplane.interaction.ports import InstructionPublishPort
from cognitiveplane.interaction.signals.event_bus import InMemoryEventBus
from cognitiveplane.interaction.signals.instruction import LiveInstruction

__all__ = [
    "ActiveContext",
    "BaseSessionData",
    "ContextRequirement",
    "ContextResolver",
    "InMemoryEventBus",
    "InstructionPublishPort",
    "IntentPattern",
    "LiveInstruction",
    "ModeProtocol",
    "ModeResponse",
    "MultiModalMessage",
    "MultiModalParser",
    "ResponseType",
    "SessionManager",
    "StreamingContext",
    "UserAction",
    "UserMessage",
]
