"""MCP 会话管理 — 支持 JWT 认证的 Streamable HTTP 连接。

适配真实标注平台 MCP Server（见 Test/mcp-server-api-reference.html）：
  - 地址: http://172.16.11.11:8079/api/mcp/
  - 传输: Streamable HTTP
  - 认证: JWT Token（Authorization: Bearer <token>）

transport 由环境变量 MCP_TRANSPORT 决定：
  - "mock"  : 用 MockMcpServer（测试用，不连真 server）
  - "http"  : 连真实 MCP server，需设 MCP_SERVER_URL + JWT 认证
  - "rest"  : 直接调 8081 REST API（不依赖 MCP SDK），需设 MCP_REST_URL + JWT 认证

JWT 认证方式（二选一）：
  1. 直接传 token：设 MCP_JWT_TOKEN 环境变量
  2. 用户名密码登录：设 MCP_AUTH_URL + MCP_USERNAME + MCP_PASSWORD，
     mcp_session 会自动登录拿 token，token 过期时自动刷新
"""

import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

# 模块级 token 缓存（避免每次调用都登录）
_cached_token: str | None = None
_cached_token_expires: float = 0.0


class McpSessionError(Exception):
    """MCP 会话级错误（建/关会话失败）。"""


class McpInfraError(Exception):
    """MCP 基础设施失败（网络/超时/server 崩溃）— 应触发 Temporal 重试。"""


class McpBusinessError(Exception):
    """MCP 业务失败（标注 server 拒绝 / status=failed）— 不应重试，转 ERROR output。"""


async def _login_and_cache_token() -> str:
    """用用户名密码登录拿 JWT，缓存到模块级变量。

    读环境变量:
      MCP_AUTH_URL   — 登录接口 URL，如 http://172.16.11.11:8079/api/auth/login
      MCP_USERNAME   — 用户名
      MCP_PASSWORD   — 密码

    返回 token 字符串。失败抛 McpSessionError。
    """
    global _cached_token, _cached_token_expires

    auth_url = os.environ.get("MCP_AUTH_URL")
    username = os.environ.get("MCP_USERNAME")
    password = os.environ.get("MCP_PASSWORD")

    if not all([auth_url, username, password]):
        raise McpSessionError(
            "JWT 登录需要 MCP_AUTH_URL + MCP_USERNAME + MCP_PASSWORD，"
            "或直接设 MCP_JWT_TOKEN"
        )

    try:
        import httpx
    except ImportError as e:
        raise McpSessionError("httpx 未安装，请 pip install httpx") from e

    try:
        resp = await asyncio.to_thread(
            lambda: httpx.post(
                auth_url,
                json={"username": username, "password": password},
                timeout=30,
            ),
        )
        data = resp.json()
        if resp.status_code != 200 or data.get("code") != 200:
            raise McpSessionError(f"登录失败: HTTP {resp.status_code}, {data}")
        token = data["data"]["token"]
        expires_in = data["data"].get("expiresIn", 86400000)
        _cached_token = token
        _cached_token_expires = time.time() + expires_in / 1000 - 60  # 提前 60s 过期
        return token
    except (KeyError, ValueError) as e:
        raise McpSessionError(f"登录响应解析失败: {e}") from e
    except Exception as e:
        raise McpSessionError(f"登录请求失败: {e}") from e


async def _get_token() -> str:
    """获取有效 JWT token，优先用缓存，过期则重新登录。"""
    global _cached_token, _cached_token_expires

    # 优先用环境变量直接传的 token
    env_token = os.environ.get("MCP_JWT_TOKEN")
    if env_token:
        return env_token

    # 用缓存的 token（未过期）
    if _cached_token and time.time() < _cached_token_expires:
        return _cached_token

    # 重新登录
    return await _login_and_cache_token()


