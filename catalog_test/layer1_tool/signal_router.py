"""信号路由辅助 - 根据 action 判断期望的 signal name 和 payload 提取方式."""
from __future__ import annotations

from typing import Any

# governance action -> signal "batch_signals"
_GOVERNANCE_ACTIONS = frozenset({
    "pause_scope", "resume_scope", "batch_hold", "release_hold",
    "rework_batch", "quarantine_batch",
    "relabel_request",
    "delegate_review", "standard_update",
    "human_review",
    "revoke_approval", "ground_truth_override",
    "case_library_correction",
})

# action -> signal name 映射 (由 WorkflowControlTool 代码决定)
_SIGNAL_MAP = {
    "pause": "pause",
    "resume": "resume",
    "cancel": "cancel_by_user",
    "rework_node": "rework_node",
    "inject_context": "inject_context",
    "modify": "modify_spec",
}


def expected_signal_for(action: str) -> str:
    """根据 action 返回期望的 Temporal signal name."""
    if action in _GOVERNANCE_ACTIONS:
        return "batch_signals"
    return _SIGNAL_MAP.get(action, "")


def extract_payload(action: str, args: Any) -> dict[str, Any] | None:
    """从 signal args 提取 payload dict (用于断言 key/type).

    - governance (batch_signals): args=[[{type:..., ...}]] -> {type:..., ...}
    - rework_node: args=[node_id] -> {"node_id": node_id}
    - inject_context: args=[key, value] -> {"key": key, "value": value}
    - pause/resume/cancel: args=None -> None
    - modify: args=[new_spec] -> {"new_spec": new_spec}
    """
    if args is None:
        return None

    if action in _GOVERNANCE_ACTIONS:
        # batch_signals: args = [[payload_dict]]
        try:
            lst = args[0] if isinstance(args, (list, tuple)) else args
            payload = lst[0] if isinstance(lst, (list, tuple)) and lst else lst
            if isinstance(payload, dict):
                return payload
        except (IndexError, TypeError):
            pass
        return None

    if action == "rework_node":
        # args = [node_id]
        try:
            return {"node_id": args[0]}
        except (IndexError, TypeError):
            return None

    if action == "inject_context":
        # args = [key, value]
        try:
            return {"key": args[0], "value": args[1]}
        except (IndexError, TypeError):
            return None

    if action == "modify":
        # args = [new_spec_dict]
        try:
            return {"new_spec": args[0]}
        except (IndexError, TypeError):
            return None

    return None


def is_governance_action(action: str) -> bool:
    """判断 action 是否走 batch_signals 路由 (governance action)."""
    return action in _GOVERNANCE_ACTIONS
