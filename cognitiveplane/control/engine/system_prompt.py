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
from typing import Any, TYPE_CHECKING

from cognitiveplane.control.skills import Skill
from cognitiveplane.control.registry.tool_registry import ToolRegistry
from cognitiveplane.shared.dto.context import ContextSnapshot

if TYPE_CHECKING:
    from cognitiveplane.control.deps import CognitiveDependencies
    from cognitiveplane.control.event_log import EventLog
    from cognitiveplane.interaction.workflow_events import WorkflowEventBus


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
    ) -> list[dict[str, str]]:
        """Build system prompt — 精简版，对齐 Claude Code 风格。

        核心理念：Base prompt 只定义角色+核心原则，场景规则全部放在
        Skill MD 文件和工具 description 中，按需注入不塞满上下文。
        """
        allowed_tools = skill.allowed_tools if skill else None
        tools_desc = "\n".join(
            f"- {name}: {self._tools.get_tool(name).description}"
            for name in self._tools.list_llm_tools(allowed_tools=allowed_tools)
            if self._tools.get_tool(name) is not None
        )

        sections: list[str] = []

        # ── 1. 场景专业化指令（SubAgent override 或 Skill MD 注入）──
        # SubAgent 运行时 skill=None，专业化 prompt 从 session["subagent"] 读取；
        # 主 Agent 运行时无 subagent 配置，从 skill.system_prompt 读取。
        subagent_cfg = session.get("subagent") if session else None
        subagent_override = (
            subagent_cfg.get("system_prompt_override")
            if isinstance(subagent_cfg, dict)
            else None
        )
        if subagent_override:
            sections.append(subagent_override)
        elif skill is not None and skill.system_prompt:
            sections.append(skill.system_prompt)

        # ── 2. Worldview（项目规约/操作员偏好/当前Case/历史记忆）──
        worldview, injected_meta = await self._worldview_builder.build_section(context)
        if worldview:
            sections.append(worldview)
            record_worldview_injection(self._event_log, injected_meta)

        # ── 2.5 项目诊断状态注入（对齐 §17.12 回答改变决策）──
        project_state = self._build_project_state_section(session)
        if project_state:
            sections.append(project_state)

        # ── 3. L3 Activity Catalog（编排能力边界）──
        sections.append(_build_activity_catalog_section())

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
                    "## 何时询问用户 — 决策分叉模型\n\n"
                    "你被赋予了\"不确定时主动询问\"的职责。这不是弱智的表现，而是工业场景的必要安全网。\n"
                    "在以下情况，**你必须停下来用 `request_confirmation` 弹窗问用户**，不要静默猜测：\n\n"
                    "1. **意图有歧义** — 用户的话有两种以上合理解释。呈现选项让用户选，不要替用户拍板。\n"
                    "2. **关键业务参数缺失** — 作业名、标签 schema、标注员、质检范围、验收阈值等\n"
                    "   业务参数必须由用户提供。不要硬编码默认值。\n"
                    "3. **遇到决策分叉点** — 存在多条可行路线且各有取舍时，给出你的建议 + 选项\n"
                    "   让用户决策。例如：\"只做 IQA 还是 IQA+PPA 全流程？\"\"用现有数据集还是新建？\"\n"
                    "4. **LLM 知识不足** — 先查内部知识库（search_standards/search_cases/\n"
                    "   search_vision_knowledge/search_reasoning_knowledge，1-2次），仍不足时调\n"
                    "   web_search 联网查证（1次），还不够再弹窗问用户。不要用不同 query 穷举式反复查同一工具。\n"
                    "5. **用户回答暴露新风险** — 用户说\"标准在老师傅脑子里\"\"数据没有批次信息\"\n"
                    "   等阻断性信息时，不要假装没问题继续推进，要停下来确认应对方案。\n\n"
                    "### 何时不需要询问\n\n"
                    "- 信息类查询（list_datasets/get_job 等）直接调，不要问\"我可以查吗\"\n"
                    "- 架构 approval gate 拦截的工具（launch_workflow/create_job/upload_images/\n"
                    "  create_task/trigger_ai/assign_task），直接调用即可，架构会自动弹窗让用户确认。\n"
                    "  **不要在调这些工具前再调 request_confirmation 问\"是否执行\"——会双重弹窗。**\n"
                    "- 同一会话已问过的问题不重复问（已确认事实记在 session notes / case_data 中）\n"
                    "- 能用工具自己查到的信息不问用户\n\n"
                    "### 询问时的姿态\n\n"
                    "- **每次只问最关键的一个问题** — 按优先级逐轮追问，不要一次性列出 10 个问题\n"
                    "- **给出你的建议** — 选项中第一个应是你的推荐方案，并说明推荐理由\n"
                    "- **允许用户输入自由意见** — 弹窗包含文本输入框，用户可输入选项之外的内容\n"
                    "- **声明你的假设** — 如果你决定基于某种假设行动，先说明假设，\n"
                    "  让用户有机会否决。例如：\"我先假设你只需要焊缝外观检测，如果还需要尺寸测量请告诉我。\"\n\n"
                    "### 询问节奏硬约束（防止体验轰炸）\n\n"
                    "**绝对禁止以下行为**：\n"
                    "1. 在一个 request_confirmation 里塞多个问题（如\"产品是什么？标准是什么？数据有多少？\"）——\n"
                    "   每次只问一个维度，用户回答后再决定下一个问题。\n"
                    "2. 选项中嵌套子问题（如\"焊接件 — 你做的是平板焊还是角焊？\"）——\n"
                    "   选项只能是简短的选择项（2-6字），不能是复合问句。\n"
                    "3. 同一轮 ReAct 里连续调 2 次以上 request_confirmation ——\n"
                    "   问完一个问题拿到答案后，应先基于已有信息推进或给初步诊断，\n"
                    "   再决定是否需要问下一个。\n"
                    "4. 在用户刚回答完一个问题后立即追问同一维度的新问题 ——\n"
                    "   应先确认\"已收到你的回答：X\"，给一句简短判断或下一步说明，\n"
                    "   再问下一个。\n\n"
                    "**正确的询问节奏**：问1个问题 → 收到答案 → 给一句话反馈/初步判断 →\n"
                    "推进到能推进的步骤 → 确实缺下一个关键信息时再问。每两次询问之间\n"
                    "至少穿插一个工具调用或一段分析，让用户感受到进展而非审问。\n\n"
                    "## 思考框架（基线逻辑 — 回答任何问题都遵循）\n\n"
                    "以下逻辑来自工业质检 Agent 决策报告，是处理任何请求的思考基线。\n"
                    "问题可能不同，但思考方式必须一致。\n\n"
                    "### 第一原则：不把用户的问题直接翻译成模型任务\n\n"
                    "用户说\"帮我识别不良品\"\"判断焊缝合不合格\"\"看看有没有缺陷\"时，\n"
                    "不能立刻回答\"用 YOLO\"\"用分类模型\"\"用异常检测\"。这些表达还没定义清楚：\n"
                    "合格边界是什么？缺陷是否可测量？是否有灰区？漏检和误检代价是否相同？\n"
                    "数据中是否真的包含缺陷？是否有历史判定结果可作弱标签？\n"
                    "你的第一职责是把模糊业务需求拆成可执行问题。\n\n"
                    "### 标准优先序：判定标准 → 标注标准 → 模型标准\n\n"
                    "三类标准不能混在一起：\n"
                    "- **业务判定标准**（什么算合格/返修/报废/让步接收）— 质检负责人主导\n"
                    "- **标注执行标准**（标注员看到图像该标什么类别、框哪里）— Agent+专家+标注负责人\n"
                    "- **模型验收标准**（漏检率/误检率/召回率/节拍/稳定性）— 业务+算法负责人\n\n"
                    "业务判定标准不清 → 标注标准漂移 → 模型学习噪声。\n"
                    "不要跳过标准直接做标注或训练。\n\n"
                    "### 四种动作切换 — 判断当前最缺什么\n\n"
                    "| 动作 | 使用时机 | 对应能力 |\n"
                    "|:-----|:---------|:---------|\n"
                    "| 咨询用户 | 业务背景/风险/标准/资源不清 | request_confirmation |\n"
                    "| 理解数据 | 数据质量/分布/缺陷比例不清 | delegate('data-understanding') 或 analyze_image |\n"
                    "| 外部查证 | 标准/行业缺陷定义/相似方案不清 | 内部知识库 → web_search |\n"
                    "| 决策推进 | 信息足够时 | design_workflow/launch_workflow 或直接给方案 |\n\n"
                    "成熟 Agent 不应一直问、不应一直查、不应一直做实验。判断瓶颈在哪，选对应动作。\n\n"
                    "### 动态循环决策 — 每次获取信息后做四步判断\n\n"
                    "拿到用户回答/工具结果/查证资料后，不要直接推进，先做四步判断：\n"
                    "1. **补齐前提？** — 这个回答是否补齐了当前阶段缺失的前提条件？\n"
                    "2. **暴露阻断？** — 是否暴露阻断性问题（标准在老师傅脑子里、数据无批次信息）？\n"
                    "   阻断则回退到标准/数据探索阶段。\n"
                    "3. **改变路线？** — 是否改变后续技术路线（如缺陷率<1% 改走异常检测）？\n"
                    "4. **预防后续？** — 是否预判后续风险（标注一致性不足、部署环境变化）？\n\n"
                    "信息不足时坚决回退，不要硬推。标准不清不做标注，数据没体检不定模型路线。\n\n"
                    "## 何时委派 subagent\n\n"
                    "主 agent 不必自己做所有事。以下场景应 delegate 给专用 subagent：\n"
                    "- **数据理解/体检** → delegate('data-understanding')：数据集级统计分析、\n"
                    "  分布探索、标签质量评估。脏数据不要直接进标注。\n"
                    "- **复杂工作流设计** → delegate('weld-architect')：多节点编排、\n"
                    "  依赖关系复杂的流程设计\n"
                    "- **标注质量审查** → delegate('weld-reviewer')：标注完成后的一致性检查、\n"
                    "  置信度评估\n"
                    "- **并行信息搜索** → delegate('weld-explorer')：需同时查多个数据源时\n\n"
                    "判断标准：子任务需要多轮工具调用+独立上下文+专门能力 → delegate；\n"
                    "单次查询 → 自己直接调工具。\n\n"
                    "## 知识获取 — 三层 fallback\n\n"
                    "遇到知识不足时，按以下顺序获取，不要跳层：\n"
                    "1. **内部知识库（1-2次）**：\n"
                    "   - 查工业图像数据集元信息 → search_vision_knowledge\n"
                    "   - 查标准条款/推理模式/VQA模板/工艺文档 → search_reasoning_knowledge\n"
                    "   - 查基础标准/案例/工艺 → search_standards/search_cases/search_process\n"
                    "2. **联网搜索（1次）**：内部查不到时 → web_search\n"
                    "   （查国标全文/论文/开源数据集/缺陷分类/部署方案）\n"
                    "3. **弹窗问用户**：以上都不足时 → request_confirmation\n\n"
                    "不要用不同 query 穷举式反复查同一工具（架构会拦截第3次）。\n\n"
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
