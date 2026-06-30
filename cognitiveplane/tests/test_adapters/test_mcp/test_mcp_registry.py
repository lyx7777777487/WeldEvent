"""Test MCPRegistry — 发现 + 三层判定 + 注册到 ToolRegistry.

Spec §3.1 (启动时数据流) + §3.3 (list_changed 事件流).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cognitiveplane.adapters.mcp.base import (
    MCPServer,
    ToolDescriptor,
)
from cognitiveplane.adapters.mcp.in_process import InProcessClient
from cognitiveplane.adapters.mcp.tool_policy_classifier import (
    MCPToolPolicyClassifier,
)
from cognitiveplane.control.event_log import EventLog
from cognitiveplane.control.mcp_registry import MCPRegistry
from cognitiveplane.control.tool_registry import ToolRegistry
from cognitiveplane.shared.types import CaseId


def _make_echo_server() -> MCPServer:
    desc = ToolDescriptor(
        name="echo_tool",
        description="Echo input back",
        parameters_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )

    async def echo_handler(text: str) -> dict:
        return {"echo": text}

    client = InProcessClient(tools=[desc], handlers={"echo_tool": echo_handler})
    return MCPServer(client=client, name="echo_server")


def _make_registry() -> MCPRegistry:
    classifier = MCPToolPolicyClassifier(yaml_path=None)
    event_log = EventLog(case_id=CaseId(value="test"))
    return MCPRegistry(classifier=classifier, event_log=event_log)


@pytest.mark.asyncio
async def test_register_server_pulls_tools_and_caches():
    """Spec §3.1 step 5 — register_server 拉工具列表 + 缓存判定结果。"""
    registry = _make_registry()
    server = _make_echo_server()

    await registry.register_server(server)

    cached = registry.list_cached_tools()
    assert len(cached) == 1
    assert cached[0][0].name == "echo_tool"
    # echo_tool 无 annotations, 不匹配 read/write 前缀 → 保守 Tier-B
    assert cached[0][1].reason == "prefix:unknown:conservative"


@pytest.mark.asyncio
async def test_register_all_creates_adapters_in_tool_registry():
    """Spec §3.1 step 6 — register_all 把 MCPAdapter 注册到 ToolRegistry。"""
    registry = _make_registry()
    server = _make_echo_server()
    await registry.register_server(server)

    tool_registry = ToolRegistry(current_phase=4)
    await registry.register_all(tool_registry)

    definitions = tool_registry.get_llm_tool_definitions()
    names = [d["function"]["name"] for d in definitions]
    assert "echo_tool" in names


@pytest.mark.asyncio
async def test_register_all_adapter_is_callable_through_tool_registry():
    """端到端片段 — ToolRegistry 能调到 MCPAdapter。"""
    registry = _make_registry()
    server = _make_echo_server()
    await registry.register_server(server)

    tool_registry = ToolRegistry(current_phase=4)
    await registry.register_all(tool_registry)

    result = await tool_registry._tools["echo_tool"].execute(text="hello")

    assert result.error is None
    assert result.output == {"echo": "hello"}


@pytest.mark.asyncio
async def test_register_multiple_servers():
    """多个 server 都能注册到同一个 ToolRegistry。"""
    registry = _make_registry()

    # server 1: echo
    echo_server = _make_echo_server()
    await registry.register_server(echo_server)

    # server 2: ping
    ping_desc = ToolDescriptor(
        name="ping_tool",
        description="Returns pong",
        parameters_schema={},
    )

    async def ping_handler() -> dict:
        return {"pong": True}

    ping_client = InProcessClient(tools=[ping_desc], handlers={"ping_tool": ping_handler})
    ping_server = MCPServer(client=ping_client, name="ping_server")
    await registry.register_server(ping_server)

    tool_registry = ToolRegistry(current_phase=4)
    await registry.register_all(tool_registry)

    names = [d["function"]["name"] for d in tool_registry.get_llm_tool_definitions()]
    assert "echo_tool" in names
    assert "ping_tool" in names


@pytest.mark.asyncio
async def test_list_changed_adds_new_tool():
    """Spec §3.3 — list_changed 后新增工具出现在 ToolRegistry。"""
    registry = _make_registry()
    server = _make_echo_server()
    await registry.register_server(server)

    tool_registry = ToolRegistry(current_phase=4)
    await registry.register_all(tool_registry)

    assert "ping_tool" not in [d["function"]["name"] for d in tool_registry.get_llm_tool_definitions()]

    # 模拟 server 端工具列表变更：替换 client 的 tools + handlers，触发 list_changed
    in_process_client = server._client  # type: ignore[attr-defined]
    ping_desc = ToolDescriptor(name="ping_tool", description="p", parameters_schema={})

    async def ping_handler() -> dict:
        return {"pong": True}

    in_process_client._tools = [ping_desc]  # type: ignore[attr-defined]
    in_process_client._handlers = {"ping_tool": ping_handler}  # type: ignore[attr-defined]
    await in_process_client.fire_list_changed()

    names = [d["function"]["name"] for d in tool_registry.get_llm_tool_definitions()]
    assert "ping_tool" in names
    # echo_tool 在新工具列表里没了 → 下线
    assert "echo_tool" not in names


@pytest.mark.asyncio
async def test_list_changed_removes_offline_tool():
    """Spec §3.3 — list_changed 后下线工具从 ToolRegistry 移除。"""
    registry = _make_registry()
    server = _make_echo_server()
    await registry.register_server(server)

    tool_registry = ToolRegistry(current_phase=4)
    await registry.register_all(tool_registry)
    assert "echo_tool" in [d["function"]["name"] for d in tool_registry.get_llm_tool_definitions()]

    # 清空 server 的工具列表
    in_process_client = server._client  # type: ignore[attr-defined]
    in_process_client._tools = []  # type: ignore[attr-defined]
    in_process_client._handlers = {}  # type: ignore[attr-defined]
    await in_process_client.fire_list_changed()

    assert "echo_tool" not in [d["function"]["name"] for d in tool_registry.get_llm_tool_definitions()]