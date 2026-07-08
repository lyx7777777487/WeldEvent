"""L1 Label Studio MCP server 包装 — 让标注工具对 Brain LLM 可见。

设计:
  - LabelStudioConfig: 标注平台连接配置（URL/认证/超时），从环境变量加载
  - LabelStudioMCPClientAdapter: 适配 shared HTTPMCPClient 到 cognitiveplane
    MCPClient ABC（call_tool 返回 dict，list_tools 返回 cognitiveplane
    ToolDescriptor 带 parameters_schema/annotations）
  - create_label_studio_mcp_server(): async 工厂，自动登录 + connect + initialize，
    返回 MCPServer（phase_override=3 让标注工具对 LLM 可见）

为何 L1 独立实现而不复用 executionplane/integrations/label_studio/client.py:
  - L3 的 LabelStudioMCPClient 含 9 个业务方法（list_datasets/get_dataset/...），
    是给 L3 activity 用的类型安全 API
  - L1 不需要业务方法封装 — LLM 通过 MCPAdapter 直接调工具名（如 "list_datasets"），
    adapter 委托 client.call_tool(name, args) 即可
  - L1 的 MCPClient ABC 接口（call_tool 返回 dict）与 L3 不同（返回 ToolCallResult）
  - 避免 L1 依赖 L3（boundary-pinning §1.5）

共享的是传输层（shared.mcp.client.HTTPMCPClient），不是业务封装。
"""
from __future__ import annotations

import logging
from typing import Any

from cognitiveplane.adapters.mcp.base import (
    MCPAnnotations,
    MCPClient,
    MCPServer,
    ToolDescriptor,
)
from shared.labelstudio.auth import login_labelstudio
from shared.labelstudio.config import LabelStudioConfig
from shared.mcp.client import HTTPMCPClient

logger = logging.getLogger(__name__)


# ── L1 适配器 ──

class LabelStudioMCPClientAdapter(MCPClient):
    """L1 认知层适配 — 把 shared HTTPMCPClient 包成 cognitiveplane MCPClient ABC。

    接口转换:
      - list_tools(): shared ToolDescriptor (input_schema) → cognitiveplane
        ToolDescriptor (parameters_schema + annotations)
      - call_tool(): shared ToolCallResult → dict（提取 parsed_content）
      - subscribe_list_changed(): no-op（HTTP 传输不支持 server 主动推送）

    业务方法（list_datasets/get_dataset 等）不需要在此实现 — LLM 通过
    MCPAdapter 直接调工具名，adapter 委托此 client 的 call_tool(name, args)。
    """

    def __init__(self, http_client: HTTPMCPClient) -> None:
        self._http = http_client

    async def list_tools(self) -> list[ToolDescriptor]:
        shared_tools = await self._http.list_tools()
        # 字段名映射: shared input_schema → cognitiveplane parameters_schema
        # Label Studio MCP server 不发 annotations，用空 MCPAnnotations 占位
        # （classifier 会走前缀启发式判定 policy）
        return [
            ToolDescriptor(
                name=t.name,
                description=t.description,
                parameters_schema=t.input_schema,
                annotations=MCPAnnotations(),
            )
            for t in shared_tools
        ]

    async def call_tool(self, name: str, arguments: dict) -> dict:
        result = await self._http.call_tool(name, arguments)
        parsed = result.parsed_content
        if isinstance(parsed, dict):
            # 标注 MCP server 返回结构化 JSON（如 {"datasets": [...], "total": N}）
            return parsed
        if isinstance(parsed, list):
            return {"items": parsed}
        # 纯文本响应（如 "作业创建成功，jobId: xxx"）
        return {"raw": result.text_content, "is_error": result.is_error}

    def subscribe_list_changed(self, callback) -> None:
        """HTTP 传输不支持 list_changed 主动推送 — no-op。"""
        pass


# ── 工厂 ──

async def create_label_studio_mcp_server(
    config: LabelStudioConfig | None = None,
) -> MCPServer:
    """构造 Label Studio MCP server 实例（含自动登录 + connect + initialize）。

    流程:
      1. 从环境变量加载 config（或用传入的 config）
      2. 若有 username/password → 自动登录获取 JWT
      3. 创建 HTTPMCPClient（shared 传输层）
      4. connect + initialize（MCP 握手）
      5. 包装成 LabelStudioMCPClientAdapter（L1 MCPClient ABC）
      6. 返回 MCPServer（phase_override=3 让标注工具对 LLM 可见）

    Args:
        config: 可选配置，None 则从环境变量加载

    Returns:
        MCPServer 实例，phase_override=3

    Raises:
        MCPError: 登录失败或 MCP 握手失败
    """
    if config is None:
        config = LabelStudioConfig.from_env()

    # 构造认证 headers
    headers: dict[str, str] = {}
    if config.mcp_token:
        # 直接用 token，跳过自动登录
        headers["Authorization"] = f"Bearer {config.mcp_token}"
    elif config.mcp_username and config.mcp_password:
        # 自动登录获取 JWT
        token = await login_labelstudio(config)
        headers["Authorization"] = f"Bearer {token}"
    # 否则匿名访问（headers 为空）

    http_client = HTTPMCPClient(
        url=config.server_url,
        headers=headers or None,
        timeout=config.timeout,
    )
    await http_client.connect()
    await http_client.initialize()

    adapter = LabelStudioMCPClientAdapter(http_client)
    # phase_override=3: 让标注工具对 LLM 可见（ToolRegistry.current_phase=3）
    # 其他工业 MCP server（如 defect_detection）默认 phase=4 仍隐藏
    server = MCPServer(client=adapter, name="label_studio", phase_override=3)
    logger.info(
        "Label Studio MCP server ready: url=%s, tools will be registered",
        config.server_url,
    )
    return server


__all__ = [
    "LabelStudioConfig",
    "LabelStudioMCPClientAdapter",
    "create_label_studio_mcp_server",
]
