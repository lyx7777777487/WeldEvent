"""Label Studio 登录 — 跨 L1/L3 共享的 JWT 认证。

设计:
  - login_labelstudio(config): 使用 username/password 登录获取 JWT token
  - 从 MCP URL 推导 login URL（/api/mcp → /api/auth/login）

为何放 shared: L1 和 L3 各有一份完全相同的 _login 逻辑，去重后两边共用。
"""
from __future__ import annotations

import logging

import httpx

from shared.labelstudio.config import LabelStudioConfig
from shared.mcp.client import MCPError

logger = logging.getLogger(__name__)


async def login_labelstudio(config: LabelStudioConfig) -> str:
    """使用 username/password 登录 Label Studio 获取 JWT token。

    从 MCP URL 推导 login URL（/api/mcp → /api/auth/login）。

    Args:
        config: Label Studio 连接配置（需含 mcp_username/mcp_password）

    Returns:
        JWT token 字符串

    Raises:
        MCPError: 登录失败或响应中无 token
    """
    base = config.server_url.rstrip("/")
    login_url = base.replace("/api/mcp", "/api/auth/login")
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            login_url,
            json={
                "username": config.mcp_username,
                "password": config.mcp_password,
            },
        )
        resp.raise_for_status()
        body = resp.json()
        token = body.get("data", {}).get("token")
        if not token:
            raise MCPError(f"Login failed: no token in response: {body}")
        logger.info(
            "Label Studio login successful: user=%s", config.mcp_username
        )
        return token


__all__ = ["login_labelstudio"]
