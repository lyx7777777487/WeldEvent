"""System prompt construction — worldview injection + context + tools.

Extracted from react.py. Contains:
  - Module-level helpers: _read_md_if_exists, _build_activity_catalog_section,
    _format_case_brief, _format_memory_content
  - record_worldview_injection (was ReActEngine._record_worldview_injection)
  - format_workflow_status_section (was ReActEngine._format_workflow_status_section)
  - WorldviewBuilder class (was _build_worldview_section + _fetch_memory_hits)
  - SystemPromptBuilder class (was ReActEngine._build_system_prompt)
"""

import json
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from cognitiveplane.control.skills import Skill
from cognitiveplane.control.registry.tool_registry import ToolRegistry
from cognitiveplane.shared.dto.context import ContextSnapshot

if TYPE_CHECKING:
    from cognitiveplane.control.deps import CognitiveDependencies
    from cognitiveplane.control.event_log import EventLog
    from cognitiveplane.interaction.workflow_events import WorkflowEventBus



# ── Op 10: AgentWorkPlan + cache boundary (Op 36) ──
# Source: Claude Code cache-tail reminder + Anthropic prompt caching API (2024)
# The plan is injected at the TAIL of the system prompt to keep the
# cache prefix stable. NOT in durable history - rebuilt each run() call.

CACHE_BOUNDARY_MARKER = "<!-- weldevent:cache_boundary -->"


@dataclass
class AgentWorkPlan:
    """Op 10: Runtime work plan injected at prompt tail (cache-tail reminder).

    Source: Claude Code cache-tail reminder + Anthropic prompt caching API.

    This is NOT durable history -- it is rebuilt from session state on each
    run() call and injected at the END of the system prompt. By placing
    dynamic content at the tail, the cache prefix (stable sections before
    the CACHE_BOUNDARY_MARKER) stays identical across calls, enabling
    Anthropic prompt caching cache hits (cached tokens billed at 10%).

    Fields are intentionally simple -- the LLM uses this as a reminder of
    what it's working on and what to do next, not as a rigid plan.
    """
    objective: str = ""
    current_step: str = ""
    next_actions: list[str] = field(default_factory=list)
    context_hint: str = ""

    def to_prompt_section(self) -> str:
        """Render as a prompt section for injection at the system prompt tail."""
        lines = ["## 当前工作计划 (AgentWorkPlan)\n"]
        if self.objective:
            lines.append(f"**目标**: {self.objective}")
        if self.current_step:
            lines.append(f"**当前步骤**: {self.current_step}")
        if self.next_actions:
            lines.append("\n**下一步**:")
            for action in self.next_actions[:5]:
                lines.append(f"- {action}")
        if self.context_hint:
            lines.append(f"\n**上下文提示**: {self.context_hint}")
        return "\n".join(lines)


def build_work_plan_from_session(session: dict[str, Any] | None) -> AgentWorkPlan:
    """Build an AgentWorkPlan from session state.

    Extracts the current objective, step, and next actions from session data.
    Returns an empty plan if no relevant session state exists.
    """
    if not session:
        return AgentWorkPlan()

    plan = AgentWorkPlan()

    # Objective from user input or session
    user_msg = session.get("last_user_input", "")
    if user_msg:
        plan.objective = str(user_msg)[:200]

    # Current step from notes
    notes = session.get("notes", [])
    if notes:
        plan.current_step = str(notes[-1])[:200]

    # Next actions from current_plan
    current_plan = session.get("current_plan")
    if current_plan and isinstance(current_plan, dict):
        wf_id = current_plan.get("workflow_id", "")
        if wf_id:
            plan.next_actions.append(f"launch_workflow(workflow_id={wf_id})")
        plan.context_hint = f"已有工作流方案: {current_plan.get('objective', '?')}"

    # Workflow status
    wf_status = session.get("workflow_status")
    if wf_status and isinstance(wf_status, dict):
        state = wf_status.get("state", "")
        if state:
            plan.context_hint += f" | 工作流状态: {state}"

    return plan


# Plan §5.1 line 894-902: Worldview files to auto-inject
WELDEVENT_MD_PATH = Path("WELDEVENT.md")
OPERATOR_MD_PATH = Path("OPERATOR.md")

logger = logging.getLogger("react")


# ---------------------------------------------------------------------------
# Module-level helpers for §5.1 worldview injection
# ---------------------------------------------------------------------------


def _read_md_if_exists(path: Path) -> str | None:
    """Return file content if exists, else None. Phase 2: files are optional."""
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except Exception:
        return None


