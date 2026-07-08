"""MCP Client — 跨层共享的 MCP 传输层。

来源: 原 executionplane/mcp/client.py，提升至 shared 供 L1/L3 共享。

支持两种传输:
  - StdioMCPClient: 通过子进程 stdin/stdout 通信（本地 MCP server）
  - HTTPMCPClient: 通过 HTTP POST + SSE 通信（远程 MCP server，支持 Streamable HTTP 2025-03-26）

ABC 接口（MCPClient）:
  - list_tools() -> list[ToolDescriptor]
  - call_tool(name, arguments) -> ToolCallResult
  - subscribe_list_changed(callback) — 订阅 tools/list_changed 事件
    （HTTP 传输不支持 server 主动推送，此方法为 no-op；
     InProcess 传输可由 server 端显式触发）

使用模式:
    async with StdioMCPClient(command="python", args=["-m","label_studio_mcp_server"]) as client:
        await client.initialize()
        tools = await client.list_tools()
        result = await client.call_tool("create_task", {"image_path": "/tmp/test.jpg"})

生命周期:
  - connect()/disconnect() 管理底层连接
  - initialize() 握手（每个 client 实例只需一次）
  - list_tools()/call_tool() 可多次调用
"""
from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from shared.mcp.protocol import (
    MCPRequestBuilder,
    MCPResponse,
    RPCError,
    ToolCallResult,
    ToolDescriptor,
    parse_response,
)

logger = logging.getLogger(__name__)

# 订阅 list_changed 事件的回调类型
ListChangedCallback = Callable[[], Awaitable[None]]


# ── 异常 ──

class MCPError(Exception):
    """MCP 调用异常基类。"""


class MCPConnectionError(MCPError):
    """MCP 连接失败。"""


class MCPToolError(MCPError):
    """MCP 工具调用返回错误。"""

    def __init__(self, error: RPCError) -> None:
        self.error = error
        super().__init__(f"MCP tool error [{error.code}]: {error.message}")


# ── 抽象基类 ──

