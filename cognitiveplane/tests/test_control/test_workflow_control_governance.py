"""WorkflowControlTool governance action 接入测试.

验证: NL -> control_workflow(action=governance) -> batch_signals signal -> governance 模块 全链.
覆盖 8 governance action: 5 第一档(直接发) + 3 第二档(确认门).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from cognitiveplane.control.engine.approval import ApprovalStore
from cognitiveplane.control.tools.workflow_control import WorkflowControlTool


class FakeConnector:
    """模拟 EventConnector - 记录 send_signal 调用."""

    def __init__(self, ok: bool = True):
        self.sent: list[tuple[str, str, Any]] = []
        self._ok = ok

    async def send_signal(self, workflow_id: str, signal_name: str, args: Any = None):
        self.sent.append((workflow_id, signal_name, args))
        return self._ok


def _make_tool(connector=None, approval_store=None):
    if connector is None:
        connector = FakeConnector()
    deps = SimpleNamespace(bridge=SimpleNamespace(event_connector=connector))
    return WorkflowControlTool(deps, approval_store=approval_store), connector


# ── 第一档: 直接发 signal (无确认门) ──────────────────────────────

@pytest.mark.asyncio
async def test_batch_hold_sends_batch_signals_signal():
    """batch_hold -> batch_signals signal 带 batch_hold payload."""
    tool, conn = _make_tool()
    r = await tool.execute(
        action="batch_hold", workflow_id="wf-1",
        batch_id="B-12", reason="可疑", evidence="x",
    )
    assert r.error is None
    assert r.output["ok"] is True
    assert "扣留批次 B-12" in r.output["message"]
    # signal: workflow_id, "batch_signals", [[{type:batch_hold,...}]]
    wf, sig, args = conn.sent[0]
    assert wf == "wf-1" and sig == "batch_signals"
    payload = args[0][0]
    assert payload["type"] == "batch_hold"
    assert payload["batch_id"] == "B-12"


@pytest.mark.asyncio
async def test_pause_scope_tier1_no_confirm():
    """pause_scope 是第一档, 不弹确认卡."""
    tool, _ = _make_tool()
    r = await tool.execute(
        action="pause_scope", workflow_id="wf-1",
        scope_type="station", scope_id="node-3", reason="检查",
    )
    assert r.output["ok"] is True
    assert "暂停" in r.output["message"]


@pytest.mark.asyncio
async def test_missing_id_field_reports_clearly():
    """缺必填标识 -> 明确报错 (不静默)."""
    tool, _ = _make_tool()
    r = await tool.execute(action="batch_hold", workflow_id="wf-1", reason="x")
    assert r.error is not None
    assert "batch_id" in r.error


@pytest.mark.asyncio
async def test_bridge_failure_suggests_retry():
    """bridge 返回 False -> 明确报错 + 提示重试."""
    conn = FakeConnector(ok=False)
    tool, _ = _make_tool(connector=conn)
    r = await tool.execute(
        action="batch_hold", workflow_id="wf-1", batch_id="B-1",
    )
    assert r.error is not None
    assert "重试" in r.error


# ── 第二档: 确认门 (revoke/override/case_correction) ──────────────

@pytest.mark.asyncio
async def test_revoke_approval_blocked_until_confirmed():
    """revoke_approval 未确认 -> 不发 signal."""
    store = ApprovalStore()
    tool, conn = _make_tool(approval_store=store)
    # 并行: 先发起 (会阻塞), 再 resolve 确认
    async def _resolve():
        await asyncio.sleep(0.05)
        # 找 pending approval
        apid = next(iter(store._pending))
        store.resolve(apid, "确认执行")
    task = asyncio.create_task(_resolve())
    r = await tool.execute(
        action="revoke_approval", workflow_id="wf-1",
        node_id="n-3", revoker_id="u1", reason="误判", artifact_status="draft",
    )
    await task
    assert r.output["ok"] is True
    assert len(conn.sent) == 1  # 确认后才发


@pytest.mark.asyncio
async def test_revoke_approval_rejected_no_signal():
    """revoke_approval 用户取消 -> 不发 signal, ok=False."""
    store = ApprovalStore()
    tool, conn = _make_tool(approval_store=store)
    async def _reject():
        await asyncio.sleep(0.05)
        apid = next(iter(store._pending))
        store.resolve(apid, "取消")
    task = asyncio.create_task(_reject())
    r = await tool.execute(
        action="revoke_approval", workflow_id="wf-1",
        node_id="n-3", revoker_id="u1", reason="x",
    )
    await task
    assert r.output["ok"] is False
    assert len(conn.sent) == 0  # 没发


@pytest.mark.asyncio
async def test_tier2_no_approval_store_degrades_nonblocking():
    """approval_store=None -> 第二档降级非阻塞 (不卡死 agent)."""
    tool, conn = _make_tool(approval_store=None)
    r = await tool.execute(
        action="ground_truth_override", workflow_id="wf-1",
        node_id="n-1", inspector_id="iqc1", forced_verdict="NG", reason="x",
    )
    assert r.output["ok"] is True
    assert len(conn.sent) == 1


# ── 人话反馈: 不暴露内部术语 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_feedback_human_readable_no_jargon():
    """反馈消息用人话, 不含 sig_type/batch_signals 等内部词."""
    tool, _ = _make_tool()
    r = await tool.execute(
        action="relabel_request", workflow_id="wf-1",
        node_id="n-1", artifact_id="a-1",
        original_label="OK", corrected_label="NG", reason="标错",
    )
    msg = r.output["message"]
    for jargon in ["batch_signals", "sig_type", "relabel_request", "_apply_pending"]:
        assert jargon not in msg
    assert "OK" in msg and "NG" in msg


def test_schema_exposes_governance_params():
    """schema 必须暴露 governance 参数字段 (否则 LLM 看不到无法填).

    回归守护: 之前 schema 只有 6 字段 (action/workflow_id/node_id/key/value/new_spec),
    agent 调 batch_hold 时 LLM 不知道有 batch_id 字段可填 -> 永远缺参数.
    """
    from types import SimpleNamespace
    tool, _ = _make_tool()
    props = tool.parameters_schema["properties"]
    # governance 必需的字段
    required = [
        "batch_id", "scope_type", "scope_id", "reason",
        "inspector_id", "forced_verdict", "original_label", "corrected_label",
        "new_reviewer_id", "standard_id", "old_version", "new_version",
        "case_id", "error_type", "correction",
    ]
    missing = [f for f in required if f not in props]
    assert not missing, f"schema 缺字段: {missing}"
    assert len(props) >= 20, f"字段太少: {len(props)}"


@pytest.mark.asyncio
async def test_tier2_rejected_no_signal_clean():
    """第二档用户拒绝 -> ok=False 且不发 signal (干净版, 排除之前引用混乱)."""
    store = ApprovalStore()
    tool, conn = _make_tool(approval_store=store)

    async def _reject():
        await asyncio.sleep(0.05)
        apid = next(iter(store._pending))
        store.resolve(apid, "取消")

    asyncio.create_task(_reject())
    r = await tool.execute(
        action="revoke_approval", workflow_id="wf-1",
        node_id="n-3", revoker_id="u1", reason="x",
    )
    assert r.output["ok"] is False
    assert len(conn.sent) == 0
    assert "未确认" in r.output["message"]


@pytest.mark.asyncio
async def test_all_governance_actions_backend_chain():
    """全部 13 个 governance action 后端链路直连验证 (绕过 LLM 参数提取)."""
    from types import SimpleNamespace
    conn = FakeConnector()
    tool = WorkflowControlTool(
        SimpleNamespace(bridge=SimpleNamespace(event_connector=conn)),
        approval_store=None,  # 降级非阻塞, 直接发
    )
    cases = [
        ("pause_scope", {"scope_type": "station", "scope_id": "n1", "reason": "x"}),
        ("resume_scope", {"scope_type": "station", "scope_id": "n1"}),
        ("batch_hold", {"batch_id": "B1", "reason": "x"}),
        ("release_hold", {"batch_id": "B1"}),
        ("rework_batch", {"batch_id": "B1"}),
        ("quarantine_batch", {"batch_id": "B1"}),
        ("relabel_request", {"node_id": "n1", "artifact_id": "a1",
                             "original_label": "NG", "corrected_label": "OK", "reason": "x"}),
        ("delegate_review", {"target": "rn", "new_reviewer_id": "i1", "reason": "x"}),
        ("standard_update", {"standard_id": "S1", "old_version": "1",
                             "new_version": "2", "effective_date": "d", "diff": "x"}),
        ("human_review", {"node_id": "rn", "decision": "approve"}),
        ("revoke_approval", {"node_id": "n1", "revoker_id": "u", "reason": "x", "artifact_status": "draft"}),
        ("ground_truth_override", {"node_id": "n1", "inspector_id": "i", "forced_verdict": "NG", "reason": "x"}),
        ("case_library_correction", {"case_id": "c1", "error_type": "mislabel", "correction": "fix"}),
    ]
    for action, params in cases:
        r = await tool.execute(action=action, workflow_id="wf-test", **params)
        assert r.error is None, f"{action} failed: {r.error}"
        assert r.output.get("ok") is True, f"{action} not ok: {r.output}"
        assert conn.sent, f"{action} no signal sent"
    assert len(conn.sent) == 13
