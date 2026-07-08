"""WorkflowSpec DTOs — Brain output consumed by execution layers.

These DTOs are deliberately protocol-neutral. Brain decides the workflow
shape and requested capabilities; Temporal/Activity/ToolPool decide how to
execute them reliably.

⚠️ Single Source of Truth (P0-1 fix):
    `NodeType` / `OnFailure` / `CallerType` 是节点类型与失败策略的**唯一权威定义**。
    controlplane/domain/workflow_spec.py 是本文件的 dataclass 镜像副本（跨独立 pyproject
    解耦），必须与本文件保持字段与 enum 值**逐字一致**。修改本文件时必须同步修改镜像。
    所有 JSON Schema (design_workflow / launch_workflow 的 parameters_schema) 的 enum
    列表必须从这些 Literal 派生（见 `on_failure_enum_values()` 等辅助函数），禁止手写。
"""

from __future__ import annotations

from typing import Any, Literal, get_args
from uuid import uuid4

from pydantic import BaseModel, Field


NodeType = Literal["brain_task", "tool_task", "human_task", "wait_task"]
OnFailure = Literal["abort", "continue", "escalate", "retry"]
CallerType = Literal["brain_direct", "activity", "system"]

# ── enum 派生辅助:所有 schema 必须用这些函数生成 enum 列表,禁止手写 ──
# 这样加/删 enum 值时只需改上面的 Literal,所有 schema 自动同步


def node_type_enum_values() -> list[str]:
    """NodeType 的 enum 值列表(schema 派生用)。"""
    return list(get_args(NodeType))


def on_failure_enum_values() -> list[str]:
    """OnFailure 的 enum 值列表(schema 派生用)。

    语义对齐 dag_runner_workflow.py 的实际处理:
      - abort:    立即终止整个 workflow
      - continue: 节点失败但下游继续(失败节点计入 failed_nodes)
      - escalate: 同 continue + 标记需人工介入(Phase 4+ 接 HumanGate)
      - retry:    节点级重试(Phase 4+ 由 Activity RetryPolicy 实现)
    """
    return list(get_args(OnFailure))


def caller_type_enum_values() -> list[str]:
    """CallerType 的 enum 值列表(schema 派生用)。"""
    return list(get_args(CallerType))


# OnFailure 默认值常量 — DTO 默认值、schema 默认值、L2 workflow 默认值都用此
ON_FAILURE_DEFAULT: OnFailure = "escalate"


class ToolIntent(BaseModel):
    """A requested capability, not a concrete tool/protocol binding."""

    capability: str = Field(min_length=1)
    input: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class CallerContext(BaseModel):
    """Who is invoking a capability and why."""

    caller_type: CallerType = "brain_direct"
    case_id: str | None = None
    node_id: str | None = None
    session_id: str | None = None


class WorkflowNode(BaseModel):
    """One node in a Brain-designed workflow graph."""

    node_id: str = Field(min_length=1)
    type: NodeType
    capability: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    input: dict[str, Any] = Field(default_factory=dict)
    condition: str | None = None
    on_failure: OnFailure = ON_FAILURE_DEFAULT
    caller_context: CallerContext = Field(default_factory=CallerContext)


class WorkflowSpec(BaseModel):
    """Workflow DAG draft produced by Brain."""

    workflow_id: str = Field(default_factory=lambda: f"wf-{uuid4().hex[:8]}")
    objective: str = Field(min_length=1)
    requirements: list[str] = Field(default_factory=list)
    nodes: list[WorkflowNode] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

