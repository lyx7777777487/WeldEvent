"""L2→L3 集成测试 — 验证 execute_node 调真实 L3 ActivityPool。

boundary-pinning §6.2 契约 3/4:
  - Temporal workflow 是 generic DAG runner，按 nodes 调度 Activity
  - 每个 Activity 按 capability 调 L3 ActivityPool

验证链路:
  1. ActivityPool 创建 + IQA/PPA 注册
  2. execute_node 注入 pool 后调真实 L3 IQA（非 mock）
  3. IQA 写 WeldMap（image/quality）
  4. PPA 读 WeldMap（IQA 报告）→ 共享状态验证
  5. 未注册 capability fallback 到 mock
  6. pool 清除后 fallback 到 mock
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pytest

# 确保项目根目录在 path 上
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from controlplane.adapter.dag_activities import configure_activity_pool, execute_node
from executionplane.pool import ActivityPool, create_default_pool
from executionplane.weldmap.in_memory import InMemoryWeldMapClient
from executionplane.weldmap.models import WorkflowId


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

def _make_test_image(width: int = 2048, height: int = 1536) -> np.ndarray:
    """生成符合默认质量标准的测试图像（灰色渐变，满足分辨率/曝光/对焦）。"""
    # 生成有纹理的图像（Laplacian 方差 > 50）
    x = np.linspace(0, 255, width, dtype=np.uint8)
    y = np.linspace(0, 255, height, dtype=np.uint8)
    img = np.outer(y, x).astype(np.uint8)
    # 加纹理让 Laplacian 方差够高
    noise = np.random.RandomState(42).randint(0, 30, size=(height, width), dtype=np.uint8)
    img = np.clip(img.astype(int) + noise, 0, 255).astype(np.uint8)
    # 转 3 通道
    img = np.stack([img, img, img], axis=-1)
    return img


def _make_node_input(
    node_id: str = "n1",
    capability: str = "iqa",
    workflow_id: str = "wf-test-001",
    image: np.ndarray | None = None,
    node_type: str = "tool_task",
) -> dict:
    """构造 execute_node 的入参。"""
    node_input: dict = {
        "node_id": node_id,
        "type": node_type,
        "capability": capability,
        "input": {},
        "workflow_id": workflow_id,
        "caller_context": {
            "caller_type": "activity",
            "case_id": "case-test",
            "node_id": node_id,
            "session_id": "sess-test",
        },
    }
    if image is not None:
        node_input["input"]["image_array"] = image
    return node_input


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

class TestActivityPoolCreation:
    """验证 ActivityPool 创建 + 注册。"""

    def test_create_default_pool_registers_iqa_ppa(self):
        """create_default_pool 应注册 IQA + PPA。"""
        pool = create_default_pool()
        assert pool.supports("iqa") is True
        assert pool.supports("ppa") is True
        # 别名也能解析
        assert pool.supports("defect_detection") is True
        assert pool.supports("preprocess") is True

    def test_pool_shares_weldmap_across_activities(self):
        """IQA 和 PPA 应共享同一个 WeldMapClient 实例。"""
        pool = create_default_pool()
        iqa = pool.resolve("iqa")
        ppa = pool.resolve("ppa")
        assert iqa is not None
        assert ppa is not None
        # 同一 WeldMap 实例（IQA 写入 PPA 能读到）
        assert iqa._weldmap is ppa._weldmap
        assert iqa._weldmap is pool.weldmap

    def test_unregistered_capability_returns_none(self):
        """未注册的 capability（如 mea）resolve 返回 None。"""
        pool = create_default_pool()
        assert pool.supports("mea") is False
        assert pool.resolve("mea") is None


class TestExecuteNodeDispatch:
    """验证 execute_node 优先调 L3 ActivityPool。"""

    def setup_method(self):
        """每个测试前注入新 pool。"""
        self.pool = create_default_pool()
        configure_activity_pool(self.pool)

    def teardown_method(self):
        """每个测试后清除 pool（隔离）。"""
        configure_activity_pool(None)

    @pytest.mark.asyncio
    async def test_execute_node_dispatches_to_real_iqa(self):
        """execute_node 注入 pool 后调真实 IQA（非 mock_result）。"""
        image = _make_test_image()
        node_input = _make_node_input(
            node_id="n1",
            capability="iqa",
            workflow_id="wf-iqa-test",
            image=image,
        )
        result = await execute_node(node_input)

        # 应返回真实 IQA 结果（有 route_decision，不是 mock_result）
        assert result["status"] in ("OK", "MARGINAL", "NG")
        assert "mock_result" not in result.get("data", {})
        assert "route_decision" in result.get("data", {})

    @pytest.mark.asyncio
    async def test_execute_node_dispatches_via_alias(self):
        """capability='defect_detection' 别名应解析到 IQA。"""
        image = _make_test_image()
        node_input = _make_node_input(
            capability="defect_detection",
            workflow_id="wf-alias-test",
            image=image,
        )
        result = await execute_node(node_input)
        assert result["status"] in ("OK", "MARGINAL", "NG")
        assert "route_decision" in result.get("data", {})

    @pytest.mark.asyncio
    async def test_iqa_writes_to_weldmap(self):
        """IQA 执行后 WeldMap 应有 image/quality 记录。"""
        image = _make_test_image()
        wf_id = "wf-weldmap-write"
        node_input = _make_node_input(
            capability="iqa",
            workflow_id=wf_id,
            image=image,
        )
        await execute_node(node_input)

        # 从 pool 的共享 WeldMap 读回
        report = await self.pool.weldmap.read_image_quality(WorkflowId(wf_id))
        assert report is not None, "IQA 应写入 WeldMap image/quality"
        assert report.route_decision in ("AUTO_PASS", "SUGGEST_REVIEW", "MANDATORY_REVIEW", "REJECT")
        assert report.inspected_by == "iqa_activity"


class TestIqaPpaChain:
    """验证 IQA → PPA 通过共享 WeldMap 传递状态。"""

    def setup_method(self):
        self.pool = create_default_pool()
        configure_activity_pool(self.pool)

    def teardown_method(self):
        configure_activity_pool(None)

    @pytest.mark.asyncio
    async def test_ppa_reads_iqa_report_from_weldmap(self):
        """PPA 应从共享 WeldMap 读 IQA 报告（链路验证）。"""
        image = _make_test_image()
        wf_id = "wf-chain-test"

        # 1. 先跑 IQA（写 WeldMap）
        iqa_input = _make_node_input(
            node_id="n1",
            capability="iqa",
            workflow_id=wf_id,
            image=image,
        )
        iqa_result = await execute_node(iqa_input)
        assert iqa_result["status"] in ("OK", "MARGINAL")

        # 2. 再跑 PPA（读 WeldMap 的 IQA 报告）
        ppa_input = _make_node_input(
            node_id="n2",
            capability="ppa",
            workflow_id=wf_id,
            image=image,
        )
        ppa_result = await execute_node(ppa_input)

        # PPA 应成功读到 IQA 报告（非 "未找到IQA报告" 错误）
        assert ppa_result["status"] in ("OK", "MARGINAL", "NG")
        assert "未找到IQA报告" not in (ppa_result.get("error") or "")
        # PPA 数据应含 iqa_confidence（证明读了 IQA 报告）
        assert "iqa_confidence" in ppa_result.get("data", {})

    @pytest.mark.asyncio
    async def test_ppa_without_iqa_returns_error(self):
        """未先跑 IQA 时 PPA 应返回错误（读不到报告）。"""
        image = _make_test_image()
        wf_id = "wf-ppa-only"
        ppa_input = _make_node_input(
            capability="ppa",
            workflow_id=wf_id,
            image=image,
        )
        result = await execute_node(ppa_input)
        # 未找到 IQA 报告 → ERROR
        assert result["status"] == "ERROR"
        assert "IQA" in (result.get("error") or "")


class TestFallbackBehavior:
    """验证未注册 capability 和 pool 清除后走 mock。"""

    def setup_method(self):
        self.pool = create_default_pool()
        configure_activity_pool(self.pool)

    def teardown_method(self):
        configure_activity_pool(None)

    @pytest.mark.asyncio
    async def test_unregistered_capability_falls_back_to_mock(self):
        """未注册的 capability（mea）应走 mock fallback。"""
        node_input = _make_node_input(
            capability="mea",
            workflow_id="wf-mock-test",
        )
        result = await execute_node(node_input)
        # mock 路径返回 mock_result
        assert result["status"] == "OK"
        assert "mock_result" in result.get("data", {})
        assert result["data"]["mock_result"] == "mea_activity_executed"

    @pytest.mark.asyncio
    async def test_pool_cleared_falls_back_to_mock(self):
        """configure_activity_pool(None) 后 IQA 应走 mock。"""
        configure_activity_pool(None)
        node_input = _make_node_input(
            capability="iqa",
            workflow_id="wf-no-pool",
        )
        result = await execute_node(node_input)
        # pool 清除后走 mock
        assert result["status"] == "OK"
        assert "mock_result" in result.get("data", {})

    @pytest.mark.asyncio
    async def test_unknown_capability_returns_error(self):
        """完全未知的 capability 应返回 ERROR。"""
        node_input = _make_node_input(
            capability="nonexistent_capability",
            workflow_id="wf-unknown",
        )
        result = await execute_node(node_input)
        assert result["status"] == "ERROR"
        assert "Unknown capability" in (result.get("error") or "")


class TestHumanTaskGate:
    """验证 human_task 仍由 workflow 层 HumanGateSignal 处理（不走 L3）。"""

    def setup_method(self):
        configure_activity_pool(create_default_pool())

    def teardown_method(self):
        configure_activity_pool(None)

    @pytest.mark.asyncio
    async def test_human_task_without_gate_approved_returns_error(self):
        """human_task 未带 gate_approved 应返回 ERROR（P2-6 fix）。"""
        node_input = _make_node_input(
            capability="hca",
            node_type="human_task",
            workflow_id="wf-human",
        )
        result = await execute_node(node_input)
        assert result["status"] == "ERROR"
        assert "gate_approved" in (result.get("error") or "")

    @pytest.mark.asyncio
    async def test_human_task_with_gate_approved_returns_ok(self):
        """human_task 带 gate_approved=True 应返回 OK。"""
        node_input = _make_node_input(
            capability="hca",
            node_type="human_task",
            workflow_id="wf-human-ok",
        )
        node_input["gate_approved"] = True
        result = await execute_node(node_input)
        assert result["status"] == "OK"
        assert "approved via HumanGateSignal" in result.get("data", {}).get("review", "")
