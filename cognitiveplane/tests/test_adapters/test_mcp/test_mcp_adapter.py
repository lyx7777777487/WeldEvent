"""Test MCPAdapter — adapts a single MCP tool to BrainTool.

Spec: §2.4 + §3.2 steps 11-15.
"""
from __future__ import annotations

import pytest

from cognitiveplane.adapters.mcp.base import (
    MCPAdapter,
    MCPPolicyDecision,
    ToolDescriptor,
)
from cognitiveplane.control.tools import BrainTool


@pytest.mark.asyncio
async def test_adapter_is_braintool():
    """C.1 验收 — MCPAdapter 必须是 BrainTool 子类。"""
    desc = ToolDescriptor(name="echo_tool", description="d", parameters_schema={})
    policy = MCPPolicyDecision(auto_approve=False, tier="B")
    # MCPServer 构造需要 MCPClient；用 None 占位因为测试不调 execute
    adapter = MCPAdapter(server=None, descriptor=desc, policy=policy)  # type: ignore[arg-type]
    assert isinstance(adapter, BrainTool)


def test_adapter_name_description_schema_from_descriptor():
    desc = ToolDescriptor(
        name="search_docs",
        description="Search documents",
        parameters_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )
    policy = MCPPolicyDecision(auto_approve=True, tier="A")
    adapter = MCPAdapter(server=None, descriptor=desc, policy=policy)  # type: ignore[arg-type]

    assert adapter.name == "search_docs"
    assert adapter.description == "Search documents"
    assert adapter.parameters_schema == {
        "type": "object",
        "properties": {"q": {"type": "string"}},
    }


def test_adapter_to_function_definition_format():
    """function definition 与内部工具格式一致 — LLM 不感知来源。"""
    desc = ToolDescriptor(
        name="echo_tool",
        description="Echo input",
        parameters_schema={"type": "object", "properties": {}},
    )
    policy = MCPPolicyDecision(auto_approve=False, tier="B")
    adapter = MCPAdapter(server=None, descriptor=desc, policy=policy)  # type: ignore[arg-type]

    definition = adapter.to_function_definition()

    assert definition == {
        "type": "function",
        "function": {
            "name": "echo_tool",
            "description": "Echo input",
            "parameters": {"type": "object", "properties": {}},
        },
    }


@pytest.mark.asyncio
async def test_adapter_execute_delegates_to_server_call_tool():
    """Spec §3.2 step 12 — adapter.execute 委托 server.call_tool。"""
    desc = ToolDescriptor(name="echo_tool", description="d", parameters_schema={})
    policy = MCPPolicyDecision(auto_approve=False, tier="B")

    class FakeServer:
        def __init__(self):
            self.call_log: list[tuple[str, dict]] = []

        async def call_tool(self, name, arguments):
            self.call_log.append((name, arguments))
            return {"echo": arguments.get("text")}

    server = FakeServer()
    adapter = MCPAdapter(server=server, descriptor=desc, policy=policy)  # type: ignore[arg-type]

    result = await adapter.execute(text="hello")

    assert result.error is None
    assert result.output == {"echo": "hello"}
    assert server.call_log == [("echo_tool", {"text": "hello"})]


@pytest.mark.asyncio
async def test_adapter_execute_wraps_call_failure_as_tool_result_error():
    """Spec §5 — call_tool 抛异常时返回 ToolResult(error=...), 不抛到上层。"""
    desc = ToolDescriptor(name="bad_tool", description="d", parameters_schema={})
    policy = MCPPolicyDecision(auto_approve=False, tier="B")

    class FailingServer:
        async def call_tool(self, name, arguments):
            raise RuntimeError("connection refused")

    server = FailingServer()
    adapter = MCPAdapter(server=server, descriptor=desc, policy=policy)  # type: ignore[arg-type]

    result = await adapter.execute(text="x")

    assert result.error is not None
    assert "mcp_call_failed" in result.error
    assert "connection refused" in result.error


def test_adapter_policy_metadata_exposed():
    """Spec §4 — policy 缓存到 adapter，Hook 拦截时可直接读。"""
    desc = ToolDescriptor(name="echo_tool", description="d", parameters_schema={})
    policy = MCPPolicyDecision(
        auto_approve=False, tier="B", require_reason=True, reason="prefix:unknown:conservative"
    )
    adapter = MCPAdapter(server=None, descriptor=desc, policy=policy)  # type: ignore[arg-type]

    assert adapter.policy == policy
    assert adapter.policy.reason == "prefix:unknown:conservative"