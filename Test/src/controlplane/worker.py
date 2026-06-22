"""Control Plane Worker — 注册 TemplateWorkflow + activities。

第一版改动（相比原 controlplane/worker.py）:
  - 注入 FileTemplateRepository 到 load_template activity（跨进程共享 template）
  - 注册真实 annotation activity（裸函数，见 executionplane.activities.annotation）
  - 保留 mock activities 作为其他 CP 的占位（template 里用到才需要替换）
"""

import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from controlplane.adapter.mocks import ALL_MOCK_ACTIVITIES
from controlplane.adapter.template_loader import create_load_template_activity
from controlplane.config import ControlPlaneConfig
from controlplane.runtime.template_workflow import TemplateWorkflow
from executionplane.activities.annotation import annotation_activity
from shared.template_repository import FileTemplateRepository


async def run_worker(config: ControlPlaneConfig | None = None) -> None:
    if config is None:
        config = ControlPlaneConfig()

    client = await Client.connect(
        config.temporal_host,
        namespace=config.namespace,
    )

    repo_root = os.environ.get("TEMPLATE_REPO_ROOT", "templates")
    repository = FileTemplateRepository(root_dir=repo_root)

    activities = (
        list(ALL_MOCK_ACTIVITIES)
        + [create_load_template_activity(repository=repository)]
        + [annotation_activity]
    )

    worker = Worker(
        client,
        task_queue=config.task_queue,
        workflows=[TemplateWorkflow],
        activities=activities,
    )

    print(f"Starting Control Plane worker on task queue: {config.task_queue}")
    print(f"  template repo: {repo_root}")
    print(f"  activities: {[getattr(a, '__name', str(a)) for a in activities]}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(run_worker())