"""L3 工业执行 MCP 基础设施。

与 cognitiveplane/adapters/mcp（L1 认知 MCP）独立，不共享实现。
boundary-pinning §1.1: "认知 MCP vs 工业执行 MCP 不共享实现"。

当前提供（纯协议层，不绑定具体业务）:
  - StdioMCPClient: 本地子进程通信
  - HTTPMCPClient: 远程 HTTP 通信

具体外部 MCP 服务的接入见 executionplane/integrations/（如 label_studio/）。

"""
from __future__ import annotations

from .client import (
    MCPClient,
    MCPConnectionError,
    MCPError,
    MCPToolError,
    HTTPMCPClient,
    StdioMCPClient,
)
from .protocol import (
    MCPRequestBuilder,
    MCPResponse,
    RPCError,
    RPCErrorCode,
    ToolCallResult,
    ToolDescriptor,
    parse_response,
)

__all__ = [
    "MCPClient",
    "StdioMCPClient",
    "HTTPMCPClient",
    "MCPError",
    "MCPConnectionError",
    "MCPToolError",
    "MCPRequestBuilder",
    "MCPResponse",
    "RPCError",
    "RPCErrorCode",
    "ToolCallResult",
    "ToolDescriptor",
    "parse_response",
]
