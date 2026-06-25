"""AgentLoop — Phase 3 子项目 D 双 task + WebSocket 双向.

Spec: docs/superpowers/specs/2026-06-25-phase3-d-agentloop-websocket-design.md

职责:
  - 管理 receive_task (feedback consumer) + react_task (主循环) 生命周期
  - receive_task 收 WebSocket 消息 → 分发 chat/feedback/interrupt
  - react_task 跑 ReActEngine.run() (一次性, 不打断)
  - feedback → Memory.write + asyncio.Queue (即时信号)
  - interrupt → react_task.cancel()
  - 下一轮 ReAct 通过 §5.1 _fetch_memory_hits (现有) 读到 correction

零侵入: 不改 ReActEngine.run() 接口。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FeedbackMessage(BaseModel):
    """用户反馈消息 — 针对当前 ReAct 的修正."""

    type: Literal["feedback"] = "feedback"
    correction: str = Field(min_length=1)
    session_id: str
    target_tool_call_id: str | None = None
    category: Literal[
        "wrong_result", "wrong_tool", "missing_info", "other"
    ] = "other"


class InterruptMessage(BaseModel):
    """用户中断消息 — 取消当前 ReAct run()."""

    type: Literal["interrupt"] = "interrupt"
    session_id: str
    reason: str | None = None


class FeedbackSummary(BaseModel):
    """Feedback 的轻量摘要 — 用于 asyncio.Queue 传递 (不传完整 BaseModel)."""

    correction: str
    memory_id: str
    category: str
    target_tool_call_id: str | None = None

    @classmethod
    def from_message(cls, msg: FeedbackMessage, memory_id: str) -> "FeedbackSummary":
        return cls(
            correction=msg.correction,
            memory_id=memory_id,
            category=msg.category,
            target_tool_call_id=msg.target_tool_call_id,
        )
