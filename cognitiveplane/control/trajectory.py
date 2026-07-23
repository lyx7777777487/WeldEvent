"""TrajectoryRecorder — 借鉴 trae-agent AgentExecution/AgentStep 体系。

记录 per-step LLM 推理轨迹，与 EventLog（架构层事件溯源）互补：
  - EventLog：架构层事件（worldview 注入、工具调用、approval 请求）
  - Trajectory：LLM 推理层轨迹（step/state/thought/tool_calls/tool_results/reflection/error）

导出端点 GET /sessions/{id}/trajectory 返回完整 JSON。
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentStep:
    """单步 LLM 推理轨迹。

    借鉴 trae-agent AgentStep，保留其核心字段，精简 WeldEvent 不用的字段。
    """
    step_number: int
    state: str  # THINKING | CALLING_TOOL | REFLECTING | COMPLETED | ERROR
    # 本轮给 LLM 的 messages（不含 tool 结果），可能很大，导出时截断
    llm_messages: list[dict[str, Any]] = field(default_factory=list)
    # LLM 原始响应（含 tool_calls 或 content）
    llm_response: dict[str, Any] | None = None
    # 工具调用列表（含 tool_name + arguments）
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # 工具执行结果列表
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    # 反思内容（失败时）
    reflection: str | None = None
    # 错误信息
    error: str | None = None
    # LLM 用量
    llm_usage: dict[str, int] | None = None  # {"prompt_tokens": N, "completion_tokens": N}
    # 时间戳
    timestamp: float = field(default_factory=lambda: _time.time())


@dataclass
class AgentExecution:
    """完整执行轨迹。

    借鉴 trae-agent AgentExecution。
    """
    task: str  # 用户输入
    session_id: str
    steps: list[AgentStep] = field(default_factory=list)
    final_result: str | None = None
    success: bool = False
    total_tokens: int = 0
    execution_time: float = 0.0
    started_at: float = field(default_factory=lambda: _time.time())



@dataclass
class ContinuableSnapshot:
    """Provider-valid checkpoint for crash recovery.

    Source: Pydantic AI Harness step persistence (调研报告 §5.4).
    Op 8 of IMPLEMENTATION_REPORT.

    A snapshot of the LLM message history that is guaranteed to be
    "provider-valid" -- every assistant message containing tool_calls
    has a matching tool-result message following it. This means the
    messages can be replayed to the LLM API without errors after a crash.

    Key constraint: only guarantees provider history legality. Does NOT
    restore capability state (tool registrations, session variables, etc.).
    Those must be rebuilt by the engine on resume.

    The snapshot is taken only when a tool-call/return pair is complete:
    - After an LLM response with NO tool_calls (final answer or thinking)
    - After ALL tool results for the current tool_calls have been appended
    Never taken mid-tool-call (assistant has tool_calls but results not yet
    appended), because that state is invalid for the provider API.
    """
    messages: list[dict[str, Any]] = field(default_factory=list)
    step_number: int = 0
    session_id: str = ""
    timestamp: float = field(default_factory=lambda: _time.time())
    # Token estimate at snapshot time (rough: ~4 chars per token)
    token_estimate: int = 0
    # Human-readable reason for this snapshot (e.g. "tool_pair_complete", "final_answer")
    checkpoint_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "messages": self.messages,
            "step_number": self.step_number,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "token_estimate": self.token_estimate,
            "checkpoint_reason": self.checkpoint_reason,
        }

class TrajectoryRecorder:
    """记录 ReAct 引擎的每一步推理轨迹。

    用法:
        recorder = TrajectoryRecorder(task="...", session_id="...")
        recorder.start()
        # ... ReAct loop ...
        recorder.record_step(step)
        recorder.finalize(result="...", success=True)
        execution = recorder.execution
        # 导出
        json_str = recorder.to_json()
    """

    MAX_STEP_MESSAGES_BYTES = 16384  # 单步 messages 截断上限

    def __init__(self, task: str, session_id: str) -> None:
        self._execution = AgentExecution(task=task, session_id=session_id)
        self._started = False
        # Op 8: ContinuableSnapshot - latest provider-valid checkpoint
        self._latest_snapshot: ContinuableSnapshot | None = None

    @property
    def execution(self) -> AgentExecution:
        return self._execution

    def start(self) -> None:
        self._started = True
        self._execution.started_at = _time.time()

    def record_step(self, step: AgentStep) -> None:
        """记录一个推理步骤。自动截断过大的 llm_messages。"""
        if not self._started:
            self.start()
        # 截断过大的 messages 以控制内存
        self._truncate_step(step)
        self._execution.steps.append(step)

    def finalize(self, result: str | None = None, success: bool = False) -> None:
        """结束记录，汇总 token 用量和总耗时。"""
        self._execution.final_result = result
        self._execution.success = success
        self._execution.execution_time = _time.time() - self._execution.started_at
        self._execution.total_tokens = sum(
            sum(step.llm_usage.values()) if step.llm_usage else 0
            for step in self._execution.steps
        )

    def _truncate_step(self, step: AgentStep) -> None:
        """截断过大的 llm_messages（防止内存爆炸）。"""
        if not step.llm_messages:
            return
        serialized = str(step.llm_messages)
        if len(serialized) <= self.MAX_STEP_MESSAGES_BYTES:
            return
        # 保留最近 4 条 messages（通常是本轮最相关的），其余标记截断
        step.llm_messages = step.llm_messages[-4:]
        step.llm_messages.insert(0, {
            "role": "system",
            "content": f"... (截断，原 {len(serialized)} 字节)",
        })


    # ── Op 8: ContinuableSnapshot ──────────────────────────────

    @staticmethod
    def _is_provider_valid(messages: list[dict[str, Any]]) -> bool:
        """Check if message list is valid for LLM provider API replay.

        Provider-valid means: every assistant message with tool_calls
        has matching tool-result messages for ALL tool_call_ids before
        the next assistant message appears.
        """
        pending_tool_call_ids: set[str] = set()
        for msg in messages:
            role = msg.get("role", "")
            if role == "assistant":
                # If there are pending tool results from a previous assistant,
                # they must all be resolved before this new assistant message
                if pending_tool_call_ids:
                    return False
                tool_calls = msg.get("tool_calls") or []
                if tool_calls:
                    pending_tool_call_ids = {
                        tc.get("id", "") for tc in tool_calls if tc.get("id")
                    }
            elif role == "tool":
                tool_call_id = msg.get("tool_call_id", "")
                pending_tool_call_ids.discard(tool_call_id)
        # All tool_calls must have been resolved
        return len(pending_tool_call_ids) == 0

    def take_snapshot(
        self,
        messages: list[dict[str, Any]],
        checkpoint_reason: str = "tool_pair_complete",
    ) -> ContinuableSnapshot | None:
        """Take a provider-valid snapshot of the current message history.

        Only saves if the messages are provider-valid (all tool-call/return
        pairs complete). Returns the snapshot, or None if invalid.

        Call this AFTER tool results have been appended to messages, or
        after a final answer (no tool_calls in the response).
        """
        import copy
        if not self._is_provider_valid(messages):
            return None
        step_num = len(self._execution.steps)
        token_est = sum(
            len(str(m.get("content", ""))) for m in messages
        ) // 4
        snapshot = ContinuableSnapshot(
            messages=copy.deepcopy(messages),
            step_number=step_num,
            session_id=self._execution.session_id,
            token_estimate=token_est,
            checkpoint_reason=checkpoint_reason,
        )
        self._latest_snapshot = snapshot
        return snapshot

    @property
    def latest_snapshot(self) -> ContinuableSnapshot | None:
        """Return the most recent provider-valid snapshot, or None."""
        return self._latest_snapshot

    @classmethod
    def restore_from_snapshot(cls, snapshot: ContinuableSnapshot) -> "TrajectoryRecorder":
        """Reconstruct a TrajectoryRecorder from a snapshot.

        Creates a new recorder with the snapshot's messages as the starting
        point. The engine can then resume the ReAct loop from these messages.
        Note: only message history is restored; capability state (tools,
        session vars) must be rebuilt by the caller.
        """
        recorder = cls(
            task=f"[restored from snapshot @ step {snapshot.step_number}]",
            session_id=snapshot.session_id,
        )
        recorder._latest_snapshot = snapshot
        recorder.start()
        return recorder

    def to_json(self) -> str:
        """导出为 JSON 字符串。"""
        import json
        return json.dumps(
            self._to_dict(),
            ensure_ascii=False,
            default=str,
            indent=2,
        )

    def _to_dict(self) -> dict[str, Any]:
        return {
            "task": self._execution.task,
            "session_id": self._execution.session_id,
            "success": self._execution.success,
            "total_tokens": self._execution.total_tokens,
            "execution_time": self._execution.execution_time,
            "started_at": self._execution.started_at,
            "final_result": self._execution.final_result,
            "steps": [
                {
                    "step_number": s.step_number,
                    "state": s.state,
                    "tool_calls": s.tool_calls,
                    "tool_results": s.tool_results,
                    "reflection": s.reflection,
                    "error": s.error,
                    "llm_usage": s.llm_usage,
                    "timestamp": s.timestamp,
                }
                for s in self._execution.steps
            ],
            "latest_snapshot": self._latest_snapshot.to_dict() if self._latest_snapshot else None,
        }


class TrajectoryStore:
    """进程内轨迹存储 — 按 session_id 索引。

    与 ApprovalStore 同模式：单进程 dict + 轻量存储。
    """

    def __init__(self) -> None:
        self._store: dict[str, TrajectoryRecorder] = {}

    def put(self, session_id: str, recorder: TrajectoryRecorder) -> None:
        self._store[session_id] = recorder

    def get(self, session_id: str) -> TrajectoryRecorder | None:
        return self._store.get(session_id)

    def remove(self, session_id: str) -> None:
        self._store.pop(session_id, None)

    def get_snapshot(self, session_id: str) -> ContinuableSnapshot | None:
        """Get the latest ContinuableSnapshot for a session."""
        recorder = self._store.get(session_id)
        if recorder is None:
            return None
        return recorder.latest_snapshot

    def restore(self, session_id: str) -> TrajectoryRecorder | None:
        """Restore a TrajectoryRecorder from its latest snapshot.

        Returns None if no snapshot exists. The caller is responsible for
        rebuilding capability state (tools, session vars) that the snapshot
        does not capture.
        """
        recorder = self._store.get(session_id)
        if recorder is None or recorder.latest_snapshot is None:
            return None
        return TrajectoryRecorder.restore_from_snapshot(recorder.latest_snapshot)