"""Event bus and instruction publish ports.

Source: L1-Interaction-Layer-Business-Requirements.md §6.advice.md, §6.3.
"""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class InstructionPublishInput(BaseModel):
    """Input for publishing an instruction."""

    instruction_type: str
    target_case_id: str | None = None
    target_image_index: int | None = None
    payload: dict[str, Any] = {}
    reason: str = ""
    issued_by: str = ""


class InstructionPublishOutput(BaseModel):
    """Output from publishing an instruction."""

    instruction_id: str
    published: bool = False


class InstructionPublishPort(ABC):
    """Port for publishing instructions to the production line."""

    @abstractmethod
    async def publish_instruction(
        self, input_data: InstructionPublishInput
    ) -> InstructionPublishOutput:
        """Publish an instruction to the production line."""
        ...


class EventBusPort(ABC):
    """Port for event bus publish/consume."""

    @abstractmethod
    async def publish_event(self, topic: str, event: Any) -> None:
        """Publish an event to a topic."""
        ...

    @abstractmethod
    async def consume_event(self, topic: str) -> Any | None:
        """Consume an event from a topic (non-blocking)."""
        ...
