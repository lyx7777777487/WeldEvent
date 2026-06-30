"""Annotation Activity — 通过 MCP 连接 Label Studio 执行标注任务。

capability: "annotation"

支持的 action（通过 input.params.action 指定）:
  - create_task:      创建标注任务（上传图片到 Label Studio）
  - push_prediction:  推送预标注（IQA 检测结果 → Label Studio prediction）
  - fetch_annotations: 拉取人工标注结果
  - export_dataset:   导出标注数据集

架构:
  L2 execute_node (capability="annotation")
    → AnnotationActivity.execute()
        → 按 action 分派
            → LabelStudioMCPClient.create_annotation_task / push_prediction / ...
                → MCP 协议 (stdio/HTTP)
                    → 外部 Label Studio MCP server
                        → Label Studio REST API

配置:
  环境变量（见 LabelStudioConfig.from_env）:
    LABEL_STUDIO_MCP_TRANSPORT — "stdio" | "http"
    LABEL_STUDIO_URL           — Label Studio 地址
    LABEL_STUDIO_API_KEY       — API token
"""
from __future__ import annotations

import logging
from typing import Any

from controlplane.domain.activity import ActivityInput, ActivityOutput, ActivityStatus

from executionplane.activities.base import ActivityMetadata, BaseActivity
from executionplane.integrations.label_studio import LabelStudioMCPClient, LabelStudioConfig

logger = logging.getLogger(__name__)


