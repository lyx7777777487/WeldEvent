"""InProcessClient — Phase 3 唯一 MCP 传输实现。

Spec §2.4 — call_tool 直接调 Python 函数；Phase 5+ 加 StdioClient 时
call_tool 改成 JSON-RPC over stdin/stdout，上层（MCPServer /
MCPAdapter / MCPRegistry / ToolRegistry / ReActEngine）全部不变。

list_changed 事件由 server 实现主动调用 fire_list_changed 触发 —
真实 MCP server 是协议通知，stub 是显式调用。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from cognitiveplane.adapters.mcp.base import MCPClient, ToolDescriptor

ToolHandler = Callable[..., Awaitable[dict[str, Any]]]
ListChangedCallback = Callable[[], Awaitable[None]]


class InProcessClient(MCPClient):
    """In-process transport — tools are Python functions.

    tools: 该 server 暴露的工具描述列表
    handlers: tool_name → async callable，返回 dict 结果
    """

    def __init__(
        self,
        tools: list[ToolDescriptor],
        handlers: dict[str, ToolHandler],
    ) -> None:
        self._tools = tools
        self._handlers = handlers
        self._list_changed_listeners: list[ListChangedCallback] = []

    async def list_tools(self) -> list[ToolDescriptor]:
        return list(self._tools)

    async def call_tool(self, name: str, arguments: dict) -> dict:
        if name not in self._handlers:
            raise KeyError(f"no handler registered for tool '{name}'")
        return await self._handlers[name](**arguments)

    def subscribe_list_changed(
        self, callback: ListChangedCallback
    ) -> None:
        self._list_changed_listeners.append(callback)

    async def fire_list_changed(self) -> None:
        """Stub 专用 — 显式触发 list_changed 事件。

        真实 MCP server（StdioClient/SSEClient）由协议通知触发，
        不需要此方法。stub 用它模拟 server 端工具列表变更。
        """
        for cb in self._list_changed_listeners:
            await cb()