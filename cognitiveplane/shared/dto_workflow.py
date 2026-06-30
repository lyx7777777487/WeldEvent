"""WorkflowSpec DTOs — Brain output consumed by execution layers.

These DTOs are deliberately protocol-neutral. Brain decides the workflow
shape and requested capabilities; Temporal/Activity/ToolPool decide how to
execute them reliably.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


NodeType = Literal["brain_task", "tool_task", "human_task", "wait_task"]
OnFailure = Literal["abort", "continue", "escalate", "retry"]
CallerType = Literal["brain_direct", "activity", "system"]


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
    on_failure: OnFailure = "escalate"
    caller_context: CallerContext = Field(default_factory=CallerContext)


class WorkflowSpec(BaseModel):
    """Workflow DAG draft produced by Brain."""

    workflow_id: str = Field(default_factory=lambda: f"wf-{uuid4().hex[:8]}")
    objective: str = Field(min_length=1)
    requirements: list[str] = Field(default_factory=list)
    nodes: list[WorkflowNode] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