def _build_methodology_section() -> str:
    """构建工作方法论 section — 引导 LLM 结构化思考。

    借鉴 trae-agent 7 步法 + Claude Code feature-dev 分阶段流程，
    精简为 6 步通用框架（不绑定具体 Activity），LLM 自行判断跳过哪些环节。
    """
    return (
        "## 工作方法论\n\n"
        "处理每个请求时，参考以下框架组织思考。这不是必须逐条执行的检查清单，"
        "而是帮你理清思路的引导——简单问题自然跳过某些环节。\n\n"
        "1. **Understand（理解意图）** — 先弄清用户意图再行动。区分问答/执行/诊断/反馈，"
        "不确定时用工具查或弹窗问，不要猜。\n"
        "2. **Explore（探索信息）** — 搜索相关数据、标准、历史案例。"
        "用 list_/get_/search_/read_ 工具获取信息，形成判断依据。\n"
        "3. **Decide（决策分叉）** — 信息充分时直接执行；存在歧义或多种路线时，"
        "给出建议+选项弹窗让用户拍板。\n"
        "4. **Execute（执行验证）** — 工具执行后立即检查结果是否完整有效"
        "（如 IQA 输出是否含所有指标、PPA 是否实际处理了图片）。\n"
        "5. **Review（审视产出）** — 从用户视角审视最终产出是否满足原始意图"
        "（如标注结果是否对齐用户需求、工作流是否覆盖了用户指定的所有步骤）。\n"
        "6. **Summarize（结构化汇报）** — 用简洁语言汇报关键结论，避免堆砌无关细节。"
        "用表格或列表呈现结构化结果，让用户一眼看懂。\n"
    )


def _build_activity_catalog_section() -> str:
    """Build L3 Activity Catalog section for system prompt.

    LLM 自主编排工作流的能力边界：
      - 用户用业务语言提需求（如"这张图有没有问题"）
      - LLM 参考 catalog 自动选择 activity，通过 design_workflow 的 nodes
        参数传入编排结果（capability + depends_on）
      - 已实现(✅)的 activity 真实执行；未实现(⚠️)的虚拟执行（占位返回）
    """
    try:
        from cognitiveplane.control.tools.activity_catalog import get_catalog_text
        catalog = get_catalog_text()
    except Exception:
        # Activity catalog 不可用时静默跳过（不应阻塞 ReAct 主循环）
        return ""

    return (
        "# L3 执行层能力边界 (Activity Catalog)\n\n"
        "用户是业务人员，不会明确指定 capability 名。当用户的请求需要编排工作流时，"
        "你应参考下表自动选择 activity，并通过 design_workflow 工具的 `nodes` 参数"
        "传入编排结果（每个 node 含 capability + depends_on）。"
        "已实现的 activity 会真实执行；未实现的会虚拟执行（返回占位结果，不报错），"
        "因此你可以放心选择，不必担心未实现的 capability 导致失败。\n\n"
        f"{catalog}\n"
        "编排要点：\n"
        "- 节点依赖关系用 depends_on 引用前置 node_id（如 PPA 依赖 IQA 报告）\n"
        "- image_refs 由工具自动注入到每个 tool_task 节点，无需在 input_data 重复\n"
        "- on_failure 默认 'retry'，关键节点可显式设为 'escalate'\n"
    )


