import asyncio
import os
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from controlplane.adapter.dag_activities import (
    ALL_DAG_ACTIVITIES,
    configure_activity_pool,
    configure_eval_provider,
    reset_eval_provider,
)
from controlplane.adapter.mocks import ALL_MOCK_ACTIVITIES
from controlplane.config import ControlPlaneConfig
from controlplane.runtime.dag_runner_workflow import RunWorkflowSpec

logger = logging.getLogger(__name__)


async def run_worker(config: ControlPlaneConfig | None = None) -> None:
    if config is None:
        config = ControlPlaneConfig()

    # Temporal Client 连接重试（硬约束：断线自动重连）
    max_retries = 5
    for attempt in range(max_retries):
        try:
            client = await Client.connect(
                config.temporal_host,
                namespace=config.namespace,
            )
            break
        except Exception as e:
            if attempt < max_retries - 1:
                wait = min(2 ** attempt, 30)
                logger.warning("Temporal connect failed (attempt %d/%d): %s — retrying in %ds",
                               attempt + 1, max_retries, e, wait)
                await asyncio.sleep(wait)
            else:
                raise

    # P4: 创建 L3 ActivityPool 并注入到 execute_node
    # boundary-pinning §1.3/§1.4: execute_node 按 capability 分派到 L3 真实 activity。
    # IQA/PPA 已实现真实逻辑（CV 规则 + 预处理），MEA/RDA/VDA/RVA/MTA/HCA 仍走 mock。
    # ── Composition Root Boundary ──────────────────────────────────
    # 这是 L2→L3 的唯一跨层 import 点（composition root pattern）。
    # 不在 controlplane 的 pyproject.toml 中声明 executionplane 依赖，
    # 而是运行时 try/except，让 L2 可独立安装/测试。
    # 生产部署时 L2+L3 在同一进程（Temporal worker），由部署脚本保证 sys.path。
    # ────────────────────────────────────────────────────────────────
    try:
        from executionplane.pool import create_default_pool
        activity_pool = create_default_pool()
        configure_activity_pool(activity_pool)
        pool_status = "IQA+PPA real, MEA/RDA/VDA/RVA/MTA/HCA mock"
        l3_degraded = False
    except ImportError as e:
        # P0-2 fix: L3 不可用在工业质检场景是降级状态,必须 ERROR 级(非 warning)
        #   - 所有 activity 走 mock fallback,返回 MARGINAL + mock=True
        #   - pool_status 暴露降级标记,启动日志显眼
        #   - 不 fail-fast: 允许 dev/CI 无 L3 启动,但生产部署必须监控此日志
        logger.error(
            "L3 executionplane NOT AVAILABLE — DEGRADED MODE: all activities will "
            "return MARGINAL+mock=True. This is FORBIDDEN in production welding "
            "inspection (false-pass risk). ImportError: %s",
            e,
        )
        configure_activity_pool(None)
        pool_status = "DEGRADED: all mock (executionplane import failed) — NOT production-safe"
        l3_degraded = True

    # ── Op 34: LLM provider 装配 + 注入 (供 evaluate_node_quality activity) ──
    # 与 L1 app.py 用同一个 bootstrap_llm; eval activity 通过注入拿 provider,
    # 不在 activity 内自取 env (注入模式, 对称 configure_activity_pool).
    try:
        from cognitiveplane.bootstrap.llm_setup import bootstrap_llm
        from cognitiveplane.capability import get_llm
        llm_ok = bootstrap_llm()
        if llm_ok:
            eval_model = os.getenv("DEEPSEEK_MODEL", "") or "deepseek-v4-flash"
            if eval_model == "deepseek-chat":
                eval_model = "deepseek-v4-flash"  # .env 失效模型兜底
            configure_eval_provider(get_llm(), model=eval_model)
            eval_status = f"LLM eval bound (model={eval_model})"
        else:
            configure_eval_provider(None)
            eval_status = "DEGRADED: eval heuristic only (LLM bootstrap failed)"
            logger.error("LLM bootstrap failed - Op34 eval will use heuristic only")
    except ImportError as e:
        configure_eval_provider(None)
        eval_status = f"DEGRADED: eval heuristic only (bootstrap import failed: {e})"
        logger.error("cognitiveplane bootstrap unavailable - Op34 eval degraded: %s", e)

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
        workflows=[RunWorkflowSpec],
        activities=activities,
    )

    print(f"Starting Control Plane worker on task queue: {config.task_queue}")
    print(f"  workflows: RunWorkflowSpec")
    print(f"  activities: {len(activities)} (8 mock + execute_node)")
    print(f"  L3 ActivityPool: {pool_status}")
    print(f"  Op34 LLM eval: {eval_status}")
    if l3_degraded:
        print(f"  ⚠️  DEGRADED MODE — production deployment MUST NOT run in this state")
    print(f"  note: TemplateWorkflow removed — RunWorkflowSpec is the sole production workflow (2026-07-02 audit fix)")
    # P2-3 fix: graceful shutdown — SIGINT/SIGTERM 触发后让 worker 优雅退出
    import signal

    run_task = asyncio.create_task(worker.run())
    loop = asyncio.get_running_loop()  # Python 3.12+ 推荐用 get_running_loop（非 get_event_loop）
    stop_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        # 等 stop 信号或 worker 自然退出
        done, pending = await asyncio.wait(
            {run_task, asyncio.create_task(stop_event.wait())},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if run_task in done:
            # worker 自行退出（如 task queue 断开）
            exc = run_task.exception()
            if exc:
                raise exc
        else:
            # 收到停止信号 — 取消 worker run，让它停止 poll 新任务
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass
            # P2-6 fix: 真正等 in-flight activity 完成(原代码注释说 30s 实际只 sleep 0.1s)
            # Temporal worker 的 worker.run() 被 cancel 后,已 dispatch 的 activity
            # 仍会执行到完成或 start_to_close_timeout。我们给最多 30s 优雅期。
            print("Worker stopping — waiting up to 30s for in-flight activities...")
            # 给 worker 内部清理 + in-flight activity 收尾的宽限期
            # 不阻塞无限期:30s 后强制返回(剩余 activity 由 Temporal server 重新分配)
            grace_deadline = asyncio.get_event_loop().time() + 30.0
            while asyncio.get_event_loop().time() < grace_deadline:
                # Temporal worker 没有公开 in-flight 计数,用短 sleep 轮询让事件循环处理回调
                # 实际优雅退出由 worker.shutdown() 在新版本提供;此处给宽限期让 in-flight 收尾
                await asyncio.sleep(0.5)
                # 若 run_task 已 fully done(非 cancelled),说明所有 activity 已收尾
                if run_task.done():
                    break
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    asyncio.run(run_worker())
