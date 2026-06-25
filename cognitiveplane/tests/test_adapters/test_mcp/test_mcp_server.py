"""Test MCPServer — connection wrapper delegating to MCPClient.

Spec: §2.4 + §3.1.
"""
from __future__ import annotations

import asyncio

import pytest

from cognitiveplane.adapters.mcp.base import (
    MCPClient,
    MCPServer,
    ToolDescriptor,
)


class FakeClient(MCPClient):
    """Test double — records calls and lets test drive list_changed."""

    def __init__(self, tools: list[ToolDescriptor], call_result: dict | None = None):
        self._tools = tools
        self._call_result = call_result or {}
        self.call_log: list[tuple[str, dict]] = []
        self._listeners: list = []

    async def list_tools(self) -> list[ToolDescriptor]:
        return self._tools

    async def call_tool(self, name: str, arguments: dict) -> dict:
        self.call_log.append((name, arguments))
        return self._call_result

    def subscribe_list_changed(self, callback) -> None:
        self._listeners.append(callback)

    async def fire_list_changed(self) -> None:
        """Test helper — fire list_changed to all subscribers."""
        for cb in self._listeners:
            await cb()


@pytest.mark.asyncio
async def test_server_list_tools_delegates_to_client():
    tools = [ToolDescriptor(name="t1", description="d1", parameters_schema={})]
    client = FakeClient(tools)
    server = MCPServer(client=client, name="fake")

    result = await server.list_tools()

    assert result == tools


@pytest.mark.asyncio
async def test_server_call_tool_delegates_to_client():
    client = FakeClient([], call_result={"echo": "hello"})
    server = MCPServer(client=client, name="fake")

    result = await server.call_tool("echo_tool", {"text": "hello"})

    assert result == {"echo": "hello"}
    assert client.call_log == [("echo_tool", {"text": "hello"})]


@pytest.mark.asyncio
async def test_server_on_list_changed_forwards_to_client_subscribe():
    client = FakeClient([])
    server = MCPServer(client=client, name="fake")

    fired = asyncio.Event()

    async def listener():
        fired.set()

    server.on_list_changed(listener)
    await client.fire_list_changed()

    assert fired.is_set()


def test_server_name_property():
    client = FakeClient([])
    server = MCPServer(client=client, name="my_server")
    assert server.name == "my_server"