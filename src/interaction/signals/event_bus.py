"""InMemoryEventBus — in-memory implementation of EventBusPort.

Source: L1-Interaction-Layer-Business-Requirements.md §6.3.
"""

import asyncio
from typing import Any
from uuid import uuid4

from src.shared.ports.event_bus import (
    EventBusPort,
    InstructionPublishInput,
    InstructionPublishOutput,
    InstructionPublishPort,
)


class InMemoryEventBus(InstructionPublishPort, EventBusPort):
    """In-memory event bus for testing and development."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue] = {}
        self._instructions: list[dict] = []

    async def publish_instruction(
        self, input_data: InstructionPublishInput
    ) -> InstructionPublishOutput:
        instruction_id = str(uuid4())
        self._instructions.append({
            "instruction_id": instruction_id,
            **input_data.model_dump(),
        })
        return InstructionPublishOutput(instruction_id=instruction_id)

    async def publish_event(self, topic: str, event: Any) -> None:
        if topic not in self._queues:
            self._queues[topic] = asyncio.Queue()
        await self._queues[topic].put(event)

    async def consume_event(self, topic: str) -> Any | None:
        if topic not in self._queues:
            return None
        queue = self._queues[topic]
        if queue.empty():
            return None
        return queue.get_nowait()

    def clear(self) -> None:
        """Reset all internal storage."""
        self._queues.clear()
        self._instructions.clear()
