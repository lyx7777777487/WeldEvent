"""DAG Runner Activities — 按 WorkflowNode.capability 分派执行。

boundary-pinning §6.2: generic DAG runner 按 nodes 顺序调度 Activity，每个
Activity 按 capability 调 ToolPool。

Phase 4 实现：execute_node 优先调 L3 ActivityPool（真实 IQA/PPA activity）。
若 capability 未在 pool 注册，fallback 到 mock activity（向后兼容测试 + 未实现 activity）。

Source: boundary-pinning §6.2 + §1.3 Activity Pool + §1.4 Tool Pool
"""

from __future__ import annotations

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
        except Exception as e:
            logger.error(
                "L3 ActivityPool dispatch failed (node=%s capability=%s): %s: %s",
                node_id, capability, type(e).__name__, e, exc_info=True,
            )
            # P1-2 fix: L3 真实失败直接返回 ERROR，不用 mock 覆盖。
            # mock 结果是假数据，掩盖真实故障会让下游 node 基于错误前提继续执行，
            # 导致结果不可解释。Temporal RetryPolicy(maximum_attempts=3) 会自动
            # 重试瞬时故障；持久性故障应让 workflow 走 on_failure 决策。
            return _to_dict(ActivityOutput(
                status=ActivityStatus.ERROR,
                data={"node_id": node_id, "capability": capability, "l3_error": str(e)},
                error=f"L3 activity execution failed: {type(e).__name__}: {e}",
            ))

    # fallback: mock 分派（capability 未在 ActivityPool 注册）
    activity_name = _CAPABILITY_DISPATCH.get(capability)
    if activity_name is None:
        # 未知 capability — 返回 error
        return _to_dict(ActivityOutput(
            status=ActivityStatus.ERROR,
            data={"node_id": node_id, "capability": capability},
            error=f"Unknown capability: {capability}. Available: {list(_CAPABILITY_DISPATCH.keys())}",
        ))

    # mock 结果（向后兼容）
    return _to_dict(ActivityOutput(
        status=ActivityStatus.OK,
        data={
            "node_id": node_id,
            "capability": capability,
            "dispatched_to": activity_name,
            "input": node_input_data,
            "mock_result": f"{activity_name}_executed",
        },
    ))


def _to_dict(output: ActivityOutput) -> dict[str, Any]:
    """ActivityOutput → JSON 安全 dict（跨 Temporal 边界）。"""
    return {
        "status": output.status.value,
        "data": output.data,
        "error": output.error,
    }


# 导出供 worker.py 注册
ALL_DAG_ACTIVITIES = [execute_node]
