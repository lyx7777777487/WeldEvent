"""L1 确认门测试 - tier2 action (revoke/override/case_correction/quarantine).

验证:
  1. tier2 action 确认后才发 signal (不静默执行)
  2. 用户拒绝 -> 不发 signal, 返回 "未确认"
  3. 非 tier2 action 不触发确认门
"""
from __future__ import annotations

import pytest

from catalog_test.catalog_map import L1_ITEMS
from catalog_test.layer1_tool.fixtures import make_tool


TIER2_IDS = [i.item_id for i in L1_ITEMS if i.tier2]
TIER1_IDS = [i.item_id for i in L1_ITEMS if not i.tier2 and i.l1_action]


@pytest.mark.parametrize("item", [i for i in L1_ITEMS if i.tier2], ids=lambda i: i.item_id)
@pytest.mark.asyncio
async def test_tier2_approved_sends_signal(item, weldevent_ok):
    """tier2: 用户确认 -> 发 signal."""
    tool, conn, store = make_tool(pre_decided="approved")
    result = await tool.execute(
        action=item.l1_action, workflow_id="wf-t2-001", **dict(item.l1_params)
    )
    assert result.error is None, f"{item.item_id}: approved but error={result.error}"
    assert conn.last_signal is not None, f"{item.item_id}: no signal sent after approval"
    assert len(store.requests) == 1, f"{item.item_id}: confirmation not requested"


@pytest.mark.parametrize("item", [i for i in L1_ITEMS if i.tier2], ids=lambda i: i.item_id)
@pytest.mark.asyncio
async def test_tier2_rejected_no_signal(item, weldevent_ok):
    """tier2: 用户拒绝 -> 不发 signal."""
    tool, conn, store = make_tool(pre_decided="rejected")
    result = await tool.execute(
        action=item.l1_action, workflow_id="wf-t2-002", **dict(item.l1_params)
    )
    assert conn.last_signal is None, (
        f"{item.item_id}: signal sent despite rejection!"
    )
    assert result.output is not None, f"{item.item_id}: no output on rejection"
    assert "未确认" in (result.output.get("message") or "") or result.output.get("ok") is False


@pytest.mark.parametrize("item", [i for i in L1_ITEMS if not i.tier2 and i.l1_action], ids=lambda i: i.item_id)
@pytest.mark.asyncio
async def test_tier1_no_confirm_gate(item, weldevent_ok):
    """非 tier2: 不触发确认门, 直接发 signal."""
    tool, conn, store = make_tool(pre_decided="approved")
    result = await tool.execute(
        action=item.l1_action, workflow_id="wf-t1-001", **dict(item.l1_params)
    )
    if result.error is None:
        assert len(store.requests) == 0, (
            f"{item.item_id}: tier1 triggered confirm gate unexpectedly"
        )
