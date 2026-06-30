"""MCP Server — 将 BrainTool 注册表桥接到 Model Context Protocol。

来源：MCP（Model Context Protocol）是 2026 年连接 AI 与工具/数据的开放标准。
文档：https://modelcontextprotocol.io/ | SDK：mcp 1.x（mcp.server.fastmcp）

本模块将 ToolRegistry 中所有 LLM 可见的 BrainTool 适配为 MCP Tool，
通过 FastMCP 以 SSE/HTTP transport 暴露，使任何 MCP 客户端
（Claude Desktop、Cursor、其他 Agent 系统）都能发现并调用这些工具。

设计：
  - BrainToolMCPAdapter：子类化 mcp.server.fastmcp.tools.Tool，覆盖 run()
    直接调 brain_tool.execute(**arguments)，绕过 fn_metadata 校验
    （ToolRegistry.execute 已有 schema 校验）。
  - build_mcp_server(tool_registry)：遍历 LLM 可见工具，注册到 FastMCP。
  - mount_mcp_server(app, tool_registry)：把 FastMCP 的 SSE transport
    挂载到 FastAPI 的 /api/v1/mcp 路径。

SSE transport 选择原因：与 FastAPI 共用端口，部署简单；stdio transport
需要独立进程，不适合与 L1 共存。
"""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.tools import Tool
from mcp.server.fastmcp.utilities.func_metadata import func_metadata

if TYPE_CHECKING:
    from cognitiveplane.control.tool_registry import ToolRegistry

logger = logging.getLogger("mcp_server")
if not logger.handlers:
    import sys
    _h = logging.StreamHandler(sys.stdout)
    _h.setLevel(logging.INFO)
    _h.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)
    logger.propagate = False


class BrainToolMCPAdapter(Tool):
    """将 BrainTool 适配为 MCP Tool。

    覆盖 run() 直接调用 brain_tool.execute(**arguments)，
    绕过 fn_metadata 的 Pydantic arg_model 校验。
    原因：BrainTool 的 parameters_schema 是自定义 JSON Schema，
    func_metadata 从函数签名（**kwargs）生成的 arg_model 与之不一致。
    ToolRegistry.execute 已有 jsonschema 校验，无需重复。
    """

    @classmethod
    def from_brain_tool(cls, brain_tool: Any) -> "BrainToolMCPAdapter":
        """从 BrainTool 构造 MCP Tool 适配器。

        brain_tool: 实现 name/description/parameters_schema/execute(**kwargs) 的对象。
        """
        bt = brain_tool  # 闭包捕获

        async def wrapper(**kwargs):
            result = await bt.execute(**kwargs)
            # BrainTool.execute 返回 ToolResult dataclass，MCP 期望可序列化 dict。
            # 调 to_json() 转 dict；有 error 时附上 error 字段。
            if hasattr(result, "to_json"):
                return result.to_json()
            return result

        fn_meta = func_metadata(wrapper)
        return cls.model_construct(
            fn=wrapper,
            name=bt.name,
            description=bt.description,
            parameters=bt.parameters_schema,
            fn_metadata=fn_meta,
            is_async=True,
            context_kwarg=None,
            annotations=None,
            icons=None,
            meta=None,
            title=None,
        )

    async def run(
        self,
        arguments: dict[str, Any],
        context: Any = None,
        convert_result: bool = False,
    ) -> Any:
        """直接调 BrainTool.execute，不经 fn_metadata 校验。

        arguments: MCP 客户端传入的参数 dict，已通过 list_tools 暴露的 inputSchema 校验。
        """
        return await self.fn(**arguments)


def build_mcp_server(tool_registry: "ToolRegistry") -> FastMCP:
    """从 ToolRegistry 构建 FastMCP server，注册所有 LLM 可见工具。

    返回的 FastMCP 实例可挂载到 FastAPI（SSE transport）或独立运行（stdio）。
    工具过滤：只注册 is_llm_visible 的工具（与 ReActEngine LLM 看到的工具集一致）。
    """
    mcp = FastMCP("weld-event-cognitive-plane")

    registered_count = 0
    for tool_name in tool_registry.list_llm_tools():
        brain_tool = tool_registry.get_tool(tool_name)
        if brain_tool is None:
            continue
        try:
            adapter = BrainToolMCPAdapter.from_brain_tool(brain_tool)
            # 直接注入到 ToolManager，绕过 add_tool 的函数签名解析
            mcp._tool_manager._tools[adapter.name] = adapter
            registered_count += 1
            logger.info("[MCP] registered tool: %s", adapter.name)
        except Exception as e:
            logger.warning("[MCP] failed to register tool %s: %s", tool_name, e)

    logger.info("[MCP] server built with %d tools", registered_count)
    return mcp


def build_mcp_server_and_app(tool_registry: "ToolRegistry") -> tuple[Any, FastMCP]:
    """构建 FastMCP server + streamable_http ASGI app。

    返回 (mcp_app, mcp_instance)：
      - mcp_app: Starlette ASGI app，需在 lifespan 中初始化 task group
      - mcp_instance: FastMCP 实例，供测试用

    调用方负责：
      1. 在 FastAPI lifespan 中 `async with mcp_app.lifespan(app):` 初始化 task group
      2. `app.mount("/api/v1/mcp", mcp_app)` 挂载到 FastAPI
    端点路径：/api/v1/mcp/mcp（mount 前缀 + 内部 /mcp 路由）
    """
    mcp = build_mcp_server(tool_registry)
    mcp_app = mcp.streamable_http_app()
    logger.info("[MCP] streamable_http app built (endpoint will be /api/v1/mcp/mcp)")
    return mcp_app, mcp


def mount_mcp_server(app: Any, mcp_app: Any) -> None:
    """把已构建的 MCP ASGI app 挂载到 FastAPI app。

    分两步设计（build + mount）是为了让调用方在 lifespan 中
    先初始化 mcp_app 的 task group，再挂载。
    """
    app.mount("/api/v1/mcp", mcp_app)
    logger.info("[MCP] mounted at /api/v1/mcp/mcp (streamable_http transport)")