class AnnotationActivity(BaseActivity):
    """标注 Activity — 通过 MCP 连接 Label Studio。

    每次 execute() 创建一个短生命周期的 MCP client 连接（符合
    Temporal activity 无状态语义）。MCP server 进程独立运行。
    """

    # 支持的动作
    ACTIONS = {"create_task", "push_prediction", "fetch_annotations", "export_dataset"}

    @property
    def activity_name(self) -> str:
        return "annotation_activity"

    @property
    def metadata(self) -> ActivityMetadata:
        return ActivityMetadata(
            activity_id="annotation_activity",
            name="标注 Activity (Label Studio MCP)",
            version="v1",
            description="通过 MCP 协议连接 Label Studio，支持创建标注任务/推送预标注/拉取标注结果/导出数据集",
            capabilities=["annotation", "label_studio", "mcp"],
            execution_target="cpu",
            estimated_latency_ms=5000,  # MCP 调用 + Label Studio API，耗时较长
        )

    async def execute(self, input: ActivityInput) -> ActivityOutput:
        """执行标注任务。

        input.params 期望字段:
            action: str — 必填，4 个动作之一
            image_path: str — create_task 时必填
            task_id: int — push_prediction/fetch_annotations 时必填
            predictions: list — push_prediction 时必填
            task_ids: list — fetch_annotations 时可选
            project_id: str — export_dataset/fetch_annotations 时必填
            export_format: str — export_dataset 时可选（默认 JSON）
            title: str — create_task 时可选
        """
        params = input.params or {}
        action = params.get("action", "").strip().lower()

        if action not in self.ACTIONS:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error=f"Unknown action '{action}'. Supported: {sorted(self.ACTIONS)}",
                data={"supported_actions": sorted(self.ACTIONS)},
            )

        workflow_id = input.workflow_context.get("workflow_id", "")
        logger.info(
            "AnnotationActivity: action=%s workflow=%s params_keys=%s",
            action, workflow_id, list(params.keys()),
        )

        # 预校验必填参数（在创建 MCP 连接之前，避免无谓的连接开销）
        param_error = self._validate_params(action, params)
        if param_error:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error=param_error,
                data={"action": action, "missing_param": True},
            )

        # 创建 MCP client（每次 execute 新建，无状态）
        config = LabelStudioConfig.from_env()
        client = LabelStudioMCPClient.from_config(config)

        try:
            async with client:
                # 分派到具体动作
                if action == "create_task":
                    return await self._do_create_task(client, params, workflow_id)
                elif action == "push_prediction":
                    return await self._do_push_prediction(client, params, workflow_id)
                elif action == "fetch_annotations":
                    return await self._do_fetch_annotations(client, params, workflow_id)
                elif action == "export_dataset":
                    return await self._do_export_dataset(client, params, workflow_id)
                else:
                    # 不会走到这里（前面已校验）
                    return ActivityOutput(
                        status=ActivityStatus.ERROR,
                        error=f"Unreachable: action={action}",
                    )
        except Exception as e:
            logger.error(
                "AnnotationActivity failed (action=%s workflow=%s): %s: %s",
                action, workflow_id, type(e).__name__, e, exc_info=True,
            )
            # 抛异常让 Temporal RetryPolicy 重试（与 IQA P3-4 fix 一致）
            raise RuntimeError(
                f"Annotation activity '{action}' failed: {type(e).__name__}: {e}"
            ) from e

    # ── 参数预校验 ──

    @staticmethod
    def _validate_params(action: str, params: dict[str, Any]) -> str | None:
        """校验每个 action 的必填参数。返回错误消息或 None。

        在创建 MCP 连接之前调用，避免因参数缺失而浪费连接建立开销。
        """
        if action == "create_task":
            if not params.get("image_path"):
                return "create_task requires 'image_path' param"
        elif action == "push_prediction":
            if params.get("task_id") is None:
                return "push_prediction requires 'task_id' param"
            if not params.get("predictions"):
                return "push_prediction requires 'predictions' param (list of prediction dicts)"
        elif action == "fetch_annotations":
            if not params.get("task_ids") and not params.get("project_id"):
                return "fetch_annotations requires either 'task_ids' or 'project_id' param"
        elif action == "export_dataset":
            if not params.get("project_id"):
                return "export_dataset requires 'project_id' param"
        return None

    # ── 4 个动作实现 ──

    async def _do_create_task(
        self, client: LabelStudioMCPClient, params: dict[str, Any], workflow_id: str
    ) -> ActivityOutput:
        """创建标注任务。"""
        image_path = params.get("image_path")
        if not image_path:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error="create_task requires 'image_path' param",
            )
        title = params.get("title", "")
        project_id = params.get("project_id")
        label_config = params.get("label_config")

        result = await client.create_annotation_task(
            image_path=image_path,
            title=title,
            project_id=project_id,
            label_config=label_config,
        )
        logger.info(
            "AnnotationActivity create_task: workflow=%s task_id=%s project_id=%s",
            workflow_id, result.get("task_id"), result.get("project_id"),
        )
        return ActivityOutput(
            status=ActivityStatus.OK,
            data={
                "action": "create_task",
                "task_id": result.get("task_id"),
                "project_id": result.get("project_id"),
                "image_url": result.get("image_url"),
                "label_studio_url": config_url(result),
            },
        )

    async def _do_push_prediction(
        self, client: LabelStudioMCPClient, params: dict[str, Any], workflow_id: str
    ) -> ActivityOutput:
        """推送预标注。"""
        task_id = params.get("task_id")
        if task_id is None:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error="push_prediction requires 'task_id' param",
            )
        predictions = params.get("predictions")
        if not predictions:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error="push_prediction requires 'predictions' param (list of prediction dicts)",
            )
        model_version = params.get("model_version", "weldevent-iqa-v1")

        result = await client.push_prediction(
            task_id=task_id,
            predictions=predictions,
            model_version=model_version,
        )
        logger.info(
            "AnnotationActivity push_prediction: workflow=%s task_id=%s prediction_id=%s",
            workflow_id, task_id, result.get("prediction_id"),
        )
        return ActivityOutput(
            status=ActivityStatus.OK,
            data={
                "action": "push_prediction",
                "task_id": task_id,
                "prediction_id": result.get("prediction_id"),
                "model_version": model_version,
            },
        )

    async def _do_fetch_annotations(
        self, client: LabelStudioMCPClient, params: dict[str, Any], workflow_id: str
    ) -> ActivityOutput:
        """拉取人工标注结果。"""
        task_ids = params.get("task_ids")
        project_id = params.get("project_id")
        status_filter = params.get("status", "completed")

        if not task_ids and not project_id:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error="fetch_annotations requires either 'task_ids' or 'project_id' param",
            )

        result = await client.fetch_annotations(
            task_ids=task_ids,
            project_id=project_id,
            status=status_filter,
        )
        annotations = result.get("annotations", [])
        logger.info(
            "AnnotationActivity fetch_annotations: workflow=%s got %d annotations",
            workflow_id, len(annotations),
        )
        return ActivityOutput(
            status=ActivityStatus.OK,
            data={
                "action": "fetch_annotations",
                "annotations": annotations,
                "count": len(annotations),
                "status_filter": status_filter,
            },
        )

    async def _do_export_dataset(
        self, client: LabelStudioMCPClient, params: dict[str, Any], workflow_id: str
    ) -> ActivityOutput:
        """导出标注数据集。"""
        project_id = params.get("project_id")
        if not project_id:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error="export_dataset requires 'project_id' param",
            )
        export_format = params.get("export_format", "JSON")

        result = await client.export_dataset(
            project_id=project_id,
            export_format=export_format,
        )
        logger.info(
            "AnnotationActivity export_dataset: workflow=%s project=%s format=%s url=%s",
            workflow_id, project_id, export_format, result.get("download_url"),
        )
        return ActivityOutput(
            status=ActivityStatus.OK,
            data={
                "action": "export_dataset",
                "project_id": project_id,
                "export_format": export_format,
                "download_url": result.get("download_url"),
                "task_count": result.get("task_count"),
            },
        )


def config_url(client: LabelStudioMCPClient) -> str:
    """从 client 提取 Label Studio URL（用于返回给调用方）。"""
    config = LabelStudioConfig.from_env()
    return config.label_studio_url


__all__ = ["AnnotationActivity"]
