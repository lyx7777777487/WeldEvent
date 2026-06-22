"""workflow_runner — L1 → L2 桥接：save template + 启动 Temporal workflow + 等结果。

接口 1（见 Test/docs/02-architecture.md）：
  - repository 直接存 dict，不重建 WorkflowTemplate dataclass
  - load_template activity 返回 _make_json_safe(asdict(...)) 的 dict，
    重建由 TemplateWorkflow._dict_to_template 在 workflow 内部完成
"""

import logging
import uuid
from typing import Any

from temporalio.client import Client, WorkflowHandle

from controlplane.config import ControlPlaneConfig
from controlplane.runtime.template_workflow import TemplateWorkflow
from shared.template_repository import FileTemplateRepository

logger = logging.getLogger(__name__)


class WorkflowRunner:
    def __init__(
        self,
        temporal_client: Client,
        repository: FileTemplateRepository,
        task_queue: str = "control-plane",
    ):
        self._client = temporal_client
        self._repo = repository
        self._task_queue = task_queue

    async def start(self, template: dict) -> WorkflowHandle:
        template_id = template["id"]
        template_version = template.get("version", "1")
        template_ref = {"template_id": template_id, "template_version": template_version}

        await self._repo.save(template_id, template_version, template)
        logger.info("template saved: %s@%s", template_id, template_version)

        workflow_id = f"annot-{template_id}-{uuid.uuid4().hex[:8]}"
        handle = await self._client.start_workflow(
            TemplateWorkflow.run,
            template_ref,
            id=workflow_id,
            task_queue=self._task_queue,
        )
        logger.info("workflow started: %s", workflow_id)
        return handle

    async def wait_result(self, handle: WorkflowHandle) -> dict:
        return await handle.result()


async def connect_temporal(host: str = "localhost:7233", namespace: str = "default") -> Client:
    return await Client.connect(host, namespace=namespace)


async def run_workflow(template: dict) -> list[tuple[str, dict]]:
    """L1 直接执行模式 — 不走 Temporal，直接调 Activity。

    用于 e2e 测试和本地验证。生产环境用 WorkflowRunner.start + wait_result。
    只支持单 CP 无 transition 的线性模板。
    """
    from controlplane.domain.activity import ActivityInput
    from executionplane.activities.annotation.activity import annotation_activity

    results: list[tuple[str, dict]] = []
    for cp in template.get("control_points", []):
        cp_id = cp["id"]
        activity_name = cp.get("activity_binding", {}).get("activity_name", "")
        params = cp.get("params", {})

        if activity_name == "annotation":
            output = await annotation_activity(ActivityInput(
                control_point_id=cp_id,
                workflow_context={},
                params=params,
            ))
            results.append((cp_id, output))
        else:
            results.append((cp_id, {
                "status": "error",
                "error": f"未知 activity: {activity_name}",
            }))

    return results