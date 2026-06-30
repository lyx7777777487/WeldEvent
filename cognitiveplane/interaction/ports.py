"""Interaction-internal port ABCs.

Source: 7-plane redesign spec §7. InstructionPublishPort moved here from shared/ports/event_bus.py.
"""

from abc import ABC, abstractmethod

from cognitiveplane.interaction.signals.instruction import LiveInstruction
from cognitiveplane.shared.dto.gateway import PublishResult


class InstructionPublishPort(ABC):
    """Port for publishing non-blocking instructions into the event stream."""

    @abstractmethod
    async def publish_instruction(self, instruction: LiveInstruction) -> PublishResult: ...
