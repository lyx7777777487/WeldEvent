"""L1 工具级测试 fixtures - 构造 WorkflowControlTool + FakeConnector."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from catalog_test.layer1_tool.fake_connector import FakeConnector, FakeApprovalStore


def make_tool(connector=None, approval_store=None, pre_decided="approved"):
    """构造 WorkflowControlTool + FakeConnector + (可选) FakeApprovalStore."""
    from cognitiveplane.control.tools.workflow_control import WorkflowControlTool
    from cognitiveplane.control.engine.approval import ApprovalStore

    if connector is None:
        connector = FakeConnector()
    deps = SimpleNamespace(bridge=SimpleNamespace(event_connector=connector))

    if approval_store is None:
        approval_store = FakeApprovalStore(pre_decided=pre_decided)

    tool = WorkflowControlTool(deps, approval_store=approval_store)
    return tool, connector, approval_store


def extract_batch_signals_payload(args: Any) -> dict[str, Any] | None:
    """从 batch_signals signal 的 args 里提取 payload dict.

    connector.send_signal(wf, "batch_signals", [[{type: ..., key: val}]])
    -> 返回 {type: ..., key: val}
    """
    if args is None:
        return None
    # args 可能是 [[payload]] 或 [payload]
    try:
        lst = args[0] if isinstance(args, (list, tuple)) else args
        payload = lst[0] if isinstance(lst, (list, tuple)) and lst else lst
        if isinstance(payload, dict):
            return payload
    except (IndexError, TypeError):
        pass
    return None
