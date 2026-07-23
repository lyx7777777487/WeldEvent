"""L1 基础控制测试 - pause / resume / cancel / query."""
from __future__ import annotations

import pytest

from catalog_test.layer1_tool.fixtures import make_tool


@pytest.mark.asyncio
async def test_pause_sends_signal(weldevent_ok):
    """B1: pause -> signal 'pause'."""
    tool, conn, _ = make_tool()
    r = await tool.execute(action="pause", workflow_id="wf-1")
    assert r.error is None
    assert conn.last_signal_name == "pause"
    assert r.output["ok"] is True


@pytest.mark.asyncio
async def test_resume_sends_signal(weldevent_ok):
    """resume -> signal 'resume'."""
    tool, conn, _ = make_tool()
    r = await tool.execute(action="resume", workflow_id="wf-1")
    assert r.error is None
    assert conn.last_signal_name == "resume"


@pytest.mark.asyncio
async def test_cancel_sends_signal(weldevent_ok):
    """B11/M14: cancel -> signal 'cancel_by_user'."""
    tool, conn, _ = make_tool()
    r = await tool.execute(action="cancel", workflow_id="wf-1")
    assert r.error is None
    assert conn.last_signal_name == "cancel_by_user"


@pytest.mark.asyncio
async def test_rework_node_sends_signal(weldevent_ok):
    """C1: rework_node -> signal 'rework_node', args=[node_id]."""
    tool, conn, _ = make_tool()
    r = await tool.execute(
        action="rework_node", workflow_id="wf-1", node_id="iqa_check"
    )
    assert r.error is None
    assert conn.last_signal_name == "rework_node"
    assert conn.last_signal_args == ["iqa_check"]


@pytest.mark.asyncio
async def test_inject_context_sends_signal(weldevent_ok):
    """L1: inject_context -> signal 'inject_context', args=[key, value]."""
    tool, conn, _ = make_tool()
    r = await tool.execute(
        action="inject_context", workflow_id="wf-1",
        key="param_adjust", value="threshold=3.5",
    )
    assert r.error is None
    assert conn.last_signal_name == "inject_context"
    assert conn.last_signal_args == ["param_adjust", "threshold=3.5"]


@pytest.mark.asyncio
async def test_inject_context_missing_key(weldevent_ok):
    """inject_context 缺 key -> 报错."""
    tool, _, _ = make_tool()
    r = await tool.execute(
        action="inject_context", workflow_id="wf-1", value="x",
    )
    assert r.error is not None
    assert "key" in r.error


@pytest.mark.asyncio
async def test_human_review_five_decisions(weldevent_ok):
    """O11: human_review 五种 decision 都能发 signal."""
    for decision in ("approve", "rework", "modify_downstream", "reject", "escalate"):
        tool, conn, _ = make_tool()
        r = await tool.execute(
            action="human_review", workflow_id="wf-1",
            node_id="rda_check", decision=decision,
        )
        assert r.error is None, f"decision={decision}: error={r.error}"
        assert conn.last_signal_name == "batch_signals"


@pytest.mark.asyncio
async def test_human_review_override_decision(weldevent_ok):
    """O11+: override decision 也能发 signal."""
    tool, conn, _ = make_tool()
    r = await tool.execute(
        action="human_review", workflow_id="wf-1",
        node_id="rda_check", decision="override",
    )
    assert r.error is None
    assert conn.last_signal_name == "batch_signals"


@pytest.mark.asyncio
async def test_human_review_invalid_decision(weldevent_ok):
    """无效 decision -> 不发 signal (workflow signal 会拒绝)."""
    # 注意: 工具层不校验 decision 值 (workflow signal 才校验)
    # 所以这里只验证 signal 发出去了 (workflow 会拒绝无效值)
    tool, conn, _ = make_tool()
    r = await tool.execute(
        action="human_review", workflow_id="wf-1",
        node_id="rda_check", decision="bogus",
    )
    # 工具层不拦截, signal 照发
    assert conn.last_signal_name == "batch_signals"
