"""Activity Pool — L3 执行层的能力分派器。

boundary-pinning §1.3/§1.4:
  - Activity Pool 执行 DAG 节点，每个节点声明 capability（不是工具名）
  - 由 Activity Pool 解析到具体实现（Phase 4 同进程实例，Phase 5+ 可接 ToolPool/MCP）

Phase 4 实现：直接持有 BaseActivity 实例，按 capability 名分派。
所有 activity 共享同一个 WeldMapClient 实例（IQA 写入 → PPA 读取）。

与 L2 的边界：
  - L2 execute_node activity 调 ActivityPool.dispatch(node_input)
  - ActivityPool 构造 ActivityInput，调 BaseActivity.run()，返回 ActivityOutput dict
  - L2 不感知 L3 具体实现，只传 capability + input + workflow_context

Source: boundary-pinning §1.3/§1.4/§6.2
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .activities.base import ActivityInput, ActivityOutput, ActivityStatus

from .activities.annotation.activity import AnnotationActivity
from .activities.base import BaseActivity
from .activities.iqa.activity import IqaActivity
from .activities.mea.activity import MeaActivity
from .activities.ppa.activity import PpaActivity
from .activities.rda.activity import RdaActivity
from .capabilities.cv_rules import CVRuleChecker
from .capabilities.mllm_provider import MllmProvider
from .capabilities.numpy_cv_checker import NumpyCVRuleChecker
from .weldmap.client import WeldMapClient
from .weldmap.in_memory import InMemoryWeldMapClient

logger = logging.getLogger(__name__)


# ── capability 别名映射 ──────────────────────────────────────────────
# design_workflow 产出的 capability 名可能多种，统一映射到 L3 activity 名
_CAPABILITY_ALIASES: dict[str, str] = {
    # IQA 别名
    "iqa": "iqa",
    "defect_detection": "iqa",
    "image_quality": "iqa",
    # PPA 别名
    "ppa": "ppa",
    "preprocess": "ppa",
    "image_preprocess": "ppa",
    # MEA（几何测量）
    "mea": "mea",
    "geometry": "mea",
    "measurement": "mea",
    # RDA（缺陷识别）
    "rda": "rda",
    "defect_recognition": "rda",
    # HCA（人工审核）— Phase 4+ 实现真实 activity
    "hca": "hca",
    "human_review": "hca",
    "annotation_write": "hca",
    # Annotation（Label Studio MCP 标注）— 真实实现
    "annotation": "annotation",
    "label_studio": "annotation",
    "annotate_label": "annotation",
    "label_task": "annotation",
}


class ActivityPool:
    """L3 Activity Pool — capability → BaseActivity 实例分派。

    所有 activity 共享同一个 WeldMapClient 实例，实现跨 activity 状态传递：
      - IQA write_image_quality(wf_id, report)
      - PPA read_image_quality(wf_id) → 根据 report 调整预处理策略

    Usage::

        pool = ActivityPool(weldmap=InMemoryWeldMapClient())
        pool.register_iqa(cv_checker=NumpyCVRuleChecker())
        pool.register_ppa()

        # L2 execute_node 调用
        result_dict = await pool.dispatch({
            "node_id": "n1",
            "capability": "iqa",
            "input": {"image_path": "/data/weld.png"},
            "workflow_id": "wf-abc123",
        })
    """

    def __init__(self, weldmap: WeldMapClient | None = None) -> None:
        self._weldmap: WeldMapClient = weldmap or InMemoryWeldMapClient()
        self._activities: dict[str, BaseActivity] = {}

    @property
    def weldmap(self) -> WeldMapClient:
        """共享的 WeldMapClient 实例（供 L2 或测试直接访问）。"""
        return self._weldmap

    # ------------------------------------------------------------------
    # 注册方法
    # ------------------------------------------------------------------

    def register(self, capability: str, activity: BaseActivity) -> None:
        """注册 capability → activity 映射。"""
        canonical = _CAPABILITY_ALIASES.get(capability, capability)
        self._activities[canonical] = activity
        logger.info("ActivityPool registered: %s → %s", capability, activity.activity_name)

    def register_iqa(
        self,
        cv_checker: CVRuleChecker | None = None,
        mllm: MllmProvider | None = None,
    ) -> IqaActivity:
        """便捷注册 IQA Activity。返回实例供测试或 L2 直接访问。"""
        activity = IqaActivity(
            weldmap=self._weldmap,
            cv_checker=cv_checker or NumpyCVRuleChecker(),
            mllm=mllm,
        )
        self.register("iqa", activity)
        return activity

    def register_ppa(self) -> PpaActivity:
        """便捷注册 PPA Activity。返回实例。"""
        activity = PpaActivity(weldmap=self._weldmap)
        self.register("ppa", activity)
        return activity

    def register_mea(self) -> MeaActivity:
        """便捷注册 MEA Activity（几何测量）。返回实例。

        MEA 用确定性 CV 测量焊脚/焊喉/两焊脚差，写入 WeldMap annotations。
        """
        activity = MeaActivity(weldmap=self._weldmap)
        self.register("mea", activity)
        return activity

    def register_rda(self) -> RdaActivity:
        """便捷注册 RDA Activity（表面缺陷识别）。返回实例。

        RDA 用确定性 CV 检测气孔/裂纹/咬边/焊瘤，合并写入 WeldMap annotations。
        """
        activity = RdaActivity(weldmap=self._weldmap)
        self.register("rda", activity)
        return activity

    def register_annotation(self) -> AnnotationActivity:
        """便捷注册 Annotation Activity（Label Studio MCP 标注）。

        AnnotationActivity 不持有 WeldMap 引用（标注状态外置于 Label Studio），
        每次 execute() 从环境变量读取配置并新建短生命周期 MCP client。

        Returns:
            AnnotationActivity 实例（供测试或 L2 直接访问）。
        """
        activity = AnnotationActivity()
        self.register("annotation", activity)
        return activity

    # ------------------------------------------------------------------
    # 分派方法
    # ------------------------------------------------------------------

    def resolve(self, capability: str) -> BaseActivity | None:
        """按 capability 查找 activity 实例（含别名解析）。"""
        canonical = _CAPABILITY_ALIASES.get(capability, capability)
        return self._activities.get(canonical)

    def supports(self, capability: str) -> bool:
        """该 capability 是否已在 pool 注册。"""
        return self.resolve(capability) is not None

    async def dispatch(self, node_input: dict[str, Any]) -> dict[str, Any] | None:
        """分派 node 到对应 activity 执行。

        Args:
            node_input: L2 execute_node 传入的 dict，含:
                - node_id: str
                - capability: str
                - input: dict（业务参数，如 image_path）
                - workflow_id: str（WeldMap 空间标识，由 L2 workflow 注入）

        Returns:
            ActivityOutput 序列化 dict（status/data/error），或 None 表示
            capability 未注册（让 L2 走 fallback mock 路径）。
        """
        capability = node_input.get("capability") or ""
        activity = self.resolve(capability)
        if activity is None:
            return None

        # 构造 L3 ActivityInput
        # P5 fix: 传递 dependency_results，让下游 activity 可回退读取上游结果
        # （WeldMap InMemory 实现重启即丢，或生产 Redis 瞬时不可用时降级）
        workflow_id = node_input.get("workflow_id", "")
        activity_input = ActivityInput(
            control_point_id=node_input.get("node_id", ""),
            workflow_context={
                "workflow_id": workflow_id,
                "case_id": (node_input.get("caller_context") or {}).get("case_id", ""),
                "node_id": node_input.get("node_id", ""),
                "session_id": (node_input.get("caller_context") or {}).get("session_id", ""),
                "dependency_results": node_input.get("dependency_results", {}),
            },
            params=node_input.get("input", {}),
        )

        # 调用 BaseActivity.run()（统一入口，含 on_start/on_complete/on_error 钩子）
        output = await activity.run(activity_input)
        return _to_dict(output)


def create_default_pool(
    weldmap: WeldMapClient | None = None,
    cv_checker: CVRuleChecker | None = None,
    mllm: MllmProvider | None = None,
) -> ActivityPool:
    """创建默认 ActivityPool，注册已实现的 L3 activity（IQA + PPA + Annotation）。

    未实现的 activity（MEA/RDA/VDA/RVA/MTA/HCA）不注册，L2 走 fallback mock。
    Phase 4+ 逐步实现后在此追加注册。

    AnnotationActivity 依赖外部 Label Studio MCP server 进程：
      - 若 server 未启动，execute() 会抛 MCPConnectionError，由 Temporal RetryPolicy 重试。
      - 若不希望默认注册（避免无 MCP server 时拖累启动），可通过环境变量
        ANNOTATION_ACTIVITY_ENABLED=0 关闭。
    """
    pool = ActivityPool(weldmap=weldmap)
    pool.register_iqa(cv_checker=cv_checker, mllm=mllm)
    pool.register_ppa()
    pool.register_mea()
    pool.register_rda()

    # Annotation Activity：默认注册，但可通过环境变量关闭
    annotation_enabled = os.environ.get("ANNOTATION_ACTIVITY_ENABLED", "1").strip()
    if annotation_enabled not in ("0", "false", "False", "no", "NO"):
        pool.register_annotation()
        registered_msg = "IQA + PPA + MEA + RDA + Annotation"
    else:
        registered_msg = "IQA + PPA + MEA + RDA (Annotation disabled by env)"

    logger.info(
        "ActivityPool created: %s registered, WeldMap=%s",
        registered_msg,
        type(pool.weldmap).__name__,
    )
    return pool


def _to_dict(output: ActivityOutput) -> dict[str, Any]:
    """ActivityOutput → JSON 安全 dict（跨 Temporal 边界）。"""
    return {
        "status": output.status.value,
        "data": output.data,
        "error": output.error,
    }


__all__ = [
    "ActivityPool",
    "create_default_pool",
]
