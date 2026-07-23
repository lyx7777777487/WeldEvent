"""L1 参数校验测试 - 缺必填标识 / bridge 失败 / 无效 action."""
from __future__ import annotations

import pytest

from catalog_test.layer1_tool.fixtures import make_tool
from catalog_test.layer1_tool.fake_connector import FakeConnector


@pytest.mark.asyncio
async def test_batch_hold_missing_batch_id(weldevent_ok):
    """batch_hold 缺 batch_id -> 明确报错."""
    tool, _, _ = make_tool()
    r = await tool.execute(action="batch_hold", workflow_id="wf-1", reason="x")
    assert r.error is not None
    assert "batch_id" in r.error


@pytest.mark.asyncio
async def test_pause_scope_missing_scope_id(weldevent_ok):
    """pause_scope 缺 scope_id -> 明确报错."""
    tool, _, _ = make_tool()
    r = await tool.execute(
        action="pause_scope", workflow_id="wf-1",
        scope_type="station", reason="x",
    )
    assert r.error is not None
    assert "scope_id" in r.error


@pytest.mark.asyncio
async def test_rework_node_missing_node_id(weldevent_ok):
    """rework_node 缺 node_id -> 明确报错."""
    tool, _, _ = make_tool()
    r = await tool.execute(action="rework_node", workflow_id="wf-1")
    assert r.error is not None
    assert "node_id" in r.error


@pytest.mark.asyncio
async def test_revoke_missing_node_id(weldevent_ok):
    """revoke_approval 缺 node_id -> 明确报错."""
    tool, _, _ = make_tool()
    r = await tool.execute(
        action="revoke_approval", workflow_id="wf-1",
        revoker_id="qc-1", reason="x",
    )
    assert r.error is not None
    assert "node_id" in r.error


@pytest.mark.asyncio
async def test_human_review_missing_node_id(weldevent_ok):
    """human_review 缺 node_id -> 明确报错."""
    tool, _, _ = make_tool()
    r = await tool.execute(
        action="human_review", workflow_id="wf-1", decision="approve",
    )
    assert r.error is not None
    assert "node_id" in r.error


@pytest.mark.asyncio
async def test_invalid_action(weldevent_ok):
    """无效 action -> 明确报错."""
    tool, _, _ = make_tool()
    r = await tool.execute(action="nonexistent", workflow_id="wf-1")
    assert r.error is not None
    assert "invalid" in r.error.lower()


@pytest.mark.asyncio
async def test_missing_workflow_id(weldevent_ok):
    """缺 workflow_id -> 明确报错."""
    tool, _, _ = make_tool()
    r = await tool.execute(action="pause", workflow_id="")
    assert r.error is not None
    assert "workflow_id" in r.error.lower()


@pytest.mark.asyncio
async def test_bridge_failure_suggests_retry(weldevent_ok):
    """bridge 返回 False -> 明确报错 + 提示重试."""
    conn = FakeConnector(ok=False)
    tool, _, _ = make_tool(connector=conn)
    r = await tool.execute(
        action="batch_hold", workflow_id="wf-1", batch_id="B-1",
    )
    assert r.error is not None
    assert "重试" in r.error


@pytest.mark.asyncio
async def test_bridge_failure_basic_control(weldevent_ok):
    """KNOWN BUG: pause/resume/cancel 不检查 send_signal 返回值.

    _send_signal 调 connector.send_signal() 后不检查返回值,
    即使 bridge 返回 False 也报 ok=True. governance action 会检查 (正确),
    但基础控制不检查. 此测试记录当前行为 (bug), 修复后应改为 assert error.
    """
    conn = FakeConnector(ok=False)
    tool, _, _ = make_tool(connector=conn)
    r = await tool.execute(action="pause", workflow_id="wf-1")
    # BUG: 当前不检查返回值, 报 ok=True
    assert r.output is not None
    # 信号确实发了 (connector 记录了)
    assert conn.last_signal_name == "pause"
    # TODO: 修复 _send_signal 检查返回值后, 改为:
    #   assert r.error is not None
    #   assert "失败" in r.error or "failed" in r.error.lower()


@pytest.mark.asyncio
async def test_no_bridge(weldevent_ok):
    """bridge 未配置 -> 明确报错."""
    from types import SimpleNamespace
    from cognitiveplane.control.tools.workflow_control import WorkflowControlTool
    deps = SimpleNamespace(bridge=SimpleNamespace(event_connector=None))
    tool = WorkflowControlTool(deps)
    r = await tool.execute(action="pause", workflow_id="wf-1")
    assert r.error is not None
