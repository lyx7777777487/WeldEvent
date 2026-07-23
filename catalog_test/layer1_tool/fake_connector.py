"""FakeConnector - 模拟 EventConnector, 记录所有 send_signal 调用."""
from __future__ import annotations

import asyncio
from typing import Any


class FakeConnector:
    """模拟 EventConnector.send_signal - 记录调用, 不发真实 Temporal signal."""

    def __init__(self, ok: bool = True, query_result: dict | None = None) -> None:
        self.sent: list[tuple[str, str, Any]] = []
        self._ok = ok
        # modify_workflow 会轮询 query_status; 默认返回 MODIFIED 让它快速退出
        self._query_result = query_result if query_result is not None else {
            "status": "MODIFIED",
            "modified_spec": {"nodes": []},
            "preserved_nodes": ["iqa_check"],
        }

    async def send_signal(
        self, workflow_id: str, signal_name: str, args: Any = None
    ) -> bool:
        self.sent.append((workflow_id, signal_name, args))
        return self._ok

    async def query_status(self, workflow_id: str) -> dict[str, Any] | None:
        return self._query_result

    @property
    def last_signal(self) -> tuple[str, str, Any] | None:
        return self.sent[-1] if self.sent else None

    @property
    def last_signal_name(self) -> str | None:
        return self.last_signal[1] if self.last_signal else None

    @property
    def last_signal_args(self) -> Any:
        return self.last_signal[2] if self.last_signal else None

    def reset(self) -> None:
        self.sent.clear()

    def signals_named(self, name: str) -> list[tuple[str, str, Any]]:
        return [s for s in self.sent if s[1] == name]


class FakeApprovalStore:
    """模拟 ApprovalStore - 用真实 asyncio.Event (已 set) 模拟用户已决策."""

    def __init__(self, pre_decided: str = "approved") -> None:
        self._pre_decided = pre_decided
        self.requests: list[dict] = []

    def create(self, approval_id, session_id, iteration, tool_name,
               arguments, summary) -> Any:
        event = asyncio.Event()
        event.set()
        req = type("Req", (), {
            "approval_id": approval_id, "session_id": session_id,
            "iteration": iteration, "tool_name": tool_name,
            "arguments": arguments, "summary": summary,
            "decision": self._pre_decided, "feedback": None,
            "event": event,
        })()
        self.requests.append({
            "approval_id": approval_id, "tool_name": tool_name,
            "arguments": arguments, "summary": summary,
            "decision": self._pre_decided,
        })
        return req

    def resolve(self, approval_id, decision, feedback=None) -> bool:
        return True

    def get(self, approval_id):
        return None
