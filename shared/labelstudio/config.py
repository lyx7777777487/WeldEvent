"""Label Studio 配置 — 跨 L1/L3 共享的连接配置。

设计:
  - LabelStudioConfig: 标注平台 MCP server v2.0 连接配置（URL/认证/超时）
  - 从环境变量加载（from_env），构造认证 headers（get_headers）

为何放 shared: L1 (cognitiveplane) 和 L3 (executionplane) 各有一份完全
相同的 LabelStudioConfig，去重后两边共用一份，避免字段/环境变量名漂移。

不放业务方法（list_datasets 等）— 那些保留在各层:
  - L1 通过 MCPAdapter 直接调工具名（返回 dict）
  - L3 通过 LabelStudioMCPClient 封装类型安全 API（返回 ToolCallResult）
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class LabelStudioConfig:
    """标注平台 MCP server v2.0 连接配置。

    环境变量名相同，部署时一份配置两边可用:
      LABEL_STUDIO_MCP_URL      — MCP server URL
      LABEL_STUDIO_MCP_TOKEN    — JWT Token（优先，跳过自动登录）
      LABEL_STUDIO_MCP_USERNAME — 用户名（与 PASSWORD 一起自动登录）
      LABEL_STUDIO_MCP_PASSWORD — 密码
      LABEL_STUDIO_MCP_TIMEOUT  — 超时秒数
    """
    server_url: str = "http://172.16.11.11:8079/api/mcp"
    mcp_token: str = ""
    mcp_username: str = "admin"
    mcp_password: str = "admin123"
    timeout: float = 60.0

    @classmethod
    def from_env(cls) -> "LabelStudioConfig":
        """从环境变量加载配置。"""
        return cls(
            server_url=os.environ.get(
                "LABEL_STUDIO_MCP_URL", "http://172.16.11.11:8079/api/mcp"
            ),
            mcp_token=os.environ.get("LABEL_STUDIO_MCP_TOKEN", ""),
            mcp_username=os.environ.get("LABEL_STUDIO_MCP_USERNAME", "admin"),
            mcp_password=os.environ.get("LABEL_STUDIO_MCP_PASSWORD", "admin123"),
            timeout=float(os.environ.get("LABEL_STUDIO_MCP_TIMEOUT", "60")),
        )

    def get_headers(self) -> dict[str, str]:
        """构造 MCP HTTP 请求所需的认证 headers。"""
        headers: dict[str, str] = {}
        if self.mcp_token:
            headers["Authorization"] = f"Bearer {self.mcp_token}"
        return headers


__all__ = ["LabelStudioConfig"]
