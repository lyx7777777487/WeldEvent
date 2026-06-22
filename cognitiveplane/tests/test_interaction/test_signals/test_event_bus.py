"""Tests for InstructionPublishPort, EventBusPort, InMemoryEventBus."""

import pytest

from cognitiveplane.interaction.signals.event_bus import InMemoryEventBus
from cognitiveplane.shared.ports.event_bus import (
    EventBusPort,
    InstructionPublishInput,
    InstructionPublishPort,
)


class TestInMemoryEventBus:
    @pytest.mark.asyncio
    async def test_publish_and_consume_instruction(self):
        bus = InMemoryEventBus()
        output = await bus.publish_instruction(
            InstructionPublishInput(
                instruction_type="skip_image",
                target_case_id="CASE-001",
                target_image_index=3,
                reason="blur",
                issued_by="zhangsan",
            )
        )
        assert output.published is True
        assert output.instruction_id

    @pytest.mark.asyncio
    async def test_consume_returns_none_when_empty(self):
        bus = InMemoryEventBus()
        result = await bus.consume_event("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_publish_and_consume_event(self):
        bus = InMemoryEventBus()
        await bus.publish_event("test_topic", {"type": "test"})
        event = await bus.consume_event("test_topic")
        assert event == {"type": "test"}


class TestPortABCs:
    def test_instruction_publish_port_cannot_instantiate(self):
        with pytest.raises(TypeError):
            InstructionPublishPort()

    def test_event_bus_port_cannot_instantiate(self):
        with pytest.raises(TypeError):
            EventBusPort()