@asynccontextmanager
async def mcp_session() -> AsyncIterator:
    """打开一个 MCP client session，yield 出可调 call_tool 的对象。

    返回的对象必须实现:
      async def call_tool(name: str, arguments: dict) -> dict

    transport 由 MCP_TRANSPORT 环境变量决定。
    """
    transport = os.environ.get("MCP_TRANSPORT", "mock")

    if transport == "mock":
        from shared.mcp_tools.mock_mcp_server import MockMcpServer
        server = MockMcpServer()
        try:
            yield server
        finally:
            await server.close()
        return

    if transport == "rest":
        rest_url = os.environ.get("MCP_REST_URL", "http://172.16.11.11:8081")
        token = await _get_token()
        client = _RestAnnotateClient(rest_url, token)
        try:
            yield client
        finally:
            await client.close()
        return

    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError as e:
        raise McpSessionError(
            "mcp SDK 未安装，请 pip install mcp；或设 MCP_TRANSPORT=mock 跑测试"
        ) from e

    if transport == "http":
        url = os.environ.get("MCP_SERVER_URL")
        if not url:
            raise McpSessionError("MCP_TRANSPORT=http 需设 MCP_SERVER_URL")

        # 获取 JWT token
        token = await _get_token()

        # 每次调用都带 Authorization header（Streamable HTTP 无状态，每次请求独立鉴权）
        transport_ctx = streamablehttp_client(
            url,
            headers={"Authorization": f"Bearer {token}"},
        )
    else:
        raise McpSessionError(f"不支持的 transport: {transport}（第一版只支持 mock / http）")

    try:
        async with transport_ctx as (read, write, *_):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield _McpSessionAdapter(session)
    except McpSessionError:
        raise
    except Exception as e:
        raise McpInfraError(f"MCP 会话建立失败: {e}") from e


class _McpSessionAdapter:
    """把 mcp SDK 的 ClientSession 包成统一的 call_tool 接口。

    统一返回 dict；把 MCP 返回的 content 列表 + isError 标志翻译成
    McpBusinessError / 正常 dict。
    """

    def __init__(self, session):
        self._session = session

    async def call_tool(self, name: str, arguments: dict) -> dict:
        try:
            result = await self._session.call_tool(name, arguments)
        except TimeoutError as e:
            raise McpInfraError(f"MCP call_tool 超时: {name}") from e
        except Exception as e:
            raise McpInfraError(f"MCP call_tool 失败: {name}: {e}") from e

        if getattr(result, "is_error", False):
            raise McpBusinessError(f"MCP server 返回 error: {_extract_text(result)}")

        return _extract_content(result)


def _extract_text(result) -> str:
    """从 MCP CallToolResult 提取文本（用于错误消息）。"""
    contents = getattr(result, "content", []) or []
    parts = []
    for c in contents:
        text = getattr(c, "text", None)
        if text:
            parts.append(text)
    return " | ".join(parts) if parts else "(no text)"


def _extract_content(result) -> dict:
    """从 MCP CallToolResult 提取结构化内容。

    优先取第一个 JSON text content（兼容 ApiResponse 格式）；
    否则把文本包装为 {"raw_text": ..., "message": ...}。
    标注 MCP server 可能返回 JSON 或纯文本。
    """
    import json

    contents = getattr(result, "content", []) or []
    all_texts = []
    for c in contents:
        text = getattr(c, "text", None)
        if not text:
            continue
        all_texts.append(text)
        try:
            parsed = json.loads(text)
            # 已是标准 ApiResponse 格式
            if isinstance(parsed, dict) and "code" in parsed:
                return parsed
            # 是其他 JSON，包装一下
            return {"code": 200, "message": "success", "data": parsed}
        except (json.JSONDecodeError, TypeError):
            continue

    # 纯文本响应
    raw = " | ".join(all_texts) if all_texts else "(empty)"
    return {"code": 200, "message": "success", "data": raw}


