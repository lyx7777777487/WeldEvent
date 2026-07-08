"""Annotation Activity — 通过 MCP 连接标注平台执行标注任务（v2.0）。

capability: "annotation"

支持的 action（通过 input.params.action 指定）:
  - list_datasets: 列出数据集
  - get_dataset:  查询数据集详情（获取 versionId）
  - create_job:   创建标注作业
  - list_jobs:    列出作业及进度
  - get_job:      查询作业进度
  - create_task:  创建标注子任务
  - list_tasks:   列出子任务
  - trigger_ai:   触发 AI 自动标注
  - upload_images:上传图片到版本
  - assign_task:  分配标注员

架构:
  L2 execute_node (capability="annotation")
    → AnnotationActivity.execute()
        → 按 action 分派
            → LabelStudioMCPClient.{action}()
                → MCP Streamable HTTP
                    → 标注平台 MCP server
                        → Label Studio REST API

配置:
  环境变量（见 LabelStudioConfig.from_env）:
    LABEL_STUDIO_MCP_URL      — MCP server URL
    LABEL_STUDIO_MCP_TOKEN    — JWT 认证 token
    LABEL_STUDIO_MCP_USERNAME — 用户名（自动登录）
    LABEL_STUDIO_MCP_PASSWORD — 密码（自动登录）
"""

from __future__ import annotations

import logging
from typing import Any

from executionplane.activities.base import ActivityInput, ActivityOutput, ActivityStatus

from executionplane.activities.base import ActivityMetadata, BaseActivity
from executionplane.integrations.label_studio import LabelStudioMCPClient, LabelStudioConfig

logger = logging.getLogger(__name__)