class MCPClient(ABC):
    """MCP Client 抽象基类 — 定义传输层无关的接口。

    子类需实现:
      - _connect(): 建立底层连接
      - _disconnect(): 关闭连接
      - _send_and_receive(request_dict): 发送请求并等待响应
      - subscribe_list_changed(callback): 订阅 list_changed 事件（HTTP 传输可为 no-op）

    本基类提供:
      - initialize(): 握手
      - list_tools(): 列出工具
      - call_tool(): 调用工具
      - async context manager 支持
    """

    def __init__(self, client_name: str = "weldevent-shared", client_version: str = "1.0.0") -> None:
        self._builder = MCPRequestBuilder()
        self._client_name = client_name
        self._client_version = client_version
        self._initialized = False
        self._connected = False

    # ── 子类实现的传输层方法 ──

    @abstractmethod
    async def _connect(self) -> None:
        """建立底层连接（子进程/HTTP 等）。"""

    @abstractmethod
    async def _disconnect(self) -> None:
        """关闭底层连接。"""

    @abstractmethod
    async def _send_and_receive(self, request: dict[str, Any]) -> MCPResponse:
        """发送 JSON-RPC 请求并等待响应。

        Args:
            request: JSON-RPC 2.0 请求 dict

        Returns:
            MCPResponse 解析结果

        Raises:
            MCPConnectionError: 连接断开
            MCPError: 协议错误
        """

    @abstractmethod
    def subscribe_list_changed(
        self, callback: ListChangedCallback
    ) -> None:
        """订阅 tools/list_changed 事件。

        callback 在工具列表变更时被调用（异步）。MCPRegistry 用此触发重判。
        HTTP 传输不支持 server 主动推送，此方法应为 no-op。
        """

    # ── 公共方法 ──

    async def connect(self) -> None:
        """建立连接（幂等，重复调用安全）。"""
        if self._connected:
            return
        await self._connect()
        self._connected = True
        logger.info("MCP client connected: %s", type(self).__name__)

    async def disconnect(self) -> None:
        """关闭连接（幂等）。"""
        if not self._connected:
            return
        try:
            await self._disconnect()
        finally:
            self._connected = False
            self._initialized = False
            logger.info("MCP client disconnected: %s", type(self).__name__)

    async def initialize(self) -> dict[str, Any]:
        """MCP 握手 — 交换 capabilities。

        必须在 list_tools/call_tool 之前调用。
        幂等：重复调用返回缓存结果。
        """
        if self._initialized:
            return self._init_result
        if not self._connected:
            await self.connect()
        req = self._builder.initialize(self._client_name, self._client_version)
        resp = await self._send_and_receive(req)
        # JSON-RPC 2.0: response.id 必须匹配 request.id（通知无 id 除外）
        expected_id = req.get("id")
        if expected_id is not None and resp.id is not None and resp.id != expected_id:
            raise MCPError(
                f"JSON-RPC response id mismatch: expected {expected_id}, got {resp.id}"
            )
        if resp.is_error:
            raise MCPError(f"Initialize failed: {resp.error}")
        self._init_result = resp.result or {}
        self._initialized = True
        # 发送 initialized 通知（无 id，无需等待响应）
        await self._send_notification("notifications/initialized", {})
        logger.info("MCP initialized: server=%s", self._init_result.get("serverInfo", {}))
        return self._init_result

    async def list_tools(self) -> list[ToolDescriptor]:
        """列出 server 暴露的所有工具。"""
        if not self._initialized:
            await self.initialize()
        req = self._builder.list_tools()
        resp = await self._send_and_receive(req)
        # JSON-RPC 2.0: response.id 必须匹配 request.id（通知无 id 除外）
        expected_id = req.get("id")
        if expected_id is not None and resp.id is not None and resp.id != expected_id:
            raise MCPError(
                f"JSON-RPC response id mismatch: expected {expected_id}, got {resp.id}"
            )
        if resp.is_error:
            raise MCPToolError(resp.error)
        tools_data = resp.result or {}
        return [ToolDescriptor.from_dict(t) for t in tools_data.get("tools", [])]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolCallResult:
        """调用指定工具。

        Args:
            name: 工具名（如 "create_task"）
            arguments: 工具参数

        Returns:
            ToolCallResult 含 content + is_error

        Raises:
            MCPToolError: server 返回 JSON-RPC error
            MCPError: content isError=True（工具执行失败）
        """
        if not self._initialized:
            await self.initialize()
        req = self._builder.call_tool(name, arguments)
        resp = await self._send_and_receive(req)
        # JSON-RPC 2.0: response.id 必须匹配 request.id（通知无 id 除外）
        expected_id = req.get("id")
        if expected_id is not None and resp.id is not None and resp.id != expected_id:
            raise MCPError(
                f"JSON-RPC response id mismatch: expected {expected_id}, got {resp.id}"
            )
        if resp.is_error:
            raise MCPToolError(resp.error)
        result = ToolCallResult.from_dict(resp.result or {})
        if result.is_error:
            raise MCPError(f"Tool '{name}' execution failed: {result.text_content}")
        logger.debug("MCP tool '%s' called successfully: %d content items", name, len(result.content))
        return result

    # ── 通知（无 id，无响应） ──

    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        """发送通知（无 id，不等待响应）。子类可覆写。"""
        # 默认实现：构造通知但不等待响应（send_and_receive 会等待，通知不应调用它）
        # 子类应覆写此方法实现真正的 fire-and-forget
        pass

    # ── async context manager ──

    async def __aenter__(self) -> "MCPClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()


# ── Stdio 传输 ──

