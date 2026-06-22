import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from controlplane.adapter.mocks import ALL_MOCK_ACTIVITIES
from controlplane.adapter.template_loader import create_load_template_activity
from controlplane.config import ControlPlaneConfig
from controlplane.runtime.template_workflow import TemplateWorkflow


async def run_worker(config: ControlPlaneConfig | None = None) -> None:
    if config is None:
        config = ControlPlaneConfig()

    client = await Client.connect(
        config.temporal_host,
        namespace=config.namespace,
    )

    activities = list(ALL_MOCK_ACTIVITIES) + [create_load_template_activity()]

    worker = Worker(
        client,
        task_queue=config.task_queue,
        workflows=[TemplateWorkflow],
        activities=activities,
    )

    print(f"Starting Control Plane worker on task queue: {config.task_queue}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(run_worker())