class AnnotationActivity(BaseActivity):
    """标注 Activity — 通过 MCP 连接标注平台（v2.0）。

    每次 execute() 创建一个短生命周期的 MCP client 连接（符合
    Temporal activity 无状态语义）。MCP server 进程独立运行。
    """

    ACTIONS = {
        # 单一 action（10 个 MCP tool 各一）
        "list_datasets", "get_dataset", "create_job", "list_jobs", "get_job",
        "create_task", "list_tasks", "trigger_ai", "upload_images", "assign_task",
        # 复合 action — 自动编排完整标注链路
        "auto_annotate",
    }

    @property
    def activity_name(self) -> str:
        return "annotation_activity"

    @property
    def metadata(self) -> ActivityMetadata:
        return ActivityMetadata(
            activity_id="annotation_activity",
            name="标注 Activity (标注平台 MCP v2.0)",
            version="v2",
            description="通过 MCP 协议连接标注平台，支持数据集查询/作业管理/子任务/AI标注",
            capabilities=["annotation", "label_studio", "mcp"],
            execution_target="cpu",
            estimated_latency_ms=5000,
        )

    async def execute(self, input: ActivityInput) -> ActivityOutput:
        """执行标注任务。

        input.params 期望字段（按 action）:
            action: str — 必填

            list_datasets:
                page_num: int — 可选，默认 1
                page_size: int — 可选，默认 10

            get_dataset:
                dataset_id: str — 必填

            create_job:
                version_id: str — 必填
                name: str — 必填，作业名称
                labels: list[str] — 可选，标签列表
                annotation_type: str — 可选，默认 CLASSIFICATION
                platform: str — 可选，默认 LABEL_STUDIO

            list_jobs / get_job:
                job_id / dataset_id — 按工具要求

            list_tasks / create_task:
                job_id: str — 必填

            trigger_ai:
                task_id: str — 必填（v2.0: 按任务粒度触发）

            upload_images:
                version_id: str — 必填
                images: list[dict] — 必填，每项 {"filename": "...", "data": "base64..."}

            assign_task:
                task_id: str — 必填
                assignee_id: str — 必填
                assignee_name: str — 必填
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

        # 预校验必填参数
        param_error = self._validate_params(action, params)
        if param_error:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error=param_error,
                data={"action": action, "missing_param": True},
            )

        # P5 fix: 从 dependency_results 预解析 version_id（若上游有 get_dataset 节点）
        # 避免 _do_create_job 重复调 MCP
        if action in ("create_job", "upload_images") and not params.get("version_id"):
            dep_results = input.workflow_context.get("dependency_results", {})
            resolved_vid = self._resolve_version_id_from_deps(dep_results)
            if resolved_vid:
                params["version_id"] = resolved_vid
                logger.info(
                    "AnnotationActivity: resolved version_id=%s from dependency_results (action=%s)",
                    resolved_vid, action,
                )

        config = LabelStudioConfig.from_env()
        client = LabelStudioMCPClient.from_config(config)

        try:
            async with client:
                handler = getattr(self, f"_do_{action}", None)
                if handler is None:
                    return ActivityOutput(
                        status=ActivityStatus.ERROR,
                        error=f"No handler for action '{action}'",
                    )
                # auto_annotate 需要图片 base64 用于 upload_images
                # launch_workflow 把 image_b64 注入到 node.input["image_b64"]
                # pool.dispatch 把 node.input 作为 params 传入
                if action == "auto_annotate":
                    image_b64 = params.get("image_b64")
                    return await handler(client, params, workflow_id, image_b64=image_b64)
                return await handler(client, params, workflow_id)
        except Exception as e:
            logger.error(
                "AnnotationActivity failed (action=%s workflow=%s): %s: %s",
                action, workflow_id, type(e).__name__, e, exc_info=True,
            )
            raise RuntimeError(
                f"Annotation activity '{action}' failed: {type(e).__name__}: {e}"
            ) from e

    # ── 参数预校验 ──

    @staticmethod
    def _validate_params(action: str, params: dict[str, Any]) -> str | None:
        if action in ("list_datasets", "auto_annotate"):
            return None  # 无必填参数
        if action == "get_dataset":
            if not params.get("dataset_id"):
                return "get_dataset requires 'dataset_id' param"
        elif action == "create_job":
            # version_id/dataset_id 非必填 — _do_create_job 有 4 级 fallback:
            # params.version_id → params.dataset_id → dep_results → list_datasets
            if not params.get("name"):
                return "create_job requires 'name' param"
        elif action in ("list_jobs",):
            if not params.get("dataset_id"):
                return "list_jobs requires 'dataset_id' param"
        elif action in ("get_job", "list_tasks", "create_task"):
            if not params.get("job_id"):
                return f"{action} requires 'job_id' param"
        elif action == "trigger_ai":
            if not params.get("task_id"):
                return "trigger_ai requires 'task_id' param (v2.0: 按任务粒度)"
        elif action == "upload_images":
            # version_id/dataset_id 非必填 — _do_upload_images 有 fallback 自动解析
            if not params.get("images"):
                return "upload_images requires 'images' param"
        elif action == "assign_task":
            if not params.get("task_id"):
                return "assign_task requires 'task_id' param"
            if not params.get("assignee_id"):
                return "assign_task requires 'assignee_id' param"
            if not params.get("assignee_name"):
                return "assign_task requires 'assignee_name' param"
        return None

    @staticmethod
    def _resolve_version_id_from_deps(dep_results: dict[str, Any]) -> str | None:
        """从 dependency_results 查找上游 get_dataset 节点返回的 latest_version_id。

        get_dataset 节点返回 data.latest_version_id（见 _do_get_dataset）。
        """
        for dep_result in dep_results.values():
            if not isinstance(dep_result, dict):
                continue
            data = dep_result.get("data") or {}
            if not isinstance(data, dict):
                continue
            vid = data.get("latest_version_id")
            if vid:
                return str(vid)
        return None

    # ── Action handlers ──

    async def _do_get_dataset(
        self, client: LabelStudioMCPClient, params: dict[str, Any], workflow_id: str,
    ) -> ActivityOutput:
        result = await client.get_dataset(params["dataset_id"])
        logger.info("AnnotationActivity get_dataset: workflow=%s id=%s", workflow_id, result.get("id"))
        return ActivityOutput(status=ActivityStatus.OK, data={
            "action": "get_dataset",
            "dataset_id": result.get("id"),
            "name": result.get("name"),
            "latest_version_id": result.get("latestVersionId"),
            "version_count": result.get("versionCount"),
        })

    async def _do_list_datasets(
        self, client: LabelStudioMCPClient, params: dict[str, Any], workflow_id: str,
    ) -> ActivityOutput:
        result = await client.list_datasets(
            page_num=params.get("page_num", 1),
            page_size=params.get("page_size", 10),
        )
        datasets = result.get("records", result.get("datasets", []))
        return ActivityOutput(status=ActivityStatus.OK, data={
            "action": "list_datasets",
            "datasets": datasets,
            "total": result.get("total", len(datasets)),
            "current": result.get("current", params.get("page_num", 1)),
        })

    async def _do_upload_images(
        self, client: LabelStudioMCPClient, params: dict[str, Any], workflow_id: str,
    ) -> ActivityOutput:
        """上传图像。version_id 解析与 _do_create_job 一致（4 级 fallback）。"""
        version_id = params.get("version_id")
        if not version_id:
            dataset_id = params.get("dataset_id")
            if not dataset_id:
                logger.info(
                    "AnnotationActivity upload_images: no dataset_id, auto-listing datasets (workflow=%s)",
                    workflow_id,
                )
                list_result = await client.list_datasets(page_num=1, page_size=1)
                datasets = list_result.get("records", list_result.get("datasets", []))
                if not datasets:
                    return ActivityOutput(
                        status=ActivityStatus.ERROR,
                        error="upload_images: 标注平台无可用数据集，无法自动解析 version_id",
                        data={"action": "upload_images"},
                    )
                dataset_id = datasets[0].get("id")
                if not dataset_id:
                    return ActivityOutput(
                        status=ActivityStatus.ERROR,
                        error="upload_images: list_datasets 返回的 dataset id 为空",
                        data={"action": "upload_images"},
                    )
            logger.info(
                "AnnotationActivity upload_images: auto-resolving version_id from dataset_id=%s (workflow=%s)",
                dataset_id, workflow_id,
            )
            ds_result = await client.get_dataset(dataset_id)
            version_id = ds_result.get("latestVersionId")
            if not version_id:
                return ActivityOutput(
                    status=ActivityStatus.ERROR,
                    error=f"upload_images: dataset_id={dataset_id} 的 latestVersionId 为空",
                    data={"action": "upload_images", "dataset_id": dataset_id},
                )
        result = await client.upload_images(
            version_id=version_id,
            images=params["images"],
        )
        logger.info("AnnotationActivity upload_images: workflow=%s version=%s count=%d",
            workflow_id, version_id, len(params["images"]))
        return ActivityOutput(status=ActivityStatus.OK, data={
            "action": "upload_images",
            "version_id": version_id,
            "uploaded_count": len(params["images"]),
            "result": result,
        })

    async def _do_create_job(
        self, client: LabelStudioMCPClient, params: dict[str, Any], workflow_id: str,
    ) -> ActivityOutput:
        """创建标注作业。

        version_id 解析优先级:
            1. params["version_id"] — 直接使用
            2. params["dataset_id"] → get_dataset → latestVersionId
            3. dependency_results 中上游 get_dataset 节点的 latest_version_id
            4. list_datasets 取第一个 dataset → get_dataset → latestVersionId
        """
        version_id = params.get("version_id")

        if not version_id:
            dataset_id = params.get("dataset_id")

            # 路径 4: 无 dataset_id 时，自动 list_datasets 取第一个
            if not dataset_id:
                logger.info(
                    "AnnotationActivity create_job: no dataset_id, auto-listing datasets (workflow=%s)",
                    workflow_id,
                )
                list_result = await client.list_datasets(page_num=1, page_size=1)
                datasets = list_result.get("records", list_result.get("datasets", []))
                if not datasets:
                    return ActivityOutput(
                        status=ActivityStatus.ERROR,
                        error="create_job: 标注平台无可用数据集，无法自动解析 version_id",
                        data={"action": "create_job"},
                    )
                dataset_id = datasets[0].get("id")
                if not dataset_id:
                    return ActivityOutput(
                        status=ActivityStatus.ERROR,
                        error="create_job: list_datasets 返回的 dataset id 为空",
                        data={"action": "create_job"},
                    )

            # 有 dataset_id → get_dataset 获取 latestVersionId
            logger.info(
                "AnnotationActivity create_job: auto-resolving version_id from dataset_id=%s (workflow=%s)",
                dataset_id, workflow_id,
            )
            ds_result = await client.get_dataset(dataset_id)
            version_id = ds_result.get("latestVersionId")
            if not version_id:
                return ActivityOutput(
                    status=ActivityStatus.ERROR,
                    error=f"create_job: dataset_id={dataset_id} 的 latestVersionId 为空",
                    data={"action": "create_job", "dataset_id": dataset_id},
                )

        result = await client.create_job(
            version_id=version_id,
            name=params["name"],
            labels=params.get("labels"),
            annotation_type=params.get("annotation_type", "CLASSIFICATION"),
            platform=params.get("platform", "LABEL_STUDIO"),
        )
        job_id = result.get("id")
        logger.info("AnnotationActivity create_job: workflow=%s job_id=%s version_id=%s", workflow_id, job_id, version_id)
        return ActivityOutput(status=ActivityStatus.OK, data={
            "action": "create_job",
            "job_id": job_id,
            "version_id": version_id,
            "name": result.get("name"),
            "status": result.get("status"),
        })

    async def _do_list_jobs(
        self, client: LabelStudioMCPClient, params: dict[str, Any], workflow_id: str,
    ) -> ActivityOutput:
        result = await client.list_jobs(
            dataset_id=params["dataset_id"],
            page_num=params.get("page_num", 1),
            page_size=params.get("page_size", 50),
        )
        jobs = result.get("jobs", result.get("data", []))
        return ActivityOutput(status=ActivityStatus.OK, data={
            "action": "list_jobs",
            "jobs": jobs,
            "total": result.get("total", len(jobs)),
        })

    async def _do_get_job(
        self, client: LabelStudioMCPClient, params: dict[str, Any], workflow_id: str,
    ) -> ActivityOutput:
        result = await client.get_job(params["job_id"])
        logger.info(
            "AnnotationActivity get_job: workflow=%s job=%s progress=%s/%s",
            workflow_id, params["job_id"],
            result.get("completedCount"), result.get("totalCount"),
        )
        return ActivityOutput(status=ActivityStatus.OK, data={
            "action": "get_job",
            "job_id": result.get("id"),
            "name": result.get("name"),
            "status": result.get("status"),
            "total_count": result.get("totalCount"),
            "completed_count": result.get("completedCount"),
        })

    async def _do_create_task(
        self, client: LabelStudioMCPClient, params: dict[str, Any], workflow_id: str,
    ) -> ActivityOutput:
        result = await client.create_task(job_id=params["job_id"])
        task_id = result.get("id")
        logger.info("AnnotationActivity create_task: workflow=%s task_id=%s", workflow_id, task_id)
        return ActivityOutput(status=ActivityStatus.OK, data={
            "action": "create_task",
            "task_id": task_id,
            "job_id": params["job_id"],
        })

    async def _do_list_tasks(
        self, client: LabelStudioMCPClient, params: dict[str, Any], workflow_id: str,
    ) -> ActivityOutput:
        result = await client.list_tasks(
            job_id=params["job_id"],
            page_num=params.get("page_num", 1),
            page_size=params.get("page_size", 50),
        )
        tasks = result.get("tasks", result.get("data", []))
        return ActivityOutput(status=ActivityStatus.OK, data={
            "action": "list_tasks",
            "tasks": tasks,
            "total": result.get("total", len(tasks)),
        })

    async def _do_trigger_ai(
        self, client: LabelStudioMCPClient, params: dict[str, Any], workflow_id: str,
    ) -> ActivityOutput:
        result = await client.trigger_ai(params["task_id"])
        logger.info("AnnotationActivity trigger_ai: workflow=%s task=%s", workflow_id, params["task_id"])
        return ActivityOutput(status=ActivityStatus.OK, data={
            "action": "trigger_ai",
            "task_id": params["task_id"],
            "result": result,
        })

    async def _do_assign_task(
        self, client: LabelStudioMCPClient, params: dict[str, Any], workflow_id: str,
    ) -> ActivityOutput:
        result = await client.assign_task(
            task_id=params["task_id"],
            assignee_id=params["assignee_id"],
            assignee_name=params["assignee_name"],
        )
        logger.info(
            "AnnotationActivity assign_task: workflow=%s task=%s → %s",
            workflow_id, params["task_id"], params["assignee_name"],
        )
        return ActivityOutput(status=ActivityStatus.OK, data={
            "action": "assign_task",
            "task_id": params["task_id"],
            "assignee_id": params["assignee_id"],
        })

    # ── 复合 action: auto_annotate ─────────────────────────────────────
    #
    # 借鉴 LangGraph subgraph + plan-and-execute agent 模式：
    #   1. Plan   — 根据 params 决定执行哪些 step（静态 DAG）
    #   2. Execute — 逐步执行，每步输出作为下一步输入
    #   3. Observe — 收集每步结果，失败时按策略处理（abort/skip/continue）
    #
    # 子步骤链路:
    #   step 1: list_datasets   → 获取第一个可用 dataset
    #   step 2: get_dataset      → 解析 latestVersionId
    #   step 3: create_job       → 在版本下创建标注作业
    #   step 4: upload_images    → 上传 image_b64 到版本（图片传递核心）
    #   step 5: create_task      → 创建标注子任务
    #   step 6: trigger_ai       → 触发 AI 自动标注

    async def _do_auto_annotate(
        self, client: LabelStudioMCPClient, params: dict[str, Any], workflow_id: str,
        image_b64: str | None = None,
    ) -> ActivityOutput:
        """复合 action — 自动编排完整标注链路。

        链路: list_datasets → get_dataset → create_job → upload_images
              → create_task → trigger_ai

        参数:
            params:
                name: str — 可选，作业名（默认 "自动标注-焊缝检测"）
                labels: list[str] — 可选，标签列表
                dataset_id: str — 可选，指定数据集（默认自动取第一个）
                skip_ai: bool — 可选，跳过 AI 触发（默认 False）
            image_b64: 从 ActivityInput.image_b64 自动传入

        Returns:
            ActivityOutput.data 包含完整链路结果:
                dataset_id, version_id, job_id, uploaded_count, task_id,
                ai_triggered, steps（每步执行摘要）
        """
        steps: list[dict[str, Any]] = []
        context: dict[str, Any] = {}  # 跨步骤传递的上下文

        # ── Step 1: list_datasets ──
        dataset_id = params.get("dataset_id")
        if not dataset_id:
            logger.info("[auto_annotate] step 1: list_datasets (workflow=%s)", workflow_id)
            list_result = await client.list_datasets(page_num=1, page_size=1)
            datasets = list_result.get("records", list_result.get("datasets", []))
            if not datasets:
                return ActivityOutput(
                    status=ActivityStatus.ERROR,
                    error="auto_annotate: 标注平台无可用数据集",
                    data={"action": "auto_annotate", "step": "list_datasets", "steps": steps},
                )
            dataset_id = datasets[0].get("id")
            steps.append({"step": 1, "action": "list_datasets", "status": "ok",
                          "dataset_id": dataset_id, "dataset_name": datasets[0].get("name")})
        else:
            steps.append({"step": 1, "action": "list_datasets", "status": "skipped",
                          "reason": "dataset_id provided", "dataset_id": dataset_id})
        context["dataset_id"] = dataset_id

        # ── Step 2: get_dataset → latestVersionId ──
        logger.info("[auto_annotate] step 2: get_dataset(id=%s)", dataset_id)
        ds_detail = await client.get_dataset(dataset_id)
        version_id = ds_detail.get("latestVersionId")
        if not version_id:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error=f"auto_annotate: dataset {dataset_id} 无 latestVersionId",
                data={"action": "auto_annotate", "step": "get_dataset", "steps": steps},
            )
        context["version_id"] = version_id
        steps.append({"step": 2, "action": "get_dataset", "status": "ok",
                      "version_id": version_id, "dataset_name": ds_detail.get("name")})

        # ── Step 3: create_job ──
        job_name = params.get("name") or "自动标注-焊缝检测"
        labels = params.get("labels") or ["气孔", "夹渣", "裂纹", "未熔合", "咬边", "合格"]
        logger.info("[auto_annotate] step 3: create_job(version=%s, name=%s)", version_id, job_name)
        job_result = await client.create_job(
            version_id=version_id, name=job_name, labels=labels,
        )
        job_id = job_result.get("id")
        if not job_id:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error=f"auto_annotate: create_job 未返回 job_id: {job_result}",
                data={"action": "auto_annotate", "step": "create_job", "steps": steps},
            )
        context["job_id"] = job_id
        steps.append({"step": 3, "action": "create_job", "status": "ok",
                      "job_id": job_id, "job_name": job_name, "labels": labels})

        # ── Step 4: upload_images（图片传递核心） ──
        uploaded_count = 0
        if image_b64:
            logger.info("[auto_annotate] step 4: upload_images(version=%s, 1 image)", version_id)
            images_payload = [{
                "filename": f"weld_{workflow_id or 'img'}.jpg",
                "data": image_b64,
            }]
            up_result = await client.upload_images(
                version_id=version_id, images=images_payload,
            )
            uploaded_count = 1
            steps.append({"step": 4, "action": "upload_images", "status": "ok",
                          "uploaded_count": uploaded_count, "result": up_result})
        else:
            steps.append({"step": 4, "action": "upload_images", "status": "skipped",
                          "reason": "no image_b64 in input"})

        # ── Step 5: create_task ──
        logger.info("[auto_annotate] step 5: create_task(job=%s)", job_id)
        task_result = await client.create_task(job_id=job_id)
        task_id = task_result.get("id")
        if not task_id:
            return ActivityOutput(
                status=ActivityStatus.ERROR,
                error=f"auto_annotate: create_task 未返回 task_id: {task_result}",
                data={"action": "auto_annotate", "step": "create_task", "steps": steps,
                      **context},
            )
        context["task_id"] = task_id
        steps.append({"step": 5, "action": "create_task", "status": "ok", "task_id": task_id})

        # ── Step 6: trigger_ai ──
        skip_ai = params.get("skip_ai", False)
        ai_triggered = False
        if skip_ai:
            steps.append({"step": 6, "action": "trigger_ai", "status": "skipped",
                          "reason": "skip_ai=True"})
        else:
            logger.info("[auto_annotate] step 6: trigger_ai(task=%s)", task_id)
            try:
                ai_result = await client.trigger_ai(task_id)
                ai_triggered = True
                steps.append({"step": 6, "action": "trigger_ai", "status": "ok",
                              "result": ai_result})
            except Exception as e:
                # AI 触发失败不 fatal — 标注作业已创建，人工可继续
                logger.warning("[auto_annotate] trigger_ai failed (non-fatal): %s", e)
                steps.append({"step": 6, "action": "trigger_ai", "status": "failed",
                              "error": str(e)})

        logger.info(
            "[auto_annotate] workflow=%s completed: dataset=%s version=%s job=%s task=%s ai=%s",
            workflow_id, dataset_id, version_id, job_id, task_id, ai_triggered,
        )

        return ActivityOutput(
            status=ActivityStatus.OK,
            data={
                "action": "auto_annotate",
                "dataset_id": dataset_id,
                "version_id": version_id,
                "job_id": job_id,
                "job_name": job_name,
                "uploaded_count": uploaded_count,
                "task_id": task_id,
                "ai_triggered": ai_triggered,
                "steps": steps,
            },
        )


__all__ = ["AnnotationActivity"]