class StdioMCPClient(MCPClient):
    """通过子进程 stdin/stdout 通信的 MCP client。

    适用于本地 MCP server（如 python -m label_studio_mcp_server）。
    JSON-RPC 消息按行分隔（每行一个 JSON 对象）。

    Args:
        command: 可执行文件（如 "python"）
        args: 命令行参数（如 ["-m", "label_studio_mcp_server"]）
        env: 环境变量（如 {"LABEL_STUDIO_URL": "...", "LABEL_STUDIO_API_KEY": "..."}）
        cwd: 工作目录
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._command = command
        self._args = args or []
        self._env = env
        self._cwd = cwd
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()  # 防止并发请求混淆 stdout

    async def _connect(self) -> None:
        env = None
        if self._env:
            import os
            env = dict(os.environ)
            env.update(self._env)
        self._process = await asyncio.create_subprocess_exec(
            self._command,
            *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=self._cwd,
        )
        logger.info("StdioMCPClient started: %s %s (pid=%d)", self._command, " ".join(self._args), self._process.pid)

    async def _disconnect(self) -> None:
        if self._process is None:
            return
        try:
            self._process.terminate()
            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            self._process.kill()
            await self._process.wait()
        except ProcessLookupError:
            pass
        finally:
            self._process = None

    async def _send_and_receive(self, request: dict[str, Any]) -> MCPResponse:
        if self._process is None or self._process.stdin is None or self._process.stdout is None:
            raise MCPConnectionError("Stdio process not connected")
        async with self._lock:
            line = json.dumps(request) + "\n"
            self._process.stdin.write(line.encode("utf-8"))
            await self._process.stdin.drain()
            # 读取一行响应（跳过非 JSON 行，如 stderr 日志泄漏到 stdout）
            while True:
                raw_line = await self._process.stdout.readline()
                if not raw_line:
                    # stdout 关闭 — 检查 stderr 获取错误信息
                    stderr_data = await self._process.stderr.read() if self._process.stderr else b""
                    raise MCPConnectionError(
                        f"MCP server stdout closed. stderr: {stderr_data.decode('utf-8', errors='replace')[:500]}"
                    )
                raw_str = raw_line.decode("utf-8").strip()
                if not raw_str:
                    continue
                # 尝试解析为 JSON — 失败则跳过（可能是 server 日志）
                try:
                    return parse_response(raw_str)
                except ValueError:
                    logger.debug("Skipping non-JSON line from MCP server stdout: %s", raw_str[:200])
                    continue

    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            return
        notification = {"jsonrpc": "2.0", "method": method, "params": params}
        line = json.dumps(notification) + "\n"
        self._process.stdin.write(line.encode("utf-8"))
        await self._process.stdin.drain()

    def subscribe_list_changed(self, callback: ListChangedCallback) -> None:
        """Stdio 传输暂不支持 list_changed 主动推送 — no-op。"""
        # 子进程 MCP server 若实现 list_changed 通知，可在 _send_and_receive
        # 读取循环中识别 notification 消息并触发回调。当前实现简化为 no-op。
        pass


# ── HTTP 传输 ──

class HTTPMCPClient(MCPClient):
    """通过 HTTP POST 通信的 MCP client，支持 Streamable HTTP (2025-03-26)。

    兼容两级服务器:
      - 传统 HTTP: POST JSON → JSON response
      - Streamable HTTP: POST + Accept: text/event-stream → JSON 或 SSE response,
        自动管理 Mcp-Session-Id 跨请求持久化 + disconnect 时 DELETE 关闭 session。

    Args:
        url: MCP server HTTP endpoint（如 "http://localhost:9000/mcp"）
        headers: 额外 HTTP 头（如 Authorization）
        timeout: 请求超时（秒）
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._url = url
        # Streamable HTTP: Accept SSE 用于 server→client 推送
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if headers:
            self._headers.update(headers)
        self._timeout = timeout
        self._http_client: httpx.AsyncClient | None = None
        self._session_id: str | None = None  # Mcp-Session-Id 跨请求持久化

    @property
    def headers(self) -> dict[str, str]:
        """暴露 headers 让上层（如自动登录）在 connect 前注入 Authorization。"""
        return self._headers

    async def _connect(self) -> None:
        self._http_client = httpx.AsyncClient(
            timeout=self._timeout,
            headers=self._headers,
            trust_env=False,  # 绕过系统代理
        )
        # 健康探测 — 不强制要求 server 提前可用，initialize 时会真正连接
        logger.info("HTTPMCPClient created for url: %s", self._url)

    async def _disconnect(self) -> None:
        if self._http_client is not None:
            # Streamable HTTP: 发送 DELETE 优雅关闭 session
            if self._session_id:
                del_headers = {"Mcp-Session-Id": self._session_id}
                try:
                    await self._http_client.delete(self._url, headers=del_headers)
                except httpx.RequestError:
                    pass
            await self._http_client.aclose()
            self._http_client = None
            self._session_id = None

    async def _send_and_receive(self, request: dict[str, Any]) -> MCPResponse:
        if self._http_client is None:
            raise MCPConnectionError("HTTP client not connected")
        # Streamable HTTP: 注入 Mcp-Session-Id（首次请求不携带，由 server 返回）
        headers: dict[str, str] = {}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        try:
            resp = await self._http_client.post(self._url, json=request, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise MCPConnectionError(f"MCP server HTTP error {e.response.status_code}: {e.response.text[:200]}") from e
        except httpx.RequestError as e:
            raise MCPConnectionError(f"MCP server request failed: {e}") from e
        # 从响应头提取/更新 Mcp-Session-Id
        session_id = resp.headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id

        # Streamable HTTP: server 可能返回 JSON 或 SSE stream
        content_type = resp.headers.get("Content-Type", "")
        if "text/event-stream" in content_type:
            json_body = self._parse_sse_response(resp.text)
        else:
            json_body = resp.text
        return parse_response(json_body)

    @staticmethod
    def _parse_sse_response(sse_text: str) -> str:
        """从 SSE stream 中提取 JSON-RPC 响应。

        Streamable HTTP spec: server 可能返回 Content-Type: text/event-stream，
        其中 event: message 的 data 行即为 JSON-RPC response。
        无 event: 头时默认为 message 类型。
        """
        current_event: str | None = None
        current_data: list[str] = []
        for line in sse_text.split("\n"):
            stripped = line.rstrip("\r")
            if stripped.startswith("event:"):
                current_event = stripped[6:].strip()
            elif stripped.startswith("data:"):
                current_data.append(stripped[5:].strip())
            elif stripped == "":
                # 空行 = 事件结束；无 event 头时默认 message
                if (current_event is None or current_event == "message") and current_data:
                    return "\n".join(current_data)
                current_event = None
                current_data = []
        # 回退：取最后一个 data 作为响应（兼容无尾随空行的简化实现）
        if current_data:
            return "\n".join(current_data)
        raise MCPConnectionError(f"No JSON-RPC response found in SSE stream: {sse_text[:200]}")

    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        if self._http_client is None:
            return
        notification = {"jsonrpc": "2.0", "method": method, "params": params}
        headers: dict[str, str] = {}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        try:
            await self._http_client.post(self._url, json=notification, headers=headers)
        except httpx.RequestError:
            pass  # 通知是 fire-and-forget，失败不阻断

    def subscribe_list_changed(self, callback: ListChangedCallback) -> None:
        """HTTP 传输不支持 server 主动推送 list_changed — no-op。

        Streamable HTTP spec 虽允许 server 通过 SSE 推送 notification，
        但需要持久的 SSE 连接（GET 请求），当前实现是 POST 请求-响应模式，
        不维持长连接。若未来需要，可在此处维护一个后台 GET SSE listener。
        """
        pass


__all__ = [
    "MCPClient",
    "StdioMCPClient",
    "HTTPMCPClient",
    "MCPError",
    "MCPConnectionError",
    "MCPToolError",
]
