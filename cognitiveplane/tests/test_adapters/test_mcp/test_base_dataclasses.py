"""Test ToolDescriptor + MCPAnnotations dataclass contracts.

Spec: §2.3 — frozen dataclasses, used as MCPClient.list_tools() return type.
"""
from __future__ import annotations

import dataclasses

from cognitiveplane.adapters.mcp.base import (
    MCPAnnotations,
    MCPPolicyDecision,
    ToolDescriptor,
)


def test_tool_descriptor_minimal():
    """ToolDescriptor with only required fields — annotations default None."""
    desc = ToolDescriptor(
        name="echo_tool",
        description="Echoes input back",
        parameters_schema={"type": "object", "properties": {}},
    )
    assert desc.name == "echo_tool"
    assert desc.description == "Echoes input back"
    assert desc.parameters_schema == {"type": "object", "properties": {}}
    assert desc.annotations is None


def test_tool_descriptor_with_annotations():
    desc = ToolDescriptor(
        name="search_docs",
        description="Search documents",
        parameters_schema={},
        annotations=MCPAnnotations(readOnlyHint=True),
    )
    assert desc.annotations is not None
    assert desc.annotations.readOnlyHint is True
    assert desc.annotations.destructiveHint is None
    assert desc.annotations.openWorldHint is None


def test_tool_descriptor_frozen():
    """Frozen — cannot mutate after construction."""
    desc = ToolDescriptor(name="x", description="x", parameters_schema={})
    try:
        desc.name = "y"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    assert False, "Expected FrozenInstanceError"


def test_mcp_policy_decision_defaults():
    """MCPPolicyDecision — require_reason defaults False, reason defaults empty."""
    decision = MCPPolicyDecision(auto_approve=True, tier="A")
    assert decision.auto_approve is True
    assert decision.tier == "A"
    assert decision.require_reason is False
    assert decision.reason == ""