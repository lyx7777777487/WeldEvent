"""Test MCPClient ABC contract.

Spec: §2.4 — MCPClient is the transport-layer ABC with three abstract methods.
"""
from __future__ import annotations

import inspect

import pytest

from cognitiveplane.adapters.mcp.base import MCPClient, ToolDescriptor


def test_mcp_client_is_abc():
    """MCPClient cannot be instantiated directly."""
    with pytest.raises(TypeError):
        MCPClient()  # type: ignore[abstract]


def test_mcp_client_abstract_methods():
    """All three methods are abstract — subclasses must implement them."""
    abstract_methods = {
        name
        for name, member in inspect.getmembers(MCPClient)
        if getattr(member, "__isabstractmethod__", False)
    }
    assert abstract_methods == {"list_tools", "call_tool", "subscribe_list_changed"}


def test_mcp_client_subclass_must_implement_all():
    """Subclass missing any abstract method still abstract."""
    class Partial(MCPClient):
        async def list_tools(self):
            return []

    with pytest.raises(TypeError):
        Partial()  # type: ignore[abstract]


def test_mcp_client_full_subclass_instantiable():
    """Full implementation can be instantiated."""
    class Full(MCPClient):
        async def list_tools(self):
            return []

        async def call_tool(self, name, arguments):
            return {}

        def subscribe_list_changed(self, callback):
            pass

    client = Full()
    assert isinstance(client, MCPClient)