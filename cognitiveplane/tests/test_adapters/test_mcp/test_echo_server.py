"""Test echo_server stub — trivial MCP server for pipeline validation.

Spec §1.2 — C 子项目交付物验证。仅验证 echo_server 自身行为；
端到端管道验证 (MCPRegistry → ToolRegistry → ReActEngine) 在 Task 11。
"""
from __future__ import annotations

import pytest

from cognitiveplane.adapters.mcp.base import MCPServer
from cognitiveplane.adapters.mcp.stubs.echo_server import create_echo_server


@pytest.mark.asyncio
async def test_create_echo_server_returns_mcpserver():
    """create_echo_server 返回 MCPServer 实例且 name=echo_server。"""
    server = create_echo_server()
    assert isinstance(server, MCPServer)
    assert server.name == "echo_server"


@pytest.mark.asyncio
async def test_echo_server_lists_echo_tool():
    """list_tools 返回单个 echo_tool descriptor。"""
    server = create_echo_server()
    descriptors = await server.list_tools()
    assert len(descriptors) == 1
    assert descriptors[0].name == "echo_tool"


@pytest.mark.asyncio
async def test_echo_server_call_tool_returns_input_text():
    """call_tool('echo_tool', {text: 'hello'}) 返回 {echo: 'hello'}。"""
    server = create_echo_server()
    result = await server.call_tool("echo_tool", {"text": "hello"})
    assert result == {"echo": "hello"}


@pytest.mark.asyncio
async def test_echo_server_call_tool_with_empty_text():
    """空字符串也能 echo — 边界用例。"""
    server = create_echo_server()
    result = await server.call_tool("echo_tool", {"text": ""})
    assert result == {"echo": ""}