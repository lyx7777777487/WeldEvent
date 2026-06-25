"""echo_server — trivial MCP server stub for pipeline validation.

Spec §1.2 — C 子项目交付物。仅用于验证 MCPClient → MCPServer →
MCPAdapter → MCPRegistry → ToolRegistry → ReActEngine 整条管道。
不含业务语义，未来真实认知 MCP（文档检索等）按需接入。
"""

from __future__ import annotations

from cognitiveplane.adapters.mcp.base import MCPServer, ToolDescriptor
from cognitiveplane.adapters.mcp.in_process import InProcessClient

_ECHO_TOOL_DESCRIPTOR = ToolDescriptor(
    name="echo_tool",
    description="Echoes the input text back. Trivial stub for pipeline validation.",
    parameters_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to echo back"},
        },
        "required": ["text"],
    },
    # 无 annotations — 三层判定落入保守 Tier-B (spec §4.3)
)


async def _echo_handler(text: str) -> dict:
    return {"echo": text}


def create_echo_server() -> MCPServer:
    """构造 echo_server MCPServer 实例。"""
    client = InProcessClient(
        tools=[_ECHO_TOOL_DESCRIPTOR],
        handlers={"echo_tool": _echo_handler},
    )
    return MCPServer(client=client, name="echo_server")