def _build_governance_section() -> str:
    """Build governance intervention section - 教 LLM 把自然语言意图映射到 governance action.

    这是"用自然语言介入工作流"的最后一公里: 工具已接好(control_workflow 的 7 个
    governance action), 但 LLM 需要知道自己有这能力 + 意图->action 的映射规则,
    否则用户说"把这个批次扣住"时 LLM 不会想到调 control_workflow(action="batch_hold").

    设计原则:
      - 用业务语言描述 (不暴露 sig_type/batch_signals 等内部词)
      - 区分易混淆意图 (暂停 vs 扣留 vs 撤回)
      - 标明哪些需确认门 (agent 提议后架构会弹确认卡等用户拍板)
    """
    return (
        "# 工作流人工干预 (Governance)\n\n"
        "当工作流正在运行时，用户可能用自然语言要求干预执行过程。"
        "你应把用户的意图映射到 `control_workflow` 工具的对应 action。"
        "**工作流界面只展示，不操作——所有干预都通过你在对话中发起。**\n\n"
        "## 意图映射表\n\n"
        "| 用户说的话 | action | 关键参数 | 说明 |\n"
        "|:---|:---|:---|:---|\n"
        "| \"工作流到哪了\" / \"进度怎样\" | `query` | workflow_id | 查询当前进度 |\n"
        "| \"暂停一下\" / \"停一下\" | `pause` | workflow_id | 暂停整个工作流 |\n"
        "| \"继续\" / \"恢复\" | `resume` | workflow_id | 恢复暂停的工作流 |\n"
        "| \"取消工作流\" | `cancel` | workflow_id | 终止整个工作流 |\n"
        "| \"只暂停X工位/节点\" / \"X暂停其他继续\" | `pause_scope` | scope_type=station, scope_id=节点ID | 分级暂停，其余继续 |\n"
        "| \"这个批次扣住别动\" / \"可疑批次待查\" | `batch_hold` | batch_id | 批次冻结，隔离结果不阻塞其他批次 |\n"
        "| \"标签标错了重新标\" / \"标注错了但结果没问题\" | `relabel_request` | node_id, original_label, corrected_label | 只回标注环节，复用执行结果 |\n"
        "| \"让X来审\" / \"换个人审查\" | `delegate_review` | target, new_reviewer_id | 转移审查权 |\n"
        "| \"标准升级了\" / \"D1.1出新版了\" | `standard_update` | standard_id, old_version, new_version | 登记标准更新，标记历史判定待复查 |\n"
        "| \"这个判定撤回\" / \"刚批的作废重跑\" | `revoke_approval` | node_id, revoker_id, reason | **[需确认]** 撤回已批准，下游重跑 |\n"
        "| \"这块我拍板是NG\" / \"质检员说这条算不合格\" | `ground_truth_override` | node_id, inspector_id, forced_verdict | **[需确认]** 质检员强制覆盖机器裁决 |\n"
        "| \"这个案例库错了\" / \"历史案例有误\" | `case_library_correction` | case_id, error_type, correction | **[需确认]** 纠正案例库，波及未来所有引用 |\n\n"
        "## 参数取值指引\n\n"
        "governance action 的关键参数怎么取值:\n\n"
        "- **batch_id** (batch_hold): 焊检域里**批次 = case_id**。即 launch_workflow 时传入的 case_id，或用户消息上下文里的 case 标识。用户说\"这个批次\"时，指当前 case_id。\n"
        "- **node_id** (pause_scope scope_id / revoke_approval / relabel_request / ground_truth_override): 节点 ID。先调 control_workflow(action=\"query\", workflow_id=...) 拿到节点列表，再选用户要干预的节点。用户说\"这个节点\"时先 query 拿列表。\n"
        "- **scope_type + scope_id** (pause_scope): station 时 scope_id=node_id；batch 时 scope_id=batch_id(=case_id)；workflow 时 scope_id=workflow_id。\n"
        "- **workflow_id**: 当前对话中已启动的工作流 ID。先 query 确认，或从 launch_workflow 结果取。\n\n"
        "**重要 - 参数提取规则**:\n"
        "1. 如果用户要干预但没说具体 ID，先调 control_workflow(action=\"query\", workflow_id=...) 查出当前节点/批次信息，再决定 action 参数。不要因为缺少 ID 就放弃干预。\n"
        "2. 用户消息里**明确提到的编号要直接填进对应参数**，不要留空：\n"
        "   - 用户说\"批次 B-12\" / \"批次 weld-batch-008\" / \"case xxx\" -> 直接填入 batch_id=\"B-12\" (或对应值)\n"
        "   - 用户说\"节点 n-3\" / \"weld_iqa 工位\" -> 先 query 拿节点列表，匹配后填入 node_id/scope_id\n"
        "   - 用户说\"工作流 wf-xxx\" -> 直接填入 workflow_id\n"
        "3. 当前会话的 case_id 通常是 batch_id 的来源（焊检域 batch=case）。如果用户说\"这个批次\"而没给编号，用当前 case_id 作为 batch_id。\n"
        "4. **用户消息里出现的任何批次/节点名称都要填进参数，不要留空**：用户说\"批次 default\" -> batch_id=\"default\"；说\"批次 B-12\" -> batch_id=\"B-12\"；说\"weld_iqa 工位\" -> scope_id=\"weld_iqa\"。即使看起来像普通词(default/weld_iqa)，只要用户用它指代干预对象，就必须填进对应参数。\n\n"
        "## 易混淆意图区分\n\n"
        "- **暂停 vs 扣留**: 暂停(pause_scope)是停止执行；扣留(batch_hold)是隔离结果但继续跑其他批次。"
        "用户说\"扣住\"/\"扣留\"/\"待查\" -> batch_hold；说\"停\"/\"暂停\" -> pause_scope 或 pause。\n"
        "- **撤回 vs 重做**: 撤回(revoke_approval)针对已批准的节点(已提交)；重做(rework_node)针对未提交的节点。"
        "用户说\"刚批的作废\" -> revoke_approval；说\"这个重跑\" -> 用 rework_node。\n"
        "- **重新标注 vs 重做节点**: 重新标注(relabel_request)只回标注环节、保留执行结果；重做节点(rework_node)整个重跑。"
        "用户说\"标签错了但结果对\" -> relabel_request；说\"这个节点整个重来\" -> rework_node。\n\n"
        "## 确认门 [需确认] 的 action\n\n"
        "标 **[需确认]** 的三个 action (revoke_approval / ground_truth_override / case_library_correction) "
        "是不可逆或责任敏感的操作。你调这些 action 时，架构会自动弹出确认卡片让用户拍板——"
        "用户确认后才真正执行。你不需要自己问用户确认，架构会处理。"
        "但你要在发起前用一句话告诉用户你打算做什么、为什么。\n"
    )

def _format_case_brief(context: ContextSnapshot) -> str | None:
    """Format CASE_BRIEF from ContextSnapshot. Return None if context is empty."""
    parts: list[str] = []
    case_id = getattr(context.case_id, "value", context.case_id)
    if case_id and str(case_id) != "unknown":
        parts.append(f"- Case ID: {case_id}")
    event_type = getattr(context.event_type, "value", context.event_type)
    if event_type:
        parts.append(f"- Event Type: {event_type}")
    if context.workflow_state:
        parts.append(f"- Workflow State: {json.dumps(context.workflow_state, ensure_ascii=False)}")
    if context.case_data:
        parts.append(f"- Case Data: {json.dumps(context.case_data, ensure_ascii=False)}")
    if not parts:
        return None
    return "\n".join(parts)


