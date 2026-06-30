"""WorkflowSpec DTO — L1→L2 跨平面契约。

boundary-pinning §6.2 要求：cognitiveplane 的 design_workflow 工具产出
WorkflowSpec，bridge 层翻译成 temporal_client.start_workflow(RunWorkflowSpec,
workflow_spec)。

本文件是 cognitiveplane/shared/dto_workflow.py 的镜像副本。两模块各自独立
pyproject，不跨模块 import，保持解耦。字段定义保持一致，Phase 4+ 再考虑
抽到 shared 包。

Source: boundary-pinning §5 + cognitiveplane/shared/dto_workflow.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4


NodeType = Literal["brain_task", "tool_task", "human_task", "wait_task"]
OnFailure = Literal["abort", "continue", "escalate", "retry"]
CallerType = Literal["brain_direct", "activity", "system"]


@dataclass(frozen=True)
class ToolIntent:
    """A requested capability, not a concrete tool/protocol binding."""
    capability: str
    input: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class CallerContext:
    """Who is invoking a capability and why."""
    caller_type: CallerType = "brain_direct"
    case_id: str | None = None
    node_id: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class WorkflowNode:
    """One node in a Brain-designed workflow graph."""
    node_id: str
    type: NodeType
    capability: str | None = None
    depends_on: list[str] = field(default_factory=list)
    input: dict[str, Any] = field(default_factory=dict)
    condition: str | None = None
    on_failure: OnFailure = "escalate"
    caller_context: CallerContext = field(default_factory=CallerContext)


@dataclass(frozen=True)
class WorkflowSpec:
    """Workflow DAG draft produced by Brain.

    序列化为 dict 跨 Temporal 边界传递。controlplane 的 RunWorkflowSpec
    workflow 接收此 dict，按 depends_on 拓扑排序推进 nodes。
    """
    workflow_id: str = field(default_factory=lambda: f"wf-{uuid4().hex[:8]}")
    objective: str = ""
    requirements: list[str] = field(default_factory=list)
    nodes: list[WorkflowNode] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ── dict ↔ dataclass 转换（跨 Temporal 边界用 JSON 安全类型）──


def workflow_spec_from_dict(data: dict[str, Any]) -> WorkflowSpec:
    """从 dict 反序列化 WorkflowSpec（Temporal workflow 入参）。"""
    nodes_data = data.get("nodes", [])
    nodes = [
        WorkflowNode(
            node_id=n.get("node_id"),
            type=n.get("type", "tool_task"),
            capability=n.get("capability"),
            depends_on=list(n.get("depends_on", [])),
            input=dict(n.get("input", {})),
            condition=n.get("condition"),
            on_failure=n.get("on_failure", "escalate"),
            caller_context=CallerContext(
                caller_type=n.get("caller_context", {}).get("caller_type", "brain_direct"),
                case_id=n.get("caller_context", {}).get("case_id"),
                node_id=n.get("caller_context", {}).get("node_id"),
                session_id=n.get("caller_context", {}).get("session_id"),
            ),
        )
        for n in nodes_data
    ]
    # P2-5 fix: workflow_id 必须由 cognitiveplane 侧提供。
    # 不用 "wf-unknown" 占位符 — 多个缺 id 的 spec 会冲突（Temporal 拒绝相同 workflow_id）。
    # 也不调 uuid4 — 会违反 Temporal sandbox 确定性。
    workflow_id = data.get("workflow_id")
    if not workflow_id or not workflow_id.strip():
        raise ValueError(
            "WorkflowSpec.workflow_id must be provided by cognitiveplane "
            "(cannot use placeholder or generate uuid4 in Temporal sandbox)"
        )

    # P2-4 fix: 补齐与 L1 Pydantic 等价的校验（L1 用 Field(min_length=1)）
    objective = data.get("objective", "")
    if not objective or not objective.strip():
        raise ValueError("WorkflowSpec.objective must be non-empty")
    for n in nodes:
        if not n.node_id or not n.node_id.strip():
            raise ValueError("WorkflowNode.node_id must be non-empty")

    return WorkflowSpec(
        workflow_id=workflow_id,
        objective=objective,
        requirements=list(data.get("requirements", [])),
        nodes=nodes,
        metadata=dict(data.get("metadata", {})),
    )


def workflow_spec_to_dict(spec: WorkflowSpec) -> dict[str, Any]:
    """序列化 WorkflowSpec 为 dict（跨 Temporal 边界传递）。"""
    return {
        "workflow_id": spec.workflow_id,
        "objective": spec.objective,
        "requirements": list(spec.requirements),
        "nodes": [
            {
                "node_id": n.node_id,
                "type": n.type,
                "capability": n.capability,
                "depends_on": list(n.depends_on),
                "input": dict(n.input),
                "condition": n.condition,
                "on_failure": n.on_failure,
                "caller_context": {
                    "caller_type": n.caller_context.caller_type,
                    "case_id": n.caller_context.case_id,
                    "node_id": n.caller_context.node_id,
                    "session_id": n.caller_context.session_id,
                },
            }
            for n in spec.nodes
        ],
        "metadata": dict(spec.metadata),
    }


def topological_sort(nodes: list[WorkflowNode]) -> list[WorkflowNode]:
    """按 depends_on 拓扑排序。同层无依赖的可并行（当前串行执行）。

    Raises ValueError if cycle detected, duplicate node_id, or depends_on references unknown node.
    """
    # P2-1 fix: 检测重复 node_id（dict 构造会静默覆盖后者，导致节点丢失）
    seen_ids: set[str] = set()
    for n in nodes:
        if n.node_id in seen_ids:
            raise ValueError(f"Duplicate node_id: '{n.node_id}'")
        seen_ids.add(n.node_id)
    node_map = {n.node_id: n for n in nodes}
    # 校验 depends_on 引用
    for n in nodes:
        for dep in n.depends_on:
            if dep not in node_map:
                raise ValueError(
                    f"Node '{n.node_id}' depends on unknown node '{dep}'"
                )

    visited: dict[str, int] = {}  # 0=visiting, 1=done
    result: list[WorkflowNode] = []

    def visit(node_id: str, path: list[str]) -> None:
        state = visited.get(node_id)
        if state == 1:
            return
        if state == 0:
            cycle = " -> ".join(path + [node_id])
            raise ValueError(f"Cycle detected: {cycle}")
        visited[node_id] = 0
        for dep in node_map[node_id].depends_on:
            visit(dep, path + [node_id])
        visited[node_id] = 1
        result.append(node_map[node_id])

    for n in nodes:
        visit(n.node_id, [])

    return result
