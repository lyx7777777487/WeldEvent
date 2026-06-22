"""shared.mcp_tools — MCP 调用工具层（L1 和 L3 共享）。

中性位置，不依赖 controlplane / executionplane / l1_agent 任何一层。
"""

from .annotate_tool import annotate, McpBusinessError, McpInfraError
from .mcp_session import mcp_session

__all__ = ["annotate", "mcp_session", "McpBusinessError", "McpInfraError"]