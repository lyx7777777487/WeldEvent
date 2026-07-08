"""Control plane — tool & MCP registry.

Re-exports for backward compatibility.
"""
from cognitiveplane.control.registry.tool_registry import ToolRegistry
from cognitiveplane.control.registry.mcp_registry import MCPRegistry
from cognitiveplane.control.registry.tool_failure_reflector import (
    ReflectionResult,
    reflect as reflect_schema_failure,
)

# mcp_server 的 build_mcp_server_and_app / mount_mcp_server 不 re-export
# （它们是装配函数，调用方直接从 registry.mcp_server import）

__all__ = ["ToolRegistry", "MCPRegistry", "ReflectionResult", "reflect_schema_failure"]