def _format_memory_content(content: Any) -> str:
    """Flatten MemoryContent to a short preview string.

    MemoryContent schema: summary (str), details (dict), feature_vector (list[float]).
    We only surface summary + key details; feature_vector is irrelevant for LLM context.
    """
    if hasattr(content, "model_dump"):
        content = content.model_dump()
    if isinstance(content, dict):
        summary = content.get("summary")
        details = content.get("details") or {}
        parts: list[str] = []
        if summary:
            parts.append(str(summary)[:200])
        if details:
            # Show up to 3 key-value pairs from details
            items = list(details.items())[:3]
            detail_str = ", ".join(f"{k}={v}" for k, v in items)
            parts.append(f"[{detail_str}]")
        if parts:
            return " ".join(parts)
        # Fallback: JSON dump
        return json.dumps(content, ensure_ascii=False, default=str)[:200]
    return str(content)[:200]


def record_worldview_injection(event_log: "EventLog | None", meta: dict[str, Any]) -> None:
    """§0.1 规则 1: 架构替 LLM 做的看不见的事记进 EventLog.

    Was ReActEngine._record_worldview_injection. Now takes event_log as a
    parameter instead of reading self._event_log.

    复用 STATE_TRANSITION 枚举 + data.phase="worldview_injected" 标记,
    不新增事件类型 (避免扩散到 DecisionPipelineView.apply).
    """
    if event_log is None:
        return
    try:
        from cognitiveplane.control.event_log import BrainEventType
        event_log.emit(
            BrainEventType.STATE_TRANSITION,
            source="react_engine",
            data={"phase": "worldview_injected", **meta},
        )
    except Exception:
        # EventLog failure must never break ReAct
        logger.debug("EventLog STATE_TRANSITION emit failed", exc_info=True)


def format_workflow_status_section(
    wf_summaries: list[dict[str, Any]]
) -> str:
    """格式化 workflow 状态摘要为 system prompt section（boundary-pinning §3）。

    Was ReActEngine._format_workflow_status_section.

    输入 wf_summaries 来自 WorkflowEventBus.get_session_workflow_summary()，
    每项格式 {"workflow_id", "status", "nodes": {node_id: {...}}}.

    设计：只展示状态，不指挥 LLM 下一步——避免无限循环触发 ReAct。
    """
    lines = ["## 工作流执行状态（长路径 Temporal 观察者自动注入）\n"]
    status_emoji = {
        "COMPLETED": "✅",
        "RUNNING": "⏳",
        "PAUSED": "⏸",
        "FAILED": "❌",
    }
    for wf in wf_summaries:
        wf_id = wf.get("workflow_id", "?")
        status = wf.get("status", "UNKNOWN")
        emoji = status_emoji.get(status, "•")
        lines.append(f"- **{wf_id}** {emoji} {status}")
        nodes = wf.get("nodes") or {}
        for node_id, node_info in nodes.items():
            node_status = node_info.get("status", "unknown")
            node_emoji = {
                "ok": "✅",
                "marginal": "⚠️",
                "ng": "❌",
                "error": "❌",
                "running": "⏳",
            }.get(node_status, "•")
            data = node_info.get("data") or {}
            # 节点结果摘要：取 data 中常见的 summary/short_result/result 字段
            summary = (
                data.get("summary")
                or data.get("short_result")
                or data.get("result")
            )
            if summary:
                # 截断过长摘要（防止 token 爆炸）
                summary_str = str(summary)
                if len(summary_str) > 200:
                    summary_str = summary_str[:200] + "..."
                lines.append(f"  - {node_id}: {node_emoji} {node_status} — {summary_str}")
            else:
                lines.append(f"  - {node_id}: {node_emoji} {node_status}")
        if status == "COMPLETED":
            lines.append(f"  → 工作流已完成，可在用户提问时主动汇报结果摘要")
        elif status == "PAUSED":
            lines.append(f"  → 工作流已暂停等待人工决策，请询问用户是否继续")
        elif status == "FAILED":
            lines.append(f"  → 工作流执行失败，请询问用户是否需要重试或排查")
    lines.append(
        "\n（这是 WorkflowObserver 推送的状态，你不需要主动 query。"
        "只在用户提问或主动汇报时引用上述结果。）"
    )
    return "\n".join(lines)


