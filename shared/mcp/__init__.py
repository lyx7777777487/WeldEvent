"""MCP 协议消息层 + 传输层 — 跨 L1/L3 共享的中立基础设施。

来源: 原 executionplane/mcp/{protocol,client}.py 提升至 shared。

设计:
  - shared.mcp.protocol: JSON-RPC 2.0 消息构造/解析 + 协议数据结构
    (MCPRequestBuilder / MCPResponse / parse_response / RPCError /
     ToolDescriptor / ToolCallResult)
  - shared.mcp.client: 传输层 ABC + 具体实现
    (MCPClient ABC + StdioMCPClient + HTTPMCPClient + 异常类)

各层业务 ABC 不放这里:
  - cognitiveplane.adapters.mcp.base.MCPClient / MCPServer / MCPAdapter
    是 L1 业务 ABC（含 subscribe_list_changed、policy 判定等），保留在 L1
  - executionplane.integrations.label_studio.client.LabelStudioMCPClient
    是 L3 业务封装（含 9 个标注业务方法），保留在 L3

L1 使用方式:
  from shared.mcp.client import HTTPMCPClient
  # 在 cognitiveplane/adapters/mcp/label_studio_server.py 里
  # 写 adapter 把 HTTPMCPClient 包成 cognitiveplane MCPClient ABC

L3 使用方式（向后兼容）:
  from shared.mcp.client import HTTPMCPClient
  # 或直接 from shared.mcp.client import HTTPMCPClient
"""
from __future__ import annotations

from shared.mcp.client import (
    HTTPMCPClient,
    MCPClient,
    MCPConnectionError,
    MCPError,
    MCPToolError,
    StdioMCPClient,
)
from shared.mcp.protocol import (
    MCPRequestBuilder,
    MCPResponse,
    RPCError,
    RPCErrorCode,
    ToolCallResult,
    ToolDescriptor,
    parse_response,
)

__all__ = [
    # 协议层
    "MCPRequestBuilder",
    "MCPResponse",
    "RPCError",
    "RPCErrorCode",
    "ToolCallResult",
    "ToolDescriptor",
    "parse_response",
    # 传输层
    "MCPClient",
    "StdioMCPClient",
    "HTTPMCPClient",
    "MCPError",
    "MCPConnectionError",
    "MCPToolError",
]
