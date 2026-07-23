"""Op 28-29: Execution records + streaming delivery.

Op 28: Streaming result delivery (heartbeat payload + SSE)
Op 29: WorkflowExecutionRecord - complete execution history for replay

Source: Temporal event history replay + Magentic-One replay debugging.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NodeExecutionRecord:
    """Op 29: Single node execution record."""
    node_id: str
    capability: str
    status: str           # OK | MARGINAL | NG | ERROR | SKIPPED
    duration_ms: int = 0
    attempt: int = 1
    quality_score: float = 0.0
    result_data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    timestamp: float = 0.0  # 由调用方传入(workflow 里用 workflow.now(), activity 里用 time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "capability": self.capability,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "attempt": self.attempt,
            "quality_score": self.quality_score,
            "error": self.error,
            "timestamp": self.timestamp,
        }


@dataclass
class StreamingUpdate:
    """Op 28: Streaming result update (heartbeat payload)."""
    node_id: str
    progress: float       # 0.0 - 1.0
    stage: str             # "dispatch_start" | "dispatch_complete" | "mock_fallback"
    partial_result: dict[str, Any] | None = None
    timestamp: float = 0.0  # 由调用方传入(workflow 里用 workflow.now(), activity 里用 time.time)

    def to_sse_payload(self) -> str:
        """Convert to SSE-compatible JSON string."""
        import json
        return json.dumps({
            "node_id": self.node_id,
            "progress": self.progress,
            "stage": self.stage,
            "partial_result": self.partial_result,
            "timestamp": self.timestamp,
        }, ensure_ascii=False, default=str)


@dataclass
class WorkflowExecutionRecord:
    """Op 29: Complete workflow execution record.

    Source: Temporal event history replay + Magentic-One replay debugging.

    Captures the full execution context for:
    - Offline replay and debugging
    - Pattern extraction (Op 30)
    - Reflexion note generation (Op 32)
    - Quality trend tracking (Op 8.3)
    """
    workflow_id: str
    objective: str
    spec_summary: dict[str, Any] = field(default_factory=dict)
    node_records: list[NodeExecutionRecord] = field(default_factory=list)
    status: str = "RUNNING"  # RUNNING | COMPLETED | FAILED
    started_at: float = 0.0  # 由调用方传入
    completed_at: float | None = None
    total_duration_ms: int = 0
    total_tokens: int = 0
    quality_score: float = 0.0
    reflexion_notes: list[str] = field(default_factory=list)
    streaming_updates: list[StreamingUpdate] = field(default_factory=list)

    def add_node_record(self, record: NodeExecutionRecord) -> None:
        self.node_records.append(record)

    def add_streaming_update(self, update: StreamingUpdate) -> None:
        self.streaming_updates.append(update)

    def add_reflexion_note(self, note: str) -> None:
        self.reflexion_notes.append(note)

    def finalize(self, status: str = "COMPLETED", now_ts: float | None = None) -> None:
        self.status = status
        # workflow sandbox 禁止 time.time(); 由 workflow 调用方传 workflow.now() 的 epoch.
        # now_ts=None 时回退 _time.time()(兼容 activity/非 workflow 调用).
        self.completed_at = now_ts if now_ts is not None else _time.time()
        self.total_duration_ms = int((self.completed_at - self.started_at) * 1000)

    @property
    def node_count(self) -> int:
        return len(self.node_records)

    @property
    def ok_count(self) -> int:
        return sum(1 for n in self.node_records if n.status == "OK")

    @property
    def failed_count(self) -> int:
        return sum(1 for n in self.node_records
                   if n.status in ("NG", "ERROR"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "objective": self.objective,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_duration_ms": self.total_duration_ms,
            "total_tokens": self.total_tokens,
            "quality_score": self.quality_score,
            "node_count": self.node_count,
            "ok_count": self.ok_count,
            "failed_count": self.failed_count,
            "node_records": [n.to_dict() for n in self.node_records],
            "reflexion_notes": self.reflexion_notes,
            "streaming_updates": len(self.streaming_updates),
        }


__all__ = [
    "NodeExecutionRecord", "StreamingUpdate",
    "WorkflowExecutionRecord",
]
