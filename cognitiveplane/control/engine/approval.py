"""Approval gate — human-in-the-loop confirmation for destructive tools.

Extracted from react.py. Contains:
  - APPROVAL_REQUIRED_TOOLS / APPROVAL_TIMEOUT_SECONDS constants
  - ApprovalRequest dataclass + ApprovalStore class
  - summarize_for_approval (was ReActEngine._summarize_for_approval)
"""

import asyncio
import json
import time as _time
from dataclasses import dataclass, field
from typing import Any


# 架构级 approval gate：执行前必须暂停等待用户确认的工具。
# 不依赖 system prompt 约束 LLM "等用户确认"，而是架构在工具执行前
# 拦截这些工具，emit approval_request 事件并阻塞，直到前端 /chat/approve
# 端点 resolve。这是 Claude Code 风格的 human-in-the-loop 架构保证。
#
# 标注写入类工具（2026-07-03 暴露给 L1 后加入）— 让用户逐步确认
# 标注流程的每个关键决策点（创建作业/任务、上传图片、触发 AI、分配标注员），
# 而不是黑盒自动执行。查询类工具（list_datasets/get_dataset/list_jobs/
# get_job/list_tasks）不在此集合 — LLM 可直接调用获取上下文。
APPROVAL_REQUIRED_TOOLS = frozenset({
    # ── 标注写入类 ──
    "create_job",     # 创建标注作业（含数据集/作业名/标签集等关键决策）
    "create_task",    # 创建标注任务（含图片选择/标注员等关键决策）
    "trigger_ai",     # 触发 AI 自动标注（不可逆操作）
    "upload_images",  # 上传图片到数据集
    "assign_task",    # 分配标注任务给标注员
})
APPROVAL_TIMEOUT_SECONDS = 300.0  # 用户 5 分钟不响应则超时取消


@dataclass
class ApprovalRequest:
    """待审批的工具执行请求 — ReAct loop 与 /chat/approve 端点的共享桥梁。

    ReAct loop create() 后 await event.wait() 阻塞；前端用户通过 /chat/approve
    端点 resolve() 触发 event.set()，loop 恢复执行。
    """
    approval_id: str
    session_id: str
    iteration: int
    tool_name: str
    arguments: dict[str, Any]
    summary: str  # 供前端展示的工具调用摘要（人类可读）
    created_at: float = field(default_factory=lambda: _time.time())
    event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: str | None = None  # "approved" | "rejected" | "timeout"
    feedback: str | None = None


class ApprovalStore:
    """进程级 approval 状态存储。

    ReAct loop 在执行需要审批的工具前调 create() 创建 ApprovalRequest 并
    await req.event.wait()。前端 /chat/approve 端点调 resolve() 触发 event.set()。

    设计：进程内 dict + asyncio.Event，单进程部署足够。多进程时换 Redis。
    """

    def __init__(self) -> None:
        self._pending: dict[str, ApprovalRequest] = {}

    def create(
        self,
        approval_id: str,
        session_id: str,
        iteration: int,
        tool_name: str,
        arguments: dict[str, Any],
        summary: str,
    ) -> ApprovalRequest:
        req = ApprovalRequest(
            approval_id=approval_id,
            session_id=session_id,
            iteration=iteration,
            tool_name=tool_name,
            arguments=arguments,
            summary=summary,
        )
        self._pending[approval_id] = req
        return req

    def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._pending.get(approval_id)

    def resolve(self, approval_id: str, decision: str, feedback: str | None = None) -> bool:
        req = self._pending.get(approval_id)
        if req is None or req.decision is not None:
            return False  # 不存在或已决
        req.decision = decision
        req.feedback = feedback
        req.event.set()
        return True

    def remove(self, approval_id: str) -> None:
        self._pending.pop(approval_id, None)


