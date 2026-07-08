"""Label Studio MCP Client — 对接标注平台 MCP Server v2.0。

对外提供类型安全的方法，内部通过 MCPClient 调用标注平台 MCP server
暴露的 10 个 tools。MCP server 负责对接 Label Studio REST API。

工具映射（v2.0）:
  - list_datasets  → 分页列出数据集
  - get_dataset    → 查看数据集详情 + 版本信息
  - create_job     → 创建标注作业（需 versionId）
  - list_jobs      → 列出作业及进度
  - get_job        → 查看作业详情
  - list_tasks     → 列出子任务
  - create_task    → 创建标注子任务
  - trigger_ai     → 触发 AI 自动标注（v2.0: 按 taskId 粒度）
  - upload_images  → 上传图片到版本（v2.0 NEW）
  - assign_task    → 分配标注员

配置（环境变量）:
  LABEL_STUDIO_MCP_URL      — MCP server URL（默认 http://172.16.11.11:8079/api/mcp）
  LABEL_STUDIO_MCP_TOKEN    — JWT Token（可选，不传则匿名）
  LABEL_STUDIO_MCP_USERNAME — 用户名（自动登录用，与 PASSWORD 配合）
  LABEL_STUDIO_MCP_PASSWORD — 密码（自动登录用，与 USERNAME 配合）
  LABEL_STUDIO_MCP_TIMEOUT  — HTTP 超时秒数（默认 60）

使用:
    client = LabelStudioMCPClient.from_env()
    async with client:
        ds = await client.get_dataset("01ABC...")
        job = await client.create_job(version_id=ds["latestVersionId"], name="焊缝质检")
        task = await client.create_task(job_id=job["id"])
"""

from __future__ import annotations

import logging
import re
from typing import Any

from shared.labelstudio.auth import login_labelstudio
from shared.labelstudio.config import LabelStudioConfig
from shared.mcp.client import HTTPMCPClient, MCPClient, MCPError

logger = logging.getLogger(__name__)


# ── 工具方法 ──

def _extract_id(text: str, pattern: str) -> str | None:
    """从文本中提取 ID（jobId/taskId 等）。"""
    m = re.search(pattern, text)
    return m.group(1) if m else None


def _parse_tool_response(result: Any) -> dict[str, Any]:
    """统一解析 MCP tool 返回值（JSON 或纯文本）。

    若 server 返回 JSON dict 则透传；若返回纯文本（如 "作业创建成功，jobId: xxx"），
    则从文本中提取 ID 放入 {"raw": ..., "id": ...}。
    """
    if hasattr(result, "parsed_content"):
        parsed = result.parsed_content
        if isinstance(parsed, dict):
            return parsed
        text = result.text_content
    elif isinstance(result, dict):
        return result
    else:
        text = str(result)
    resp: dict[str, Any] = {"raw": text}
    extracted = _extract_id(text, r"(?:jobId|taskId):\s*([A-Za-z0-9]+)")
    if extracted:
        resp["id"] = extracted
    return resp


# ── Label Studio MCP Client ──

