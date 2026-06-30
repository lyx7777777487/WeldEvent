"""Label Studio MCP Client — 封装 4 个标注业务工具调用。

对外提供类型安全的方法，内部通过 MCPClient 调用 Label Studio MCP server
暴露的 tools。MCP server 负责对接 Label Studio REST API。

工具映射:
  - create_annotation_task → Label Studio 建 project + 上传图片
  - push_prediction        → IQA 检测结果作为预标注推送
  - fetch_annotations      → 拉取人工标注结果
  - export_dataset         → 导出标注数据集

配置:
  环境变量（传给 MCP server 子进程）:
    LABEL_STUDIO_URL       — Label Studio 地址（如 http://localhost:8080）
    LABEL_STUDIO_API_KEY   — API token
    LABEL_STUDIO_PROJECT_ID— 默认 project（可选）

使用:
    client = LabelStudioMCPClient.from_env()
    async with client:
        task = await client.create_annotation_task(image_path="/tmp/test.jpg", title="焊缝1")
        await client.push_prediction(task_id=task["id"], predictions=[...])
        annotations = await client.fetch_annotations(task_ids=[task["id"]])
        dataset = await client.export_dataset(project_id=task["project_id"], format="JSON")
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from executionplane.mcp.client import HTTPMCPClient, MCPClient, MCPError, StdioMCPClient

logger = logging.getLogger(__name__)


# ── 数据结构 ──

@dataclass
class LabelStudioConfig:
    """Label Studio MCP server 连接配置。"""
    # MCP 传输方式: "stdio" 或 "http"
    transport: str = "stdio"
    # stdio 模式: MCP server 启动命令
    server_command: str = "python"
    server_args: list[str] = field(default_factory=lambda: ["-m", "label_studio_mcp_server"])
    # http 模式: MCP server URL
    server_url: str = "http://localhost:9000/mcp"
    # Label Studio 连接（作为 env 传给 MCP server）
    label_studio_url: str = "http://localhost:8080"
    label_studio_api_key: str = ""
    label_studio_project_id: str = ""
    # HTTP 超时
    timeout: float = 60.0

    @classmethod
    def from_env(cls) -> "LabelStudioConfig":
        """从环境变量加载配置。

        环境变量:
          LABEL_STUDIO_MCP_TRANSPORT — "stdio" | "http"（默认 stdio）
          LABEL_STUDIO_MCP_COMMAND   — stdio 模式命令（默认 python）
          LABEL_STUDIO_MCP_ARGS      — stdio 模式参数（空格分隔）
          LABEL_STUDIO_MCP_URL       — http 模式 URL
          LABEL_STUDIO_URL           — Label Studio 地址
          LABEL_STUDIO_API_KEY       — API token
          LABEL_STUDIO_PROJECT_ID    — 默认 project
          LABEL_STUDIO_MCP_TIMEOUT   — 超时秒数
        """
        transport = os.environ.get("LABEL_STUDIO_MCP_TRANSPORT", "stdio").strip().lower()
        command = os.environ.get("LABEL_STUDIO_MCP_COMMAND", "python")
        args_str = os.environ.get("LABEL_STUDIO_MCP_ARGS", "-m label_studio_mcp_server")
        server_url = os.environ.get("LABEL_STUDIO_MCP_URL", "http://localhost:9000/mcp")
        ls_url = os.environ.get("LABEL_STUDIO_URL", "http://localhost:8080")
        ls_key = os.environ.get("LABEL_STUDIO_API_KEY", "")
        ls_project = os.environ.get("LABEL_STUDIO_PROJECT_ID", "")
        timeout_str = os.environ.get("LABEL_STUDIO_MCP_TIMEOUT", "60")

        return cls(
            transport=transport,
            server_command=command,
            server_args=args_str.split(),
            server_url=server_url,
            label_studio_url=ls_url,
            label_studio_api_key=ls_key,
            label_studio_project_id=ls_project,
            timeout=float(timeout_str),
        )

    def to_env(self) -> dict[str, str]:
        """转成环境变量 dict（传给 stdio 子进程）。"""
        env: dict[str, str] = {
            "LABEL_STUDIO_URL": self.label_studio_url,
            "LABEL_STUDIO_API_KEY": self.label_studio_api_key,
        }
        if self.label_studio_project_id:
            env["LABEL_STUDIO_PROJECT_ID"] = self.label_studio_project_id
        return env


# ── Label Studio MCP Client ──

class LabelStudioMCPClient:
    """Label Studio MCP client — 封装 4 个标注业务工具。

    内部持有 MCPClient（stdio 或 http），对外暴露类型安全的方法。
    生命周期由 async context manager 管理。
    """

    # MCP server 暴露的工具名（约定）
    TOOL_CREATE_TASK = "create_annotation_task"
    TOOL_PUSH_PREDICTION = "push_prediction"
    TOOL_FETCH_ANNOTATIONS = "fetch_annotations"
    TOOL_EXPORT_DATASET = "export_dataset"

    def __init__(self, mcp_client: MCPClient) -> None:
        self._mcp = mcp_client

    @classmethod
    def from_config(cls, config: LabelStudioConfig) -> "LabelStudioMCPClient":
        """从配置创建 client。"""
        if config.transport == "http":
            mcp = HTTPMCPClient(
                url=config.server_url,
                timeout=config.timeout,
            )
        else:
            mcp = StdioMCPClient(
                command=config.server_command,
                args=config.server_args,
                env=config.to_env(),
            )
        return cls(mcp)

    @classmethod
    def from_env(cls) -> "LabelStudioMCPClient":
        """从环境变量创建 client（便捷方法）。"""
        return cls.from_config(LabelStudioConfig.from_env())

    # ── 生命周期 ──

    async def connect(self) -> None:
        await self._mcp.connect()
        await self._mcp.initialize()

    async def disconnect(self) -> None:
        await self._mcp.disconnect()

    async def __aenter__(self) -> "LabelStudioMCPClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()

    # ── 4 个业务工具 ──

    async def create_annotation_task(
        self,
        image_path: str,
        title: str = "",
        project_id: str | None = None,
        label_config: str | None = None,
    ) -> dict[str, Any]:
        """创建标注任务 — 在 Label Studio 建 project（可选）+ 上传图片。

        Args:
            image_path: 图片磁盘路径（L2 launch_workflow 已落盘的 image_path）
            title: 任务标题（默认用文件名）
            project_id: 已有 project ID（不传则新建 project）
            label_config: Label Studio XML 标注配置（不传用默认焊缝缺陷模板）

        Returns:
            {"task_id": N, "project_id": N, "image_url": "..."}
        """
        args: dict[str, Any] = {"image_path": image_path}
        if title:
            args["title"] = title
        if project_id:
            args["project_id"] = project_id
        if label_config:
            args["label_config"] = label_config
        result = await self._mcp.call_tool(self.TOOL_CREATE_TASK, args)
        parsed = result.parsed_content
        if isinstance(parsed, dict):
            return parsed
        return {"raw": result.text_content, "task_id": None, "project_id": None}

    async def push_prediction(
        self,
        task_id: int | str,
        predictions: list[dict[str, Any]],
        model_version: str = "weldevent-iqa-v1",
    ) -> dict[str, Any]:
        """推送预标注 — 把 IQA 的检测结果作为 prediction 推送到 Label Studio。

        人工标注时能看到预标注结果，只需确认/修正，提升效率。

        Args:
            task_id: Label Studio task ID
            predictions: 预测结果列表，格式兼容 Label Studio predictions API
                         如 [{"result": [{"type": "rectanglelabels", "value": {...}}]}]
            model_version: 模型版本标识

        Returns:
            {"prediction_id": N, "task_id": N}
        """
        args = {
            "task_id": task_id,
            "predictions": predictions,
            "model_version": model_version,
        }
        result = await self._mcp.call_tool(self.TOOL_PUSH_PREDICTION, args)
        parsed = result.parsed_content
        if isinstance(parsed, dict):
            return parsed
        return {"raw": result.text_content, "prediction_id": None}

    async def fetch_annotations(
        self,
        task_ids: list[int | str] | None = None,
        project_id: str | None = None,
        status: str = "completed",
    ) -> dict[str, Any]:
        """拉取人工标注结果。

        Args:
            task_ids: 指定 task ID 列表（不传则拉整个 project）
            project_id: project ID（task_ids 不传时必填）
            status: 过滤状态 — "completed"=已完成, "all"=全部

        Returns:
            {"annotations": [{"task_id": N, "result": [...], "created_at": "..."}]}
        """
        args: dict[str, Any] = {"status": status}
        if task_ids:
            args["task_ids"] = task_ids
        if project_id:
            args["project_id"] = project_id
        result = await self._mcp.call_tool(self.TOOL_FETCH_ANNOTATIONS, args)
        parsed = result.parsed_content
        if isinstance(parsed, dict):
            return parsed
        return {"raw": result.text_content, "annotations": []}

    async def export_dataset(
        self,
        project_id: str,
        export_format: str = "JSON",
    ) -> dict[str, Any]:
        """导出标注数据集。

        Args:
            project_id: project ID
            export_format: 导出格式 — "JSON" | "JSON_MIN" | "COCO" | "CSV"

        Returns:
            {"download_url": "...", "format": "...", "task_count": N}
            （download_url 可用于下载完整数据集）
        """
        args = {"project_id": project_id, "format": export_format}
        result = await self._mcp.call_tool(self.TOOL_EXPORT_DATASET, args)
        parsed = result.parsed_content
        if isinstance(parsed, dict):
            return parsed
        return {"raw": result.text_content, "download_url": None}

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
        except (MCPError, Exception) as e:
            logger.warning("Label Studio MCP health check failed: %s: %s", type(e).__name__, e)
            return False


__all__ = ["LabelStudioConfig", "LabelStudioMCPClient"]