class WorldviewBuilder:
    """Plan §5.1 line 894-902: build worldview section from 4 sources.

    Was ReActEngine._build_worldview_section + _fetch_memory_hits.
    """

    def __init__(self, deps: "CognitiveDependencies") -> None:
        self._deps = deps

    async def build_section(
        self, context: ContextSnapshot
    ) -> tuple[str | None, dict[str, Any]]:
        """Returns (section_text, injection_metadata). section_text is None if all
        4 sources are empty/missing — caller skips the section. injection_metadata
        always populated for EventLog recording (§0.1 规则 1).
        """
        blocks: list[str] = []
        injected_sections: list[str] = []
        memory_hit_ids: list[str] = []
        discover_meta_extra: dict[str, Any] = {}

        # 1. WELDEVENT.md — 项目级规约（优先用路径发现，回退到 cwd 直接读）
        weldevent_md: str | None = None
        try:
            from cognitiveplane.interaction.project_doc import discover_project_docs
            discovered, discover_meta = discover_project_docs()
            if discovered:
                weldevent_md = discovered
                injected_sections.append("project_docs_discovered")
                discover_meta_extra = {"discover_sources": discover_meta.get("sources", []),
                              "discover_bytes": discover_meta.get("total_bytes", 0)}
            else:
                weldevent_md = _read_md_if_exists(WELDEVENT_MD_PATH)
                if weldevent_md:
                    injected_sections.append("weldevent_md")
        except Exception:
            # discover_project_docs 失败时回退到静态读取
            weldevent_md = _read_md_if_exists(WELDEVENT_MD_PATH)
            if weldevent_md:
                injected_sections.append("weldevent_md")
        if weldevent_md:
            blocks.append("## 项目规约 (WELDEVENT.md)\n\n" + weldevent_md)

        # 2. OPERATOR.md — 操作员偏好
        operator_md = _read_md_if_exists(OPERATOR_MD_PATH)
        if operator_md:
            blocks.append("## 操作员偏好 (OPERATOR.md)\n\n" + operator_md)
            injected_sections.append("operator_md")

        # 3. CASE_BRIEF — 当前 case 简报 (从 context 构造)
        case_brief = _format_case_brief(context)
        if case_brief:
            blocks.append("## 当前 Case 简报\n\n" + case_brief)
            injected_sections.append("case_brief")

        # 4. Memory.search — 相关历史修正/类似案例
        memory_hits, memory_hit_ids = await self.fetch_memory_hits(context)
        if memory_hits:
            blocks.append("## 相关历史 (Memory.search)\n\n" + memory_hits)
            injected_sections.append("memory_search")

        meta: dict[str, Any] = {
            "sections": injected_sections,
            "memory_hit_ids": memory_hit_ids,
            "memory_hit_count": len(memory_hit_ids),
            **discover_meta_extra,
        }

        if not blocks:
            return None, meta
        return "# 世界观 (上下文，不是步骤)\n\n" + "\n\n".join(blocks), meta

    async def fetch_memory_hits(
        self, context: ContextSnapshot
    ) -> tuple[str | None, list[str]]:
        """Plan §5.1 line 901: Memory.search 相关历史修正/类似案例.

        Returns (formatted_text, memory_hit_ids). Both are None/empty if
        MemoryDeps.search is None, raises, or returns empty.
        """
        memory_search = self._deps.memory.search
        if memory_search is None:
            return None, []
        try:
            from cognitiveplane.shared.ports.memory import (
                MemorySearchInput, MemorySearchQuery,
            )
            query = MemorySearchInput(
                query=MemorySearchQuery(
                    case_features={
                        "case_id": context.case_id.value,
                        "event_type": context.event_type.value,
                    },
                    feature_vector=[0.0],  # Phase 2: in-memory stub ignores vector
                    max_results=3,
                    min_confidence=0.5,
                )
            )
            output = await memory_search.search(query)
        except Exception:
            # Memory failure must never break ReAct (plan §5.1: worldview is optional)
            return None, []

        if not output.results:
            return None, []

        lines: list[str] = []
        hit_ids: list[str] = []
        for i, hit in enumerate(output.results, 1):
            content_preview = _format_memory_content(hit.content)
            lines.append(
                f"{i}. [相似度 {hit.similarity_score:.2f} | 状态 {hit.promotion_status.value}] "
                f"{content_preview}"
            )
            hit_id = str(getattr(hit.memory_id, "value", hit.memory_id))
            hit_ids.append(hit_id)
        return "\n".join(lines), hit_ids