def summarize_for_approval(tool_name: str, arguments: dict[str, Any]) -> str:
    """构造待审批工具的人类可读摘要，供前端 approval_request 事件展示。

    Was ReActEngine._summarize_for_approval (staticmethod).

    - launch_workflow: 展示工作流节点设计
    - 标注写入类工具: 展示关键决策点（作业名/标签/标注员等）
    - 其他: 通用截断 JSON
    """
    # ── 标注写入类工具专用摘要 ──
    if tool_name == "create_job":
        name = arguments.get("name") or "（未指定）"
        dataset_id = arguments.get("dataset_id") or "（未指定）"
        labels = arguments.get("labels") or []
        labels_str = "、".join(labels) if labels else "（未指定）"
        version_id = arguments.get("version_id", "（未指定）")
        return (
            f"**创建标注作业**\n"
            f"- 作业名: {name}\n"
            f"- 数据集 ID: {dataset_id}\n"
            f"- 标注类别: {labels_str}\n"
            f"- 版本 ID: {version_id}"
        )
    if tool_name == "create_task":
        job_id = arguments.get("job_id") or "（未指定）"
        annotator = arguments.get("annotator") or "（未指定）"
        # image_paths 或 image_b64
        img_count = 0
        if "image_paths" in arguments:
            img_count = len(arguments["image_paths"]) if isinstance(arguments["image_paths"], list) else 1
        elif "image_b64" in arguments:
            img_count = 1
        elif "images" in arguments:
            img_count = len(arguments["images"]) if isinstance(arguments["images"], list) else 1
        return (
            f"**创建标注任务**\n"
            f"- 作业 ID: {job_id}\n"
            f"- 标注图片数: {img_count}\n"
            f"- 标注员: {annotator}"
        )
    if tool_name == "upload_images":
        dataset_id = arguments.get("dataset_id") or "（未指定）"
        img_count = 0
        if "images" in arguments:
            img_count = len(arguments["images"]) if isinstance(arguments["images"], list) else 1
        elif "image_paths" in arguments:
            img_count = len(arguments["image_paths"]) if isinstance(arguments["image_paths"], list) else 1
        elif "image_b64" in arguments:
            img_count = 1
        return (
            f"**上传图片到数据集**\n"
            f"- 数据集 ID: {dataset_id}\n"
            f"- 图片数: {img_count}"
        )
    if tool_name == "assign_task":
        task_id = arguments.get("task_id") or "（未指定）"
        annotator = arguments.get("annotator") or "（未指定）"
        return (
            f"**分配标注任务**\n"
            f"- 任务 ID: {task_id}\n"
            f"- 标注员: {annotator}"
        )
    if tool_name == "trigger_ai":
        job_id = arguments.get("job_id") or "（未指定）"
        task_id = arguments.get("task_id", "（未指定）")
        return (
            f"**触发 AI 预标注** ⚠️ 不可逆操作\n"
            f"- 作业 ID: {job_id}\n"
            f"- 任务 ID: {task_id}"
        )
    if tool_name == "web_search":
        query = arguments.get("query") or arguments.get("question") or "（未提供）"
        return (
            f"**联网检索申请**\n"
            f"- 查询内容: {query}\n"
            f"- 说明: 系统已优先使用内部知识 / RAG / 会话上下文，"
            f"现在需要外部最新信息，请确认是否允许联网搜索。"
        )

    # ── launch_workflow 摘要 ──
    if tool_name != "launch_workflow":
        # 通用摘要：截断的 JSON
        s = json.dumps(arguments, ensure_ascii=False, default=str)
        return s[:500] + ("..." if len(s) > 500 else "")
    parts: list[str] = []
    reason = arguments.get("reason") or arguments.get("objective") or ""
    if reason:
        parts.append(f"**理由**: {reason}")
    wf = arguments.get("workflow_design") or {}
    if isinstance(wf, dict):
        nodes = wf.get("nodes") or []
        if nodes:
            parts.append(f"**节点数**: {len(nodes)}")
            node_lines = []
            for idx, n in enumerate(nodes, 1):
                cap = n.get("capability") or n.get("activity") or "?"
                nid = n.get("node_id") or n.get("id") or f"node{idx}"
                inp = n.get("input_data") or {}
                action = inp.get("action", "")
                action_str = f" [{action}]" if action else ""
                node_lines.append(f"  {idx}. {nid} → {cap}{action_str}")
            parts.append("**节点设计**:\n" + "\n".join(node_lines))
        edges = wf.get("edges") or []
        if edges:
            parts.append(f"**依赖边**: {len(edges)} 条")
    wf_id = arguments.get("workflow_id")
    if wf_id:
        parts.append(f"**已有工作流 ID**: {wf_id}")
    return "\n".join(parts) if parts else "（无摘要信息）"
