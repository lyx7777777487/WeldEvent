import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from controlplane.adapter.dag_activities import ALL_DAG_ACTIVITIES, configure_activity_pool
from controlplane.adapter.mocks import ALL_MOCK_ACTIVITIES
from controlplane.config import ControlPlaneConfig
from controlplane.runtime.dag_runner_workflow import RunWorkflowSpec
from controlplane.runtime.template_workflow import TemplateWorkflow

logger = logging.getLogger(__name__)


async def run_worker(config: ControlPlaneConfig | None = None) -> None:
    if config is None:
        config = ControlPlaneConfig()

    client = await Client.connect(
        config.temporal_host,
        namespace=config.namespace,
    )

    # P4: 创建 L3 ActivityPool 并注入到 execute_node
    # boundary-pinning §1.3/§1.4: execute_node 按 capability 分派到 L3 真实 activity。
    # IQA/PPA 已实现真实逻辑（CV 规则 + 预处理），MEA/RDA/VDA/RVA/MTA/HCA 仍走 mock。
    try:
        from executionplane.pool import create_default_pool
        activity_pool = create_default_pool()
        configure_activity_pool(activity_pool)
        pool_status = "IQA+PPA real, MEA/RDA/VDA/RVA/MTA/HCA mock"
    except ImportError as e:
        logger.warning("L3 executionplane not available — falling back to all-mock: %s", e)
        configure_activity_pool(None)
        pool_status = "all mock (executionplane import failed)"

    # 注册所有 activities:
    #   - 8 个 mock activity（iqa/ppa/mea/rda/vda/rva/mta/hca）— TemplateWorkflow 用
    #   - execute_node activity — RunWorkflowSpec DAG runner 用（boundary-pinning §6.2）
    #     execute_node 内部优先调 ActivityPool（L3 真实），未注册的 fallback 到 mock
    # P2-2 fix: load_template activity 需要 repository 注入才能工作。
    # 当前 Phase 4 无 TemplateRepository 实现，不注册 load_template。
    activities = (
        list(ALL_MOCK_ACTIVITIES)
        + list(ALL_DAG_ACTIVITIES)
    )

    # 注册所有 workflows:
    #   - TemplateWorkflow — 基于预定义 WorkflowTemplate FSM（legacy，当前不可用因无 load_template）
    #   - RunWorkflowSpec  — 接收 WorkflowSpec dict 的 generic DAG runner（§6.2，可用）
    worker = Worker(
        client,
        task_queue=config.task_queue,
        workflows=[TemplateWorkflow, RunWorkflowSpec],
        activities=activities,
    )

    print(f"Starting Control Plane worker on task queue: {config.task_queue}")
    print(f"  workflows: TemplateWorkflow, RunWorkflowSpec")
    print(f"  activities: {len(activities)} (8 mock + execute_node)")
    print(f"  L3 ActivityPool: {pool_status}")
    print(f"  note: load_template not registered (no repository) — TemplateWorkflow unavailable")
    # P2-3 fix: graceful shutdown — 捕获 SIGINT/SIGTERM 让 worker 优雅退出
    import signal
    run_task = asyncio.create_task(worker.run())
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, run_task.cancel)
    try:
        await run_task
    except asyncio.CancelledError:
        print("Worker shutdown requested — waiting for in-flight activities to complete...")


if __name__ == "__main__":
    asyncio.run(run_worker())