class LabelStudioMCPClient:
    """标注平台 MCP client v2.0 — 封装 9 个标注业务工具。

    内部持有 HTTPMCPClient（Streamable HTTP），对外暴露类型安全的方法。
    生命周期由 async context manager 管理。
    """

    # v2.0 工具名
    TOOL_LIST_DATASETS = "list_datasets"
    TOOL_GET_DATASET = "get_dataset"
    TOOL_CREATE_JOB = "create_job"
    TOOL_LIST_JOBS = "list_jobs"
    TOOL_GET_JOB = "get_job"
    TOOL_LIST_TASKS = "list_tasks"
    TOOL_CREATE_TASK = "create_task"
    TOOL_TRIGGER_AI = "trigger_ai"
    TOOL_UPLOAD_IMAGES = "upload_images"
    TOOL_ASSIGN_TASK = "assign_task"

    def __init__(self, mcp_client: MCPClient, config: LabelStudioConfig | None = None) -> None:
        self._mcp = mcp_client
        self._config = config

    @classmethod
    def from_config(cls, config: LabelStudioConfig) -> "LabelStudioMCPClient":
        """从配置创建 client（HTTP transport + JWT 认证）。"""
        mcp = HTTPMCPClient(
            url=config.server_url,
            headers=config.get_headers() or None,
            timeout=config.timeout,
        )
        return cls(mcp, config)

    @classmethod
    def from_env(cls) -> "LabelStudioMCPClient":
        """从环境变量创建 client（便捷方法）。"""
        return cls.from_config(LabelStudioConfig.from_env())

    # ── 生命周期 ──

    async def connect(self) -> None:
        # 自动登录获取 JWT token
        if self._config and self._config.mcp_username and self._config.mcp_password:
            token = await login_labelstudio(self._config)
            self._mcp._headers["Authorization"] = f"Bearer {token}"
        await self._mcp.connect()
        await self._mcp.initialize()

    async def disconnect(self) -> None:
        await self._mcp.disconnect()

    async def __aenter__(self) -> "LabelStudioMCPClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()

    # ── 1. 数据集查询 ──

    async def list_datasets(
        self, page_num: int = 1, page_size: int = 10,
    ) -> dict[str, Any]:
        """分页列出当前用户可见的数据集。

        Returns:
            {"datasets": [...], "total": N, "pageNum": 1, "pageSize": 10}
        """
        args = {"pageNum": page_num, "pageSize": page_size}
        result = await self._mcp.call_tool(self.TOOL_LIST_DATASETS, args)
        parsed = result.parsed_content
        return parsed if isinstance(parsed, dict) else {"raw": result.text_content}

    async def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        """获取数据集详情，含 latestVersionId（创建作业必需）。

        Returns:
            {"id": "...", "name": "...", "latestVersionId": "...", ...}
        """
        args = {"datasetId": dataset_id}
        result = await self._mcp.call_tool(self.TOOL_GET_DATASET, args)
        parsed = result.parsed_content
        return parsed if isinstance(parsed, dict) else {"raw": result.text_content}

    # ── 2. 作业管理 ──

    async def create_job(
        self,
        version_id: str,
        name: str,
        labels: list[str] | None = None,
        annotation_type: str = "CLASSIFICATION",
        platform: str = "LABEL_STUDIO",
    ) -> dict[str, Any]:
        """在指定数据集版本下创建标注作业。

        Args:
            version_id: 数据集版本 ID（从 get_dataset 的 latestVersionId 获取）
            name: 作业名称（必填）
            labels: 标签列表，如 ["气孔", "夹渣", "裂纹"]
            annotation_type: 标注类型，默认 CLASSIFICATION
            platform: 标注平台，默认 LABEL_STUDIO

        Returns:
            创建的作业信息，含 id
        """
        args: dict[str, Any] = {"versionId": version_id, "name": name}
        if labels:
            args["labels"] = labels
        if annotation_type:
            args["annotationType"] = annotation_type
        if platform:
            args["platform"] = platform
        result = await self._mcp.call_tool(self.TOOL_CREATE_JOB, args)
        return _parse_tool_response(result)

    async def list_jobs(
        self, dataset_id: str, page_num: int = 1, page_size: int = 50,
    ) -> dict[str, Any]:
        """列出某个数据集下的标注作业。

        Returns:
            {"jobs": [...], "total": N}
        """
        args = {"datasetId": dataset_id, "pageNum": page_num, "pageSize": page_size}
        result = await self._mcp.call_tool(self.TOOL_LIST_JOBS, args)
        parsed = result.parsed_content
        return parsed if isinstance(parsed, dict) else {"raw": result.text_content}

    async def get_job(self, job_id: str) -> dict[str, Any]:
        """查看作业详情及完成进度。

        Returns:
            {"id": "...", "name": "...", "status": "...", "totalCount": N, "completedCount": N, ...}
        """
        args = {"jobId": job_id}
        result = await self._mcp.call_tool(self.TOOL_GET_JOB, args)
        parsed = result.parsed_content
        return parsed if isinstance(parsed, dict) else {"raw": result.text_content}

    # ── 3. 子任务管理 ──

    async def list_tasks(
        self, job_id: str, page_num: int = 1, page_size: int = 50,
    ) -> dict[str, Any]:
        """列出某个作业下的标注子任务。

        Returns:
            {"tasks": [...], "total": N}
        """
        args = {"jobId": job_id, "pageNum": page_num, "pageSize": page_size}
        result = await self._mcp.call_tool(self.TOOL_LIST_TASKS, args)
        parsed = result.parsed_content
        return parsed if isinstance(parsed, dict) else {"raw": result.text_content}

    async def create_task(self, job_id: str) -> dict[str, Any]:
        """为某个作业创建标注子任务。

        Returns:
            创建的任务信息，含 id
        """
        args = {"jobId": job_id}
        result = await self._mcp.call_tool(self.TOOL_CREATE_TASK, args)
        return _parse_tool_response(result)

    # ── 4. AI 标注 ──

    async def trigger_ai(self, task_id: str) -> dict[str, Any]:
        """触发指定标注任务的 AI 自动标注（v2.0: 按任务粒度触发）。

        Returns:
            触发确认
        """
        args = {"taskId": task_id}
        result = await self._mcp.call_tool(self.TOOL_TRIGGER_AI, args)
        return _parse_tool_response(result)

    # ── 4.5 图片上传 ──

    async def upload_images(
        self, version_id: str, images: list[dict[str, str]],
    ) -> dict[str, Any]:
        """上传图片到指定数据集版本（v2.0 NEW）。

        Args:
            version_id: 数据集版本 ID
            images: 图片列表，每项 {"filename": "img001.jpg", "data": "base64..."}

        Returns:
            上传结果
        """
        args = {"versionId": version_id, "images": images}
        result = await self._mcp.call_tool(self.TOOL_UPLOAD_IMAGES, args)
        return _parse_tool_response(result)

    # ── 5. 分配 ──

    async def assign_task(
        self, task_id: str, assignee_id: str, assignee_name: str,
    ) -> dict[str, Any]:
        """将子任务分配给指定标注员。

        Returns:
            分配确认
        """
        args = {
            "taskId": task_id,
            "assigneeId": assignee_id,
            "assigneeName": assignee_name,
        }
        result = await self._mcp.call_tool(self.TOOL_ASSIGN_TASK, args)
        return _parse_tool_response(result)

    # ── 工具发现 ──

    async def list_available_tools(self) -> list[str]:
        """列出 MCP server 实际暴露的所有工具名（调试用）。"""
        tools = await self._mcp.list_tools()
        return [t.name for t in tools]

    async def health_check(self) -> bool:
        """健康检查 — 尝试 list_tools，成功返回 True。"""
        try:
            await self._mcp.list_tools()
            return True
        except Exception as e:
            logger.warning(
                "Label Studio MCP health check failed: %s: %s",
                type(e).__name__, e,
            )
            return False


__all__ = ["LabelStudioConfig", "LabelStudioMCPClient"]
