"""MCP 协议消息层 — JSON-RPC 2.0 消息封装。

来源: 原 executionplane/mcp/protocol.py，提升至 shared 供 L1/L3 共享。

MCP (Model Context Protocol) 基于 JSON-RPC 2.0。
本模块封装协议消息构造/解析，与传输层（stdio/HTTP）解耦。

协议规范:
  - initialize: 客户端握手，交换 capabilities
  - tools/list: 列出 server 暴露的工具
  - tools/call: 调用指定工具

消息类型:
  - request: {"jsonrpc":"2.0","id":N,"method":"...","params":{...}}
  - response: {"jsonrpc":"2.0","id":N,"result":{...}} 或 {"jsonrpc":"2.0","id":N,"error":{...}}
  - notification: {"jsonrpc":"2.0","method":"...","params":{...}}  (无 id)

Source: https://spec.modelcontextprotocol.io/
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


# ── JSON-RPC 2.0 错误码 ──

class RPCErrorCode:
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    # MCP 扩展错误码（-32000 ~ -32099 应用自定义）
    TOOL_NOT_FOUND = -32001
    TOOL_EXECUTION_ERROR = -32002


@dataclass
class RPCError:
    """JSON-RPC 错误对象。"""
    code: int
    message: str
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            d["data"] = self.data
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RPCError":
        return cls(
            code=d.get("code", RPCErrorCode.INTERNAL_ERROR),
            message=d.get("message", "Unknown error"),
            data=d.get("data"),
        )


# ── 消息构造 ──

class MCPRequestBuilder:
    """构造 MCP JSON-RPC 请求消息。"""

    def __init__(self) -> None:
        self._next_id: int = 1

    def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            msg["params"] = params
        self._next_id += 1
        return msg

    def initialize(self, client_name: str = "weldevent-shared", client_version: str = "1.0.0") -> dict[str, Any]:
        """构造 initialize 请求 — 握手并交换 capabilities。"""
        return self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": client_name, "version": client_version},
        })

    def list_tools(self) -> dict[str, Any]:
        """构造 tools/list 请求 — 列出 server 暴露的所有工具。"""
        return self._request("tools/list", {})

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """构造 tools/call 请求 — 调用指定工具。"""
        return self._request("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })


# ── 消息解析 ──

@dataclass
class MCPResponse:
    """MCP JSON-RPC 响应解析结果。"""
    id: int | None
    result: Any = None
    error: RPCError | None = None
    is_error: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MCPResponse":
        return cls(
            id=d.get("id"),
            result=d.get("result"),
            error=RPCError.from_dict(d["error"]) if "error" in d else None,
            is_error="error" in d,
        )


def parse_response(raw: str | bytes) -> MCPResponse:
    """解析 JSON-RPC 响应字符串。

    Args:
        raw: JSON 字符串或 bytes

    Returns:
        MCPResponse 解析结果

    Raises:
        ValueError: JSON 解析失败或不符合 JSON-RPC 2.0 规范
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON-RPC response: {e}") from e
    if not isinstance(d, dict) or d.get("jsonrpc") != "2.0":
        raise ValueError(f"Not a JSON-RPC 2.0 message: {d}")
    return MCPResponse.from_dict(d)


# ── 工具描述数据结构 ──

@dataclass
class ToolDescriptor:
    """MCP 工具描述（tools/list 返回的单个工具）。

    这是协议层标准格式，字段名遵循 MCP spec（inputSchema）。
    各层业务 ABC 若需不同字段名（如 cognitiveplane 用 parameters_schema），
    由该层 adapter 自行转换。
    """
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolDescriptor":
        return cls(
            name=d.get("name", ""),
            description=d.get("description", ""),
            input_schema=d.get("inputSchema", {}),
        )


# ── 工具调用结果 ──

@dataclass
class ToolCallResult:
    """MCP tools/call 返回结果。"""
    content: list[dict[str, Any]] = field(default_factory=list)
    is_error: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolCallResult":
        return cls(
            content=d.get("content", []),
            is_error=d.get("isError", False),
        )

    @property
    def text_content(self) -> str:
        """提取所有 text 类型 content 拼接成字符串。"""
        parts: list[str] = []
        for item in self.content:
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts)

    @property
    def parsed_content(self) -> Any:
        """尝试把 text_content 解析为 JSON，失败则返回原始字符串。"""
        text = self.text_content
        if not text:
            return None
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text


__all__ = [
    "RPCErrorCode",
    "RPCError",
    "MCPRequestBuilder",
    "MCPResponse",
    "parse_response",
    "ToolDescriptor",
    "ToolCallResult",
]