class SystemPromptBuilder:
    """Build system prompt with worldview injection + context + tools.

    Was ReActEngine._build_system_prompt. Dependencies (tools, workflow_event_bus,
    event_log, deps) are injected at construction time.
    """

    def __init__(
        self,
        tools: ToolRegistry,
        deps: "CognitiveDependencies",
        workflow_event_bus: "WorkflowEventBus | None" = None,
        event_log: "EventLog | None" = None,
    ) -> None:
        self._tools = tools
        self._deps = deps
        self._workflow_event_bus = workflow_event_bus
        self._event_log = event_log
        self._worldview_builder = WorldviewBuilder(deps)

    async def build(
        self,
        context: ContextSnapshot,
        session: dict[str, Any],
        skill: Skill | None = None,
        work_plan: AgentWorkPlan | None = None,
    ) -> list[dict[str, str]]:
        """Build system prompt — 精简版，对齐 Claude Code 风格。

        核心理念：Base prompt 只定义角色+核心原则，场景规则全部放在
        Skill MD 文件和工具 description 中，按需注入不塞满上下文。
        """
        # Modernized: main agent sees all phase-visible tools regardless of skill.
        # skill only injects specialized prompt; it does NOT restrict tool access.
        # (Aligned with Claude Code / Codex progressive disclosure pattern)
        tools_desc = "\n".join(
            f"- {name}: {self._tools.get_tool(name).description}"
            for name in self._tools.list_llm_tools()
            if self._tools.get_tool(name) is not None
        )

        sections: list[str] = []

        # ── 1. 场景专业化指令（SubAgent override 或 Skill MD 注入）──
        # SubAgent 运行时 skill=None，专业化 prompt 从 session["subagent"] 读取；
        # 主 Agent 运行时无 subagent 配置，从 skill.system_prompt 读取。
        # 注入时带 token 预算控制：默认上限 6000 chars，防止大 skill 挤压对话上下文。
        SKILL_PROMPT_MAX_CHARS = 6000
        subagent_cfg = session.get("subagent") if session else None
        subagent_override = (
            subagent_cfg.get("system_prompt_override")
            if isinstance(subagent_cfg, dict)
            else None
        )
        if subagent_override:
            if len(subagent_override) > SKILL_PROMPT_MAX_CHARS:
                subagent_override = subagent_override[:SKILL_PROMPT_MAX_CHARS] + "\n\n... (截断: 专业化指令过长)"
            sections.append(subagent_override)
        elif skill is not None and skill.system_prompt:
            skill_prompt = skill.system_prompt
            if len(skill_prompt) > SKILL_PROMPT_MAX_CHARS:
                skill_prompt = skill_prompt[:SKILL_PROMPT_MAX_CHARS] + "\n\n... (截断: skill prompt 过长)"
            sections.append(skill_prompt)

        # ── 2. Worldview（项目规约/操作员偏好/当前Case/历史记忆）──
        worldview, injected_meta = await self._worldview_builder.build_section(context)
        if worldview:
            sections.append(worldview)
            record_worldview_injection(self._event_log, injected_meta)

        # ── 3. L3 Activity Catalog（编排能力边界）── [stable: before cache boundary]
        sections.append(_build_activity_catalog_section())

        # ── 3.5 Governance 干预能力（意图映射）── [stable: before cache boundary]
        # 教 LLM 把自然语言意图映射到 control_workflow 的 governance action.
        sections.append(_build_governance_section())

        # ── Op 10/36: Cache boundary marker ──
        # Sections above this marker are stable across calls (cache prefix).
        # Sections below are dynamic (rebuilt each run() call = cache tail).
        # Provider can use this marker to set cache_control on the prefix.
        sections.append(CACHE_BOUNDARY_MARKER)

        # ── 2.5 项目诊断状态注入（对齐 §17.12 回答改变决策）── [dynamic: after boundary]
        project_state = self._build_project_state_section(session)
        if project_state:
            sections.append(project_state)

        # ── 4. Plan 持久化（已设计方案，防止重复设计）──
        current_plan = session.get("current_plan") if session else None
        if current_plan:
            plan_lines = ["## 当前已设计的工作流方案（不要重复 design_workflow）\n"]
            plan_lines.append(f"**目标**: {current_plan.get('objective', '?')}")
            wf_id = current_plan.get("workflow_id")
            if wf_id:
                plan_lines.append(f"**workflow_id**: `{wf_id}`（launch_workflow 时传入此 ID）")
            nodes = current_plan.get("nodes", [])
            if nodes:
                plan_lines.append("\n**节点设计**:")
                plan_lines.append("| # | node_id | capability | action | depends_on |")
                plan_lines.append("|:-:|:--------|:-----------|:-------|:-----------|")
                for idx, n in enumerate(nodes, 1):
                    deps = ", ".join(n.get("depends_on", [])) or "—"
                    action = n.get("action") or "—"
                    plan_lines.append(
                        f"| {idx} | {n.get('node_id','?')} | {n.get('capability','?')} | {action} | {deps} |"
                    )
            plan_lines.append(
                "\n**下一步**: 直接调 `launch_workflow`（workflow_id=" + str(wf_id or "?") + "）启动此方案。"
                "架构会自动拦截并要求用户确认。**不要重复 design_workflow。**"
            )
            sections.append("\n".join(plan_lines))

        # ── 5. Session Notes（已完成步骤备忘，避免重复）──
        notes = session.get("notes", []) if session else []
        if notes:
            notes_lines = ["## 已完成步骤备忘\n"]
            for n in notes:
                notes_lines.append(f"- {n}")
            sections.append("\n".join(notes_lines))

        # ── 6. Workflow 状态注入（长路径 Temporal 观察者）──
        if self._workflow_event_bus is not None and session:
            wf_summaries = self._workflow_event_bus.get_session_workflow_summary(
                session.get("session_id", "")
            )
            if wf_summaries:
                sections.append(format_workflow_status_section(wf_summaries))

        # ── 7. 工具列表（每个工具的 description 已包含 WHEN/WHY/HOW）──
        sections.append(
            "## 可用工具\n\n"
            "每个工具的 description 会告诉你何时使用、如何使用。仔细阅读后再选择工具。\n\n"
            f"{tools_desc}"
        )

        return [
            {
                "role": "system",
                "content": (
                    "你是 WeldEvent 工业智能 Agent，运行在 ReAct（推理-行动）循环中。\n"
                    "你的职责是理解工业场景下的用户需求，利用可用工具获取信息、执行操作、编排工作流。\n\n"
                    "## 意图理解 — 每轮对话的第一步\n\n"
                    "收到用户消息后，不要急于调用工具。先在思考中完成意图分类：\n\n"
                    "| 意图类型 | 特征 | 你的动作 |\n"
                    "|:---------|:-----|:--------|\n"
                    "| **问答类** | \"这张图有没有问题\"\"当前有哪些数据集\"\"标准怎么写\" | 直接调工具获取信息并回答 |\n"
                    "| **执行类** | \"帮我标注\"\"启动质检\"\"跑一遍全流程\" | 先确认前提条件，再进入执行 |\n"
                    "| **诊断类** | \"能不能做自动质检\"\"这个项目怎么搞\"\"帮我看看\" | 进入项目诊断流程，逐轮追问，不直接给答案 |\n"
                    "| **反馈类** | 用户对上一步结果的修正、补充、否定 | 更新已确认事实，调整路线，不要忽略 |\n\n"
                    "**关键区分**：诊断类 ≠ 执行类。用户说\"能不能做\"不是在要求你\"现在就做\"。\n\n"
                    "## 何时询问用户\n\n"
                    "**当专业 Skill 处于激活状态时，Skill 的交互规则优先于本节的默认规则。**\n"
                    "如果 Skill 要求\"每步都询问用户\"，则必须遵循，即使你认为可以合理假设。\n\n"
                    "**无 Skill 或通用模式时**，优先用工具查询获取信息，查不到的再问用户。\n"
                    "以下情况应主动弹窗询问：\n"
                    "1. 关键业务参数缺失且无法合理推断（如作业名、标签集、标注员、标准来源）\n"
                    "2. 存在多条路线且各有重大取舍（如只做IQA vs 全流程）\n"
                    "3. 用户的回答暴露了阻断性问题（如标准在老师傅脑子里）\n"
                    "4. 设计方案时，每个关键决策点都应停下来让用户确认\n\n"
                    "**不需要问的情况**：\n"
                    "- 能用工具查到的信息直接查，不要问用户\n"
                    "- 架构 approval gate 拦截的工具直接调，架构自动弹窗确认，不要在调之前再调 request_confirmation\n"
                    "- 同一会话已问过的问题不重复问\n\n"
                    "每次只问最关键的一个问题。问完 -> 收到答案 -> 给一句话反馈 -> 推进。\n\n"
                    "## 渐进式方案设计 — 禁止一次性输出完整方案\n\n"
                    "当用户要求设计质检方案、标注方案、工作流时，**绝对不要一次性输出完整方案**。\n"
                    "信息缺失时，逐轮收集、逐轮确认，每一步都停下来让用户确认关键决策。\n\n"
                    "**禁止的行为**：\n"
                    "- ❌ 用户说\"帮我设计一个质检方案\"，你直接输出一个包含所有节点的工作流\n"
                    "- ❌ 用户说\"帮我标注\"，你直接说\"好的，我帮你创建作业、上传图片、分配任务\"而不调任何工具\n"
                    "- ❌ 在未确认标准/标签/数据集/标注员之前就 design_workflow 或 create_job\n\n"
                    "**正确的做法**：\n"
                    "- 先调工具查看现状（list_datasets / search_standards / analyze_image 等）\n"
                    "- 展示结果后，用 request_confirmation 问用户下一步决策\n"
                    "- 收到答案后再推进，每轮只推进一个步骤\n"
                    "- 原则：**先看、再问、再创建。不要猜、不要跳、不要一把梭。**\n\n"
                    "## 何时委派 subagent\n\n"
                    "delegate 启动完整子 agent（独立 LLM ReAct 循环），成本高延迟大。\n"
                    "**能用直接工具调用完成的任务，禁止 delegate。**\n\n"
                    "| 场景 | 正确做法 | 错误做法 |\n"
                    "|:-----|:---------|:---------|\n"
                    "| 查数据集列表 | 直接调 list_datasets | delegate weld-explorer |\n"
                    "| 查作业状态 | 直接调 get_job / list_tasks | delegate weld-explorer |\n"
                    "| 查标准条款 | 直接调 search_standards | delegate weld-explorer |\n"
                    "| 读工作流进度 | 直接调 read_weldmap / control_workflow | delegate weld-explorer |\n"
                    "| 同时查3+个不相关数据源 | delegate weld-explorer OK | 串行调5次工具 |\n"
                    "| 需多轮探索+分析的数据理解 | delegate data-understanding OK | 自己一步步查 |\n\n"
                    "**一轮 ReAct 内 delegate 不超过 1 次。** 多次 delegate = 在逃避直接调用工具。\n\n"
                    "## 知识获取 - retrieval budget\n\n"
                    "不要每次都查知识库。先判断你是否已经知道答案：\n"
                    "- 问题涉及行业常识/通用焊接知识 -> 直接回答，不查\n"
                    "- 问题涉及具体标准条款/历史案例/工艺参数 -> 查对应知识库工具\n"
                    "- 内部查不到且需要最新信息 -> web_search（一次够了就停）\n\n"
                    "工具选择指南：\n"
                    "- search_standards: 查基础焊接标准（NB/T47014等）\n"
                    "- search_cases: 查历史缺陷处理案例\n"
                    "- search_vision_knowledge: 查工业图像数据集元信息\n"
                    "- search_reasoning_knowledge: 查标准条款/推理模式/VQA模板/工艺文档\n"
                    "- web_search: 查国标全文/论文/开源数据集/缺陷分类/部署方案\n\n"
                    "retrieval budget 规则：一次检索已获得足够证据就立即回答，不要穷举式反复查。\n"
                    "不要用不同 query 反复查同一工具（架构会拦截第3次）。\n\n"
                                        "## 核心原则\n\n"
                    "1. **先理解再行动** — 不确定时用工具查或 delegate 子 agent，不要猜。\n"
                    "2. **短路径走问答，长路径走工作流** — \n"
                    "   问答类请求（看图/查询/了解情况）直接调工具回答；\n"
                    "   执行类请求（检测/标注/批量处理）先 design_workflow 再 launch_workflow。\n"
                    "3. **每步验证** — 工具返回后立即检查结果是否完整有效，\n"
                    "   失败时读完整错误信息，按错误类型修复，不要乱猜原因。\n"
                    "4. **结构化输出** — 用表格或列表呈现结果，用用户能理解的语言描述，\n"
                    "   不要直接 dump JSON。\n"
                    "5. **信息不足时回退** — 回到上一阶段补全信息，不要硬推。\n"
                    + "\n\n".join(sections)
                ),
            }
        ]

    @staticmethod
    def _build_project_state_section(session: dict[str, Any] | None) -> str | None:
        """从 session 中提取项目诊断状态，注入 system prompt。

        对齐报告 §17.12：用户回答改变 Agent 决策。Agent 需要知道：
        - 当前项目阶段（诊断/标准/数据/标注/模型/部署/监控）
        - 已确认的事实（不再重复问）
        - 还缺什么信息（优先追问）

        数据来源：session["case_data"] 中的 project_phase 和 confirmed_facts。
        """
        if not session:
            return None
        case_data = session.get("case_data") or {}
        if not isinstance(case_data, dict):
            case_data = {}

        phase = case_data.get("project_phase", "")
        confirmed = case_data.get("confirmed_facts", [])

        if not phase and not confirmed:
            return None

        lines = ["## 当前项目诊断状态\n"]
        if phase:
            phase_names = {
                "diagnosis": "项目启动诊断（阶段 A）",
                "standard": "标准数字化（阶段 B）",
                "data": "数据探索分析（阶段 C）",
                "external": "外部查证（阶段 D）",
                "annotation": "标注策略设计（阶段 E）",
                "model": "模型路线选型（阶段 F）",
                "validation": "验证测试（阶段 G）",
                "deploy": "部署上线（阶段 H）",
                "monitor": "上线监控（阶段 I）",
            }
            phase_label = phase_names.get(phase, phase)
            lines.append(f"**当前阶段**: {phase_label}")

            # 阶段提示：根据阶段给出下一步建议
            phase_hints = {
                "diagnosis": "优先追问业务标准、风险、数据来源。不要直接推荐模型。",
                "standard": "优先将标准数字化为结构化参数表。标记灰区和模糊描述。",
                "data": "优先调用 data-understanding subagent 做数据体检。脏数据不要直接进标注。",
                "annotation": "优先设计最小可行标注集。标注完成后调用 data-understanding 分析反馈。",
                "model": "根据标准类型、数据规模、缺陷率选择路线。不要只根据流行度选模型。",
                "validation": "验证集必须独立。按业务风险选择指标，不要只看总准确率。",
                "deploy": "默认灰度上线。采集系统是模型的一部分。",
                "monitor": "上线不是结束。建立误判回流和新缺陷处理机制。",
            }
            hint = phase_hints.get(phase)
            if hint:
                lines.append(f"**阶段提示**: {hint}")

        if confirmed:
            lines.append("\n**已确认的事实**（不要重复问）:")
            for i, fact in enumerate(confirmed, 1):
                fact_str = str(fact)[:200]
                lines.append(f"  {i}. {fact_str}")

        lines.append(
            "\n> 每次获取用户回答后，做四步判断：补齐前提？暴露阻断？改变路线？预防后续问题？"
            "\n> 信息不足时坚决回退，不要硬推。"
        )
        return "\n".join(lines)
