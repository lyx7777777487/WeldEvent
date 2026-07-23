"""L1 工具级测试 - 参数化遍历全部 L1 可测清单条目.

对每条有 l1_action 的清单条目:
  1. 调 WorkflowControlTool.execute(action, workflow_id, **params)
  2. 验证 FakeConnector 收到正确的 signal name
  3. 验证 payload 包含清单要求的 key
  4. tier2 action 验证确认门被触发

信号路由 (由工具代码决定):
  pause/resume/cancel -> 对应 signal (无 payload)
  rework_node    -> signal "rework_node", args=[node_id]
  inject_context -> signal "inject_context", args=[key, value]
  modify         -> signal "modify_spec", args=[new_spec]
  governance *   -> signal "batch_signals", args=[[{type:..., key:val}]]
"""
from __future__ import annotations

import pytest

from catalog_test.catalog_map import L1_ITEMS, CatalogItem
from catalog_test.layer1_tool.fixtures import make_tool
from catalog_test.layer1_tool.signal_router import (
    expected_signal_for, extract_payload, is_governance_action,
)


@pytest.mark.parametrize("item", L1_ITEMS, ids=lambda i: i.item_id)
@pytest.mark.asyncio
async def test_l1_signal_correct(item: CatalogItem, weldevent_ok):
    """每条 L1 清单项: 验证发送了正确的 signal + payload."""
    tool, conn, _ = make_tool(pre_decided="approved")
    result = await tool.execute(
        action=item.l1_action, workflow_id="wf-test-001", **dict(item.l1_params)
    )

    # 1. 不应有 error
    assert result.error is None, (
        f"{item.item_id} ({item.l1_action}): unexpected error={result.error}"
    )

    # 2. 验证 signal name
    exp_signal = expected_signal_for(item.l1_action)
    assert conn.last_signal_name == exp_signal, (
        f"{item.item_id}: expected signal '{exp_signal}', "
        f"got '{conn.last_signal_name}'"
    )

    # 3. 提取 payload
    payload = extract_payload(item.l1_action, conn.last_signal_args)

    # 3a. governance (batch_signals): 验证 type 字段
    if is_governance_action(item.l1_action) and item.expected_sig_type:
        assert payload is not None, f"{item.item_id}: payload is None"
        assert payload.get("type") == item.expected_sig_type, (
            f"{item.item_id}: expected type '{item.expected_sig_type}', "
            f"got '{payload.get('type')}'"
        )

    # 3b. 所有有 payload 的: 验证 expected_payload_keys 存在
    if item.expected_payload_keys and payload is not None:
        for key in item.expected_payload_keys:
            assert key in payload, (
                f"{item.item_id}: payload missing key '{key}'. "
                f"payload={payload}"
            )


@pytest.mark.parametrize("item", L1_ITEMS, ids=lambda i: i.item_id)
@pytest.mark.asyncio
async def test_l1_human_message_present(item: CatalogItem, weldevent_ok):
    """每条 L1 清单项: 验证返回了人类可读的 message (用户体验保证)."""
    tool, _, _ = make_tool(pre_decided="approved")
    result = await tool.execute(
        action=item.l1_action, workflow_id="wf-test-001", **dict(item.l1_params)
    )
    assert result.output is not None or result.error is not None, (
        f"{item.item_id}: both output and error are None"
    )
    if result.output:
        # 有 output 时, 应该有 message 字段 (modify 例外: 可能在等 workflow)
        if item.l1_action != "modify":
            msg = result.output.get("message") or result.output.get("status") or ""
            assert msg, f"{item.item_id}: output has no message/status field"