class _RestAnnotateClient:
    """直接调 8081 REST API 的标注平台 client。

    不依赖 MCP SDK，用 httpx 直接调 REST API。
    实现 call_tool 接口，把工具名映射到 REST endpoint。
    返回值和 MCP transport 一致：ApiResponse 包装的 {code, message, data}。
    """

    def __init__(self, base_url: str, token: str):
        try:
            import httpx
        except ImportError as e:
            raise McpSessionError("httpx 未安装，请 pip install httpx") from e
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """把 MCP 工具名映射到 REST API 调用。"""
        import json

        try:
            if name == "list_datasets":
                resp = await self._client.get("/api/datasets", params={
                    "page": arguments.get("pageNum", 1),
                    "size": arguments.get("pageSize", 10),
                })
            elif name == "create_dataset":
                resp = await self._client.post("/api/datasets", json=arguments)
            elif name == "get_dataset":
                resp = await self._client.get(f"/api/datasets/{arguments['id']}")
            elif name == "delete_dataset":
                resp = await self._client.delete(f"/api/datasets/{arguments['id']}")
            elif name == "list_dataset_images":
                resp = await self._client.get(f"/api/datasets/{arguments['id']}/images")
            elif name == "upload_dataset_images":
                # 上传图片需要 multipart，这里传 URL 列表模拟
                resp = await self._client.post(
                    f"/api/datasets/{arguments['id']}/images",
                    params={"files": arguments.get("files", [])},
                )
            elif name == "create_job":
                resp = await self._client.post("/api/jobs", json=arguments)
            elif name == "list_jobs":
                resp = await self._client.get(f"/api/datasets/{arguments['datasetId']}/jobs")
            elif name == "get_job":
                resp = await self._client.get(f"/api/jobs/{arguments['id']}")
            elif name == "list_tasks":
                resp = await self._client.get(f"/api/jobs/{arguments['jobId']}/tasks")
            elif name == "list_all_tasks":
                resp = await self._client.get("/api/tasks", params={
                    "pageNum": arguments.get("pageNum", 1),
                    "pageSize": arguments.get("pageSize", 10),
                })
            elif name == "create_task":
                resp = await self._client.post("/api/tasks", json=arguments)
            elif name == "get_task":
                resp = await self._client.get(f"/api/tasks/{arguments['id']}")
            elif name == "assign_task":
                resp = await self._client.put(f"/api/tasks/{arguments['taskId']}/assign", json=arguments)
            elif name == "start_label_item":
                resp = await self._client.post(
                    f"/api/label/tasks/{arguments['taskId']}/items/{arguments['itemId']}",
                    json=arguments,
                )
            elif name in ("trigger_agent", "trigger_ai"):
                resp = await self._client.post("/api/agent/trigger", json=arguments)
            elif name == "start_task":
                resp = await self._client.post("/api/agent/start-task", json=arguments)
            elif name == "request_human":
                resp = await self._client.post("/api/agent/request-human", json=arguments)
            elif name == "confirm_human":
                resp = await self._client.post("/api/agent/confirm-human", json=arguments)
            elif name == "report_progress":
                resp = await self._client.post("/api/agent/report-progress", json=arguments)
            else:
                raise McpBusinessError(f"未知工具: {name}")

            data = resp.json()
            if resp.status_code != 200:
                raise McpInfraError(f"REST {name} HTTP {resp.status_code}: {data}")
            return data

        except McpBusinessError:
            raise
        except McpInfraError:
            raise
        except Exception as e:
            raise McpInfraError(f"REST {name} 调用失败: {e}") from e

    async def upload_files(self, dataset_id: str, files) -> dict:
        """上传真实图片文件到标注平台（multipart/form-data）。

        files: list[fastapi.UploadFile]
        """
        try:
            import httpx
        except ImportError:
            raise McpSessionError("httpx 未安装")

        multipart_files = []
        for f in files:
            content = await f.read()
            multipart_files.append(("files", (f.filename, content, f.content_type or "image/png")))

        try:
            resp = await self._client.post(
                f"/api/datasets/{dataset_id}/images",
                files=multipart_files,
            )
            data = resp.json()
            if resp.status_code != 200:
                raise McpInfraError(f"REST upload_files HTTP {resp.status_code}: {data}")
            return data
        except (McpBusinessError, McpInfraError):
            raise
        except Exception as e:
            raise McpInfraError(f"REST upload_files 调用失败: {e}") from e

    async def close(self) -> None:
        await self._client.aclose()
