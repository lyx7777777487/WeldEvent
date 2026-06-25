"""Test InProcessClient — Phase 3 唯一传输实现.

Spec §2.4 + §3.2 steps 13-14 — call_tool 直接调 Python 函数。
"""
from __future__ import annotations

import asyncio

import pytest

from cognitiveplane.adapters.mcp.base import MCPAnnotations, ToolDescriptor
from cognitiveplane.adapters.mcp.in_process import InProcessClient


@pytest.mark.asyncio
async def test_list_tools_returns_registered_descriptors():
    tools = [
        ToolDescriptor(
            name="echo_tool",
            description="Echo input",
            parameters_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            annotations=MCPAnnotations(readOnlyHint=True),
        ),
    ]

    async def echo_handler(text: str) -> dict:
        return {"echo": text}

    client = InProcessClient(tools=tools, handlers={"echo_tool": echo_handler})

    result = await client.list_tools()

    assert len(result) == 1
    assert result[0].name == "echo_tool"
    assert result[0].annotations is not None
    assert result[0].annotations.readOnlyHint is True


@pytest.mark.asyncio
async def test_call_tool_routes_to_handler():
    desc = ToolDescriptor(name="echo_tool", description="d", parameters_schema={})

    async def echo_handler(text: str) -> dict:
        return {"echo": text}

    client = InProcessClient(tools=[desc], handlers={"echo_tool": echo_handler})

    result = await client.call_tool("echo_tool", {"text": "hello"})

    assert result == {"echo": "hello"}


@pytest.mark.asyncio
async def test_call_tool_unknown_tool_raises():
    """Unknown tool — InProcessClient cannot route, raises KeyError.

    上层 MCPAdapter 会捕获并转为 ToolResult(error=...) — 见 Task 4 测试。
    """
    client = InProcessClient(tools=[], handlers={})

    with pytest.raises(KeyError):
        await client.call_tool("nonexistent", {})


@pytest.mark.asyncio
async def test_call_tool_handler_missing_raises():
    """Tool descriptor registered but no handler — configuration error."""
    desc = ToolDescriptor(name="orphan", description="d", parameters_schema={})
    client = InProcessClient(tools=[desc], handlers={})  # no handler for "orphan"

    with pytest.raises(KeyError):
        await client.call_tool("orphan", {})


@pytest.mark.asyncio
async def test_subscribe_list_changed_callback_invoked_on_fire():
    """subscribe_list_changed — callback fires when fire_list_changed called."""
    client = InProcessClient(tools=[], handlers={})

    fired = asyncio.Event()

    async def listener():
        fired.set()

    client.subscribe_list_changed(listener)
    await client.fire_list_changed()

    assert fired.is_set()


@pytest.mark.asyncio
async def test_subscribe_multiple_listeners_all_invoked():
    client = InProcessClient(tools=[], handlers={})

    count = {"n": 0}

    async def listener1():
        count["n"] += 1

    async def listener2():
        count["n"] += 10

    client.subscribe_list_changed(listener1)
    client.subscribe_list_changed(listener2)
    await client.fire_list_changed()

    assert count["n"] == 11