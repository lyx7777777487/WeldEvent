"""MCP 基础数据结构 + ABC 定义。

Spec: docs/superpowers/specs/2026-06-25-phase3-c-mcp-infrastructure-design.md §2.

L1 认知 MCP 基础设施 — 仅抽象层 + InProcess 传输。
真实认知 MCP（文档检索等）按需接入；工业执行 MCP（detect_defects 等）
在 executionplane 仓库，不在本包。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from cognitiveplane.control.tools import BrainTool, ToolResult


@dataclass(frozen=True)
class MCPAnnotations:
    """MCP 协议 annotations 字段 — 工具自声明安全属性。

    Spec §4.2.1 第二层判定来源。None 表示工具未声明该 hint。
    """
    readOnlyHint: bool | None = None
    destructiveHint: bool | None = None
    openWorldHint: bool | None = None


@dataclass(frozen=True)
class ToolDescriptor:
    """MCP 工具描述 — MCPClient.list_tools() 返回。

    name: 工具名（如 "echo_tool" / 未来的 "search_docs"）
    description: 给 LLM 看的工具说明
    parameters_schema: JSON Schema，给 LLM function calling 用
    annotations: §4.2.1 第二层判定来源，None 表示工具未声明
    """
    name: str
    description: str
    parameters_schema: dict
    annotations: MCPAnnotations | None = None


@dataclass(frozen=True)
class MCPPolicyDecision:
    """三层 ToolPolicy 判定结果 — 缓存到 MCPAdapter.metadata。

    Spec §4 — 判定顺序: YAML override > annotations > 前缀启发式 > 保守 Tier-B。
    """
    auto_approve: bool
    tier: str  # "A" (retriable) / "B" (precise, require approval)
    require_reason: bool = False
    reason: str = ""


# MCPClient / MCPServer / MCPAdapter 定义在后续 task 加入此文件


class MCPClient(ABC):
    """MCP 传输层抽象。

    Spec §2.4 — Phase 3 只有 InProcessClient；Phase 5+ 加 StdioClient/SSEClient。
    上层（MCPServer / MCPAdapter / MCPRegistry / ToolRegistry / ReActEngine）
    不感知传输实现。
    """

    @abstractmethod
    async def list_tools(self) -> list[ToolDescriptor]:
        """返回该 server 暴露的所有工具描述。"""

    @abstractmethod
    async def call_tool(self, name: str, arguments: dict) -> dict:
        """调用工具，返回结构化结果。"""

    @abstractmethod
    def subscribe_list_changed(
        self, callback: Callable[[], Awaitable[None]]
    ) -> None:
        """订阅 tools/list_changed 事件。

        callback 在工具列表变更时被调用（异步）。MCPRegistry 用此触发重判。
        """


class MCPServer:
    """一个 MCP server 的连接包装。

    Spec §2.4 — 持有 MCPClient，暴露 list_tools / call_tool / on_list_changed。
    Server 本身无状态 — 每次调用都委托 client。

    phase_override: 当非 None 时，MCPRegistry 创建 adapter 时会传入此值
    覆盖 MCPAdapter 默认 phase=4。用于让特定 server 的工具对 LLM 可见
    （如 Label Studio 标注工具 phase=3 可见，其他工业 MCP 默认隐藏）。
    """

    def __init__(
        self,
        client: MCPClient,
        name: str,
        phase_override: int | None = None,
    ) -> None:
        self._client = client
        self._name = name
        self._phase_override = phase_override

    @property
    def name(self) -> str:
        return self._name

    @property
    def phase_override(self) -> int | None:
        """若非 None，MCPRegistry 创建 adapter 时用此值覆盖默认 phase。"""
        return self._phase_override

    async def list_tools(self) -> list[ToolDescriptor]:
        return await self._client.list_tools()

    async def call_tool(self, name: str, arguments: dict) -> dict:
        return await self._client.call_tool(name, arguments)

    def on_list_changed(
        self, callback: Callable[[], Awaitable[None]]
    ) -> None:
        """订阅 list_changed 事件 — 转发给 client.subscribe_list_changed。"""
        self._client.subscribe_list_changed(callback)


class MCPAdapter(BrainTool):
    """适配单个 MCP 工具为 BrainTool。

    Spec §2.4 + §3.2 — 无状态，每次 execute 都委托 server.call_tool。
    LLM 通过 ToolRegistry 看到的接口与内部工具完全一致。

    phase 默认 4（工业/外部 MCP 工具默认对 LLM 不可见，等 ToolPool/Activity
    边界就绪后再放行）。MCPServer.phase_override 可在构造时传入覆盖此默认值，
    让特定 server 的工具提前对 LLM 可见（如 Label Studio 标注工具 phase=3）。
    """

    phase = 4

    def __init__(
        self,
        server: MCPServer,
        descriptor: ToolDescriptor,
        policy: MCPPolicyDecision,
    ) -> None:
        self._server = server
        self._descriptor = descriptor
        self._policy = policy
        # 若 server 声明了 phase_override，覆盖默认 phase
        # 让该 server 的工具对 LLM 可见（如 Label Studio 标注工具）
        # getattr 安全访问：测试可能传 server=None 或 FakeServer
        phase_override = getattr(server, "phase_override", None)
        if phase_override is not None:
            self.phase = phase_override

    @property
    def name(self) -> str:
        return self._descriptor.name

    @property
    def description(self) -> str:
        return self._descriptor.description

    @property
    def parameters_schema(self) -> dict:
        return self._descriptor.parameters_schema

    @property
    def policy(self) -> MCPPolicyDecision:
        """缓存的三层判定结果 — Hook 拦截时读此字段，不重判。"""
        return self._policy

    async def execute(self, **kwargs) -> ToolResult:
        try:
            result = await self._server.call_tool(self._descriptor.name, kwargs)
            return ToolResult(output=result)
        except Exception as e:
            return ToolResult(
                error=f"mcp_call_failed: {e}",
                error_type="mcp_call_failed",
            )
