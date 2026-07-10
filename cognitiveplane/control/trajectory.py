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