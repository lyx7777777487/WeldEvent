"""DAG Runner Activities — 按 WorkflowNode.capability 分派执行。

boundary-pinning §6.2: generic DAG runner 按 nodes 顺序调度 Activity，每个
Activity 按 capability 调 ToolPool。

Phase 4 实现：execute_node 优先调 L3 ActivityPool（真实 IQA/PPA activity）。
若 capability 未在 pool 注册，fallback 到 mock activity（向后兼容测试 + 未实现 activity）。

Source: boundary-pinning §6.2 + §1.3 Activity Pool + §1.4 Tool Pool
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TYPE_CHECKING

from temporalio import activity

from controlplane.domain.activity import ActivityOutput, ActivityStatus

if TYPE_CHECKING:
    from executionplane.pool import ActivityPool

logger = logging.getLogger(__name__)


# ── L3 ActivityPool 注入点 ───────────────────────────────────────────
# Phase 4: execute_node 优先调 ActivityPool.dispatch() 走真实 L3 activity。
# pool 未配置时（测试 / 旧环境），fallback 到 mock 分派。
_activity_pool: "ActivityPool | None" = None


def configure_activity_pool(pool: "ActivityPool | None") -> None:
    """注入 L3 ActivityPool 实例。

    worker.py 启动时调用，把创建好的 ActivityPool 注入到 execute_node。
    传 None 可清除注入（测试隔离用）。
    """
    global _activity_pool
    _activity_pool = pool
    if pool is not None:
        logger.info("execute_node bound to ActivityPool (L3 real activities)")
    else:
        logger.info("execute_node ActivityPool cleared (fallback to mock)")


def reset_activity_pool() -> None:
    """P3-8 fix: 显式重置全局 _activity_pool（测试隔离用）。

    与 configure_activity_pool(None) 等价，但语义更清晰 —
    专门用于测试 tearDown / pytest fixture 清理。

    测试用法:
        @pytest.fixture(autouse=True)
        def _clean_pool():
            yield
            reset_activity_pool()
    """
    global _activity_pool
    _activity_pool = None


# ── capability → mock activity 分派表（fallback 路径）─────────────────
# boundary-pinning §1.5: 工业执行 MCP（detect_defects、annotate_label）归 L3
# 仓库，受 L2 activity pool 调用。当前 Phase 4 用 ActivityPool 走真实实现；
# 未注册的 capability 走此 mock 表（向后兼容）。

_CAPABILITY_DISPATCH: dict[str, str] = {
    "iqa": "iqa_activity",
    "ppa": "ppa_activity",
    "mea": "mea_activity",
    "rda": "rda_activity",
    "vda": "vda_activity",
    "rva": "rva_activity",
    "mta": "mta_activity",
    "hca": "hca_activity",
    # Annotation（Label Studio MCP 标注）— L3 已有真实实现，这里作为 fallback
    "annotation": "annotation_activity",
    "label_studio": "annotation_activity",
    "annotate_label": "annotation_activity",
    # WorkflowNode 常见 capability 名（design_workflow 工具产出）
    "defect_detection": "iqa_activity",
    "annotation_write": "hca_activity",
    "human_review": "hca_activity",
    "brain_assessment": "mta_activity",
}


@activity.defn(name="execute_node")
async def execute_node(node_input: dict[str, Any]) -> dict[str, Any]:
    """执行单个 WorkflowNode。

    入参 node_input:
        node_id: str
        capability: str | None
        input: dict
        type: NodeType ("brain_task" / "tool_task" / "human_task" / "wait_task")
        workflow_id: str（L2 workflow 注入，供 L3 读写 WeldMap）

    返回 ActivityOutput 序列化 dict:
        status: "ok" | "marginal" | "ng" | "error"
        data: dict
        error: str | None

    分派顺序:
        1. human_task / wait_task / brain_task → 内置处理（不走 L3）
        2. tool_task + ActivityPool 已注册该 capability → 调真实 L3 activity
        3. tool_task + ActivityPool 未注册 → fallback 到 mock 分派
    """
    node_id = node_input.get("node_id", "unknown")
    capability = node_input.get("capability") or ""
    node_type = node_input.get("type", "tool_task")
    node_input_data = node_input.get("input", {})

    # human_task: 审批由 workflow 层 HumanGateSignal 处理（P2-10）。
    # P2-6 fix: activity 校验 gate_approved 字段，防止 workflow 层 bug 未等 signal 就调 activity
    if node_type == "human_task":
        gate_approved = node_input.get("gate_approved", False)
        if not gate_approved:
            return _to_dict(ActivityOutput(
                status=ActivityStatus.ERROR,
                data={"node_id": node_id},
                error="human_task called without gate_approved=True (workflow layer bug)",
            ))
        return _to_dict(ActivityOutput(
            status=ActivityStatus.OK,
            data={"node_id": node_id, "review": "approved via HumanGateSignal"},
        ))

    # wait_task: 立即返回（Phase 4+ 接 wait condition）
    if node_type == "wait_task":
        return _to_dict(ActivityOutput(
            status=ActivityStatus.OK,
            data={"node_id": node_id, "wait": "skipped (Phase 2 mock)"},
        ))

    # brain_task: 占位返回（Brain 不通过 Activity 执行，Phase 4+ 走回调）
    if node_type == "brain_task":
        return _to_dict(ActivityOutput(
            status=ActivityStatus.OK,
            data={"node_id": node_id, "brain": "placeholder (Phase 4+ callback)"},
        ))

    # tool_task: 优先调 L3 ActivityPool（真实执行）
    if _activity_pool is not None and _activity_pool.supports(capability):
        try:
            result = await _activity_pool.dispatch(node_input)
            if result is not None:
                logger.info(
                    "execute_node dispatched to L3 ActivityPool: node=%s capability=%s → status=%s",
                    node_id, capability, result.get("status"),
                )
                return result
        except (ConnectionError, OSError, ImportError, TimeoutError, asyncio.TimeoutError) as e:
            # 瞬时故障 — re-raise 让 Temporal RetryPolicy 自动重试
            logger.error(
                "L3 dispatch transient failure (node=%s capability=%s): %s: %s — will be retried by Temporal",
                node_id, capability, type(e).__name__, e, exc_info=True,
            )
            raise
        except Exception as e:
            # 业务/非瞬时故障 — 返回 ERROR，让 workflow 走 on_failure 决策
            logger.error(
                "L3 ActivityPool dispatch failed (node=%s capability=%s): %s: %s",
                node_id, capability, type(e).__name__, e, exc_info=True,
            )
            return _to_dict(ActivityOutput(
                status=ActivityStatus.ERROR,
                data={"node_id": node_id, "capability": capability, "l3_error": str(e)},
                error=f"L3 activity execution failed: {type(e).__name__}: {e}",
            ))

    # fallback: mock 分派（capability 未在 ActivityPool 注册）
    # P0-2 fix: 工业质检场景下静默 mock OK 是安全风险(假合格)。
    #   - mock 必须返回 MARGINAL(非 OK),让上层区分"未真执行"
    #   - data 加 mock=True 标记,L2/L1/用户可识别
    #   - L2 dag_runner 把 mock 节点单独计入 _mocked_nodes,_build_result 暴露
    activity_name = _CAPABILITY_DISPATCH.get(capability)
    if activity_name is None:
        # 未知 capability — 返回 error
        return _to_dict(ActivityOutput(
            status=ActivityStatus.ERROR,
            data={"node_id": node_id, "capability": capability},
            error=f"Unknown capability: {capability}. Available: {list(_CAPABILITY_DISPATCH.keys())}",
        ))

    # mock 结果 — MARGINAL + mock=True,绝不返回 OK
    logger.warning(
        "MOCK DISPATCH: node=%s capability=%s → %s (no L3 activity registered, "
        "returning MARGINAL with mock=True; this MUST NOT be treated as real pass)",
        node_id, capability, activity_name,
    )
    return _to_dict(ActivityOutput(
        status=ActivityStatus.MARGINAL,
        data={
            "node_id": node_id,
            "capability": capability,
            "dispatched_to": activity_name,
            "input": node_input_data,
            "mock": True,  # P0-2: 显式 mock 标记,上层必须尊重
            "mock_reason": "no L3 ActivityPool registered for this capability",
            "mock_result": f"{activity_name}_executed",
        },
        error=None,
    ))


def _to_dict(output: ActivityOutput) -> dict[str, Any]:
    """ActivityOutput → JSON 安全 dict（跨 Temporal 边界）。"""
    return {
        "status": output.status.value,
        "data": output.data,
        "error": output.error,
    }


# ── emit_workflow_event activity ─────────────────────────────────────
# P1-4: L2→L1 节点事件回传。Temporal workflow 不能直接调 HTTP(sandbox 限制),
# 必须通过 activity 执行。dag_runner_workflow 在节点 start/end/error 时调用此
# activity,POST 事件到 L1 的 /api/v1/chat/workflow/events 端点。
# L1 收到后按 session_id 路由到 WorkflowEventBus,广播给前端 SSE 订阅者。

@activity.defn(name="emit_workflow_event")
async def emit_workflow_event(event_data: dict[str, Any]) -> dict[str, Any]:
    """向 L1 推送工作流节点事件(HTTP POST)。

    入参 event_data:
        session_id: str       — L1 路由用
        workflow_id: str      — workflow 标识
        event_type: str       — node_start | node_end | node_error | workflow_started | ...
        callback_url: str     — L1 接收端点 URL
        node_id: str | None
        node_status: str | None
        node_data: dict | None
        error: str | None

    返回:
        {"ok": True/False, "error": str | None}

    失败处理:
        - HTTP 调用失败不阻断 workflow — 只记日志
        - 用短超时(5s)避免拖慢节点执行
        - retry_policy=0 次 — 节点事件不需要重试(丢一两条不影响整体流程)
    """
    callback_url = event_data.get("callback_url")
    if not callback_url:
        return {"ok": False, "error": "callback_url missing"}

    import httpx
    payload = {
        "session_id": event_data.get("session_id", "unknown"),
        "workflow_id": event_data.get("workflow_id", "unknown"),
        "event_type": event_data.get("event_type", "unknown"),
        "node_id": event_data.get("node_id"),
        "node_status": event_data.get("node_status"),
        "node_data": event_data.get("node_data"),
        "error": event_data.get("error"),
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(callback_url, json=payload)
            if resp.status_code != 200:
                logger.warning(
                    "emit_workflow_event: L1 returned %s: %s",
                    resp.status_code, resp.text[:200],
                )
                return {"ok": False, "error": f"L1 status={resp.status_code}"}
        return {"ok": True}
    except Exception as e:
        # 节点事件回传失败不阻断 workflow
        logger.warning(
            "emit_workflow_event failed (non-blocking): %s: %s",
            type(e).__name__, e,
        )
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# 导出供 worker.py 注册
ALL_DAG_ACTIVITIES = [execute_node, emit_workflow_event]
