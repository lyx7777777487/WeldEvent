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

        # 1. WELDEVENT.md — 项目级规约
        weldevent_md = _read_md_if_exists(WELDEVENT_MD_PATH)
        if weldevent_md:
            blocks.append("## 项目规约 (WELDEVENT.md)\n\n" + weldevent_md)
            injected_sections.append("weldevent_md")

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
        """Build system prompt with worldview injection + context + tools.

        Plan §5.1 line 894-902: ReAct Loop 启动时自动注入:
          - WELDEVENT.md (项目级规约)
          - OPERATOR.md (操作员偏好)
          - CASE_BRIEF (当前 case 简报)
          - Memory.search (相关历史修正/类似案例)

        注入方式: 提供"世界观"，不规定"步骤"
        关键: 这是上下文，不是流水线第一步 — 即使 LLM 完全不引用也算正确行为
        所有 section 都是 optional，缺失文件/空数据/Memory 故障时优雅跳过。

        Plan §0.1 规则 1 (line 145): 架构替 LLM 做的看不见的事必须记进 EventLog.
        本方法在注入成功后 emit 一个 STATE_TRANSITION 事件, 记录注入了哪些
        section + Memory hit ids, LLM 下一轮可查.

        Phase 5 Agent Skills: 如果选中 skill，则注入其专业化 system_prompt 并
        按 allowed_tools 白名单过滤可见工具。
        """
        allowed_tools = skill.allowed_tools if skill else None
        tools_desc = "\n".join(
            f"- {name}: {self._tools.get_tool(name).description}"
            for name in self._tools.list_llm_tools(allowed_tools=allowed_tools)
            if self._tools.get_tool(name) is not None
        )

        sections: list[str] = []

        # Phase 5 Agent Skills: 注入专业化角色与约束
        if skill is not None and skill.system_prompt:
            sections.append(f"# 当前场景专业化指令\n\n{skill.system_prompt}")

        # ── Worldview sections (plan §5.1 line 894-902) ──
        worldview, injected_meta = await self._worldview_builder.build_section(context)
        if worldview:
            sections.append(worldview)
            # §0.1 规则 1: 记进 EventLog, LLM 下一轮可查
            record_worldview_injection(self._event_log, injected_meta)

        # ── L3 Activity Catalog (LLM 自主编排工作流的能力边界) ──
        # 用户是业务人员，不会明确指定 capability 名。LLM 需参考本 catalog
        # 自主选择 activity，通过 design_workflow 的 nodes 参数传入编排结果。
        # 已实现的 activity 真实执行；未实现的虚拟执行（返回占位结果，不报错）。
        sections.append(_build_activity_catalog_section())

        # ── Tools + algorithmic flow ──
        # 关键：工具分两类，走两种完全不同的路径。
        # LLM 必须在第一步就判断用户意图，选对路径，不是把所有请求都当成工作流。
        sections.append(
            "你可以使用以下工具来完成任务：\n\n"
            f"{tools_desc}\n\n"
            "## 意图判断\n\n"
            "用户说的话归为两类：\n\n"
            "### 短路径 — 问答/看图/聊天（只调认知工具，不走工作流）\n"
            "- 用户只是问问题、想知道什么、让你看图描述、了解情况\n"
            "- 示例：「这是什么图」「这张图里有什么」「气孔长什么样」「帮我看看这张」「分析一下」\n"
            "- 做法：调 analyze_image / search_* / read_weldmap 拿信息 → 直接回答。到此结束。\n"
            "- **禁止**：对这类请求调用 design_workflow 或 launch_workflow\n\n"
            "### 长路径 — 质检/标注/执行任务\n"
            "- 用户明确要求做质检、检测缺陷、标注、批量处理等工业操作\n"
            "- 示例：「帮我做质检」「检测这批焊缝的缺陷」「标注有问题的」「按 IQA 标准全部跑一遍」\n"
            "- 做法：\n"
            "  1. 先调 analyze_image / search_standards 了解情况（analyze_image 同一张图只调一次）\n"
            "  2. 调 design_workflow 设计工作流草案\n"
            "  3. 调 launch_workflow 启动 — **架构会自动拦截 launch_workflow 并要求用户确认**，\n"
            "     你无需自行判断「是否该等用户确认」，直接调 launch_workflow 即可，\n"
            "     确认门禁由架构保证。**design_workflow 之后必须立刻调 launch_workflow**，不要在两者之间反复 read_weldmap 或 search_standards。\n"
            "  4. launch_workflow 返回后：把 node_results 中的每个节点结果用表格或列表逐项\n"
            "     展示给用户（节点名、状态、结果摘要），用用户能理解的语言描述，不要直接 dump JSON。\n"
            "     示例回复格式：\n"
            "     \"工作流已执行完成，结果如下：\n"
            "     | 节点 | 状态 | 结果 |\n"
            "     |---|---:|---|\n"
            "     | 图像质量评估 (IQA) | ✅ | 焊缝区域比 36.46% (>30%通过)，曝光正常，清晰度偏低 |\n"
            "     | 图像预处理 (PPA) | ✅ | 应用降噪、锐化处理 |\n"
            "     | 标注任务 | ✅ | 作业已创建，1张图片已上传，AI标注已触发 |\"\n"
            "  5. 如果有节点失败，说明原因并给出修复建议\n\n"
            "### 判断原则\n"
            "- 用户问「是什么/看看/分析/解释/怎么样」→ 短路径\n"
            "- 用户说「做/跑/检测/标注/处理/质检」并带有明确的执行意图 → 长路径\n"
            "- 不确定时走短路径，先回答用户，不要着急设计工作流\n\n"
            "## 标注能力 — 两条路径\n\n"
            "标注有两条完全不同的执行路径，根据用户意图选择：\n\n"
            "### 路径 A: 交互式逐步标注（直接调 MCP 工具）\n"
            "适用场景：用户想逐步确认标注流程的每个决策点。\n"
            "示例：「查看现有数据集」「创建个标注作业」「把这张图上传到标注平台」\n"
            "工具集（已直接暴露给你，无需走 design_workflow）：\n"
            "- **查询类**（Tier-A，你可直接调用，无拦截）：\n"
            "  - list_datasets: 列出所有数据集\n"
            "  - get_dataset: 查看指定数据集详情\n"
            "  - list_jobs: 列出标注作业\n"
            "  - get_job: 查看作业详情\n"
            "  - list_tasks: 列出标注任务\n"
            "- **写入类**（Tier-B，架构自动拦截要用户确认，你只管调用）：\n"
            "  - create_job: 创建标注作业（需询问用户作业名/标签集）\n"
            "  - create_task: 创建标注任务（需询问标注哪些图/标注员）\n"
            "  - upload_images: 上传图片到数据集\n"
            "  - assign_task: 分配任务给标注员\n"
            "  - trigger_ai: 触发 AI 预标注（不可逆，必须用户确认）\n"
            "推荐流程：list_datasets → 询问用户 → create_job（拦截）→ create_task（拦截）"
            " → assign_task（拦截）→ trigger_ai（拦截）→ list_tasks 查进度。\n"
            "**关键原则**：不要硬编码作业名/标签/标注员，每步都询问用户。\n\n"
            "### 路径 B: 批量自动标注（走 design_workflow 的 annotation 节点）\n"
            "适用场景：用户要一个端到端自动化流程，一次性跑完 IQA→PPA→标注。\n"
            "示例：「设计个方案测试 IQA-PPA-标注」「跑一遍质检全流程」\n"
            "标注节点 (capability=annotation) 支持 11 个 action：\n"
            "- **auto_annotate**（推荐）：一键完成全链路（list_datasets→get_dataset→create_job→"
            "upload_images→create_task→trigger_ai），自动上传用户图片到标注平台。"
            "适合大多数「标注这张图」的场景。\n"
            "- **单一 action**（10 个 MCP tool）：适合细粒度控制，如只查看数据集(list_datasets)、"
            "只创建作业(create_job)等。需要你自行编排多步调用。\n"
            "在 design_workflow 的 nodes 参数中，annotation 节点的 input_data.action 指定。\n\n"
            "约束：\n"
            "- 不重复调用同一工具的相同参数\n"
            "- 优先用工具拿信息再回答，不要凭空猜测\n"
            "- 不要对纯问答类请求设计或启动工作流\n"
            "- **analyze_image 一次只调一张图**：多图时每轮迭代只调一次 analyze_image，\n"
            "  让系统串行处理。禁止同一轮并发调用 2+ 次 analyze_image，\n"
            "  否则视觉 API 限流导致第二张超时。一轮分析完再看下一张。\n\n"
            "## 知识获取策略 — 三层 fallback\n\n"
            "当被问到标准/规范/技术知识时，按以下顺序获取，**不要穷举 query 反复查同一工具**：\n"
            "1. **search_standards**（内部标准库）：查询已知的标准号、条款。查不到换工具，\n"
            "   不要换 query 反复查（最多调 2 次，还不行就走第 2 步）。\n"
            "2. **web_search**（联网搜索）：内部库没有时，调 `web_search(query=\"GB/T 3323 焊缝射线检测等级\")`\n"
            "   获取最新标准信息、官方文档、技术资料。一次查询拿不到就基于已有结果回答。\n"
            "3. **request_confirmation**（向用户要线索）：联网也查不到时，弹窗问用户，\n"
            "   如 `request_confirmation(question=\"我未找到 X 标准，您能提供标准号或文档吗？\", options=[\"我来上传文档\",\"换一个标准\",\"用通用知识回答\"])`\n\n"
            "**反模式（禁止）**：用 5 种不同 query 反复查 search_standards。这是穷举式调用，\n"
            "架构会拦截，且浪费用户时间。正确做法是查 1-2 次拿不到就走 web_search 或弹窗。\n\n"
            "## 主动提问 — 何时弹窗、何时不弹\n\n"
            "你有 `request_confirmation` 工具，调用后会**弹出带选项的弹窗**给用户（不是聊天框文本回复）。\n"
            "用法：`request_confirmation(question=\"...\", options=[\"选项A\",\"选项B\",\"选项C\"], urgency=\"routine\")`\n\n"
            "### 必须弹窗的场景（调用 request_confirmation）\n"
            "1. **缺业务参数无法继续**：例如要创建标注作业但不知道作业名/标签集，\n"
            "   调 `request_confirmation(question=\"请选择本作业使用的标签集\", options=[\"气孔\",\"夹渣\",\"未焊透\",\"咬边\",\"全部缺陷类型\"])`\n"
            "2. **缺决策方向**：例如用户说\"帮我做质检\"但不清楚要哪种工艺路线，\n"
            "   调 `request_confirmation(question=\"请选择质检范围\", options=[\"只做 IQA 图像质量评估\",\"IQA+PPA+缺陷检测全流程\",\"IQA+PPA+标注\"])`\n"
            "3. **知识不足需业务输入**：例如不知道标注员名单、不知道数据集归属项目，\n"
            "   调 `request_confirmation(question=\"请指定标注员\", options=[\"张工\",\"李工\",\"王工\",\"我来指定\"])`\n"
            "4. **关键分叉点**：例如 IQA 返回 marginal 时是继续还是终止，\n"
            "   调 `request_confirmation(question=\"IQA 评估为边缘通过，是否继续 PPA?\", options=[\"继续\",\"终止\",\"看详情再决定\"])`\n\n"
            "### 禁止弹窗的场景\n"
            "1. **能用工具自己查到的信息**：例如\"有哪些数据集\"应该调 list_datasets，\n"
            "   而不是弹窗问用户\"你想用哪个数据集\"——应该先查再让用户选。\n"
            "2. **架构 approval gate 会拦截的工具**：launch_workflow/create_job/create_task/\n"
            "   upload_images/assign_task/trigger_ai 执行前架构自动弹窗，**你不要先调\n"
            "   request_confirmation 问\"是否执行\"**——直接调工具让架构拦截。\n"
            "3. **纯答疑对话**：用户只是问问题/看图/了解情况，不涉及执行决策，不弹窗。\n"
            "4. **同一会话已问过的相同问题**：用户答过的业务参数记在 notes 里，不重复问。\n\n"
            "### 调用规范\n"
            "- **必须传 options 参数**（至少 2 个选项），让用户能点按钮而不是打字。\n"
            "- options 列举最可能的 2-4 个选项，最后一个可加\"我来指定\"兜底。\n"
            "- question 必须具体明确，不要模糊（如\"你想怎么办?\"）。\n"
            "- urgency 默认 routine，紧急决策才用 urgent，关键安全才用 critical。\n"
            "- 调用后等待用户响应，不要在同一轮继续做其他事。\n\n"
            "## 标注流程工具使用 — 关键引导\n\n"
            "### 图片上传：用 upload_image_to_dataset，不要用 upload_images\n"
            "你有 `upload_image_to_dataset` 工具，接受 `image_ref` + `version_id`，\n"
            "内部自动从 ImageStore 取图转 base64 上传。\n"
            "**不要直接调 MCP `upload_images`**（它要 base64，你拿不到）。\n"
            "示例：`upload_image_to_dataset(image_ref=\"PENDING:xxx:0\", version_id=\"01abc...\")`\n\n"
            "### 标注流程推荐路径\n"
            "用户要标注时，优先走 **交互式逐步标注**（不要硬塞进工作流）：\n"
            "1. `list_datasets` → 展示给用户选\n"
            "2. `get_dataset` → 拿 version_id\n"
            "3. `upload_image_to_dataset` → 上传图片（传 image_ref + version_id）\n"
            "4. `create_job` → 创建作业（架构会弹窗确认）\n"
            "5. `create_task` → 创建任务（架构会弹窗确认）\n"
            "6. `trigger_ai` → 触发 AI 预标注（架构会弹窗确认）\n"
            "每步问用户确认，不要一口气全调。\n\n"
            "### 失败恢复 — 不要乱猜原因\n"
            "工具失败时，**看完整错误信息**（不是前 60 字符），按错误类型处理：\n"
            "- `version_id` 相关 → 调 get_dataset 拿最新 version_id 重试\n"
            "- `权限不足/403` → 告诉用户检查 Label Studio 配置\n"
            "- `网络错误/超时` → 重试一次，再失败告诉用户\n"
            "- `数据集没图片` → 先调 upload_image_to_dataset 上传图片\n"
            "**禁止**：失败后说\"我没有这个工具\"——你的工具列表是固定的，\n"
            "失败不代表工具不存在，只代表参数不对或服务异常。\n\n"
            "### 不要反复横跳\n"
            "确定了一条路径就坚持走完。例如选了交互式标注就一步步走，\n"
            "不要中途改成\"走 auto_annotate 工作流\"，除非用户明确要求换路径。\n"
            "失败时修正参数重试，不要换路径逃避问题。\n\n"
            "## ID 类型严格区分 — 避免 404\n"
            "系统里有多种 ID，**不能混用**：\n"
            "- `dataset_id`（数据集 ID，格式 01K...）— list_jobs / get_dataset 用\n"
            "- `version_id`（版本 ID，格式 01k...）— upload_image_to_dataset 用\n"
            "- `job_id`（作业 ID，格式 01K...）— get_job / list_tasks / create_task 用\n"
            "- `task_id`（任务 ID，格式 01K...）— trigger_ai / assign_task 用\n"
            "调 list_tasks / get_job 时**必须传 job_id**，不能传 task_id 或 dataset_id。\n"
            "如果不确定 job_id，先调 `list_jobs(dataset_id)` 拿到正确的 job_id 再调。\n"
            "session notes 会记录每个写入工具产出的 ID，优先用 notes 里的值。\n"
        )

        # Plan-and-Execute: 注入当前已设计的 plan（如果 session 里有）。
        # 这是 Devin/Claude Code 风格的 plan 持久化——LLM 每轮都能看到自己
        # 上轮设计的方案，知道"已设计过"，不会重复 design_workflow 循环。
        # launch_workflow 成功后会清理 current_plan。
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

        # 增强 A：Session Notes 注入（Anthropic Context Engineering 借鉴）
        # 每轮工具执行后生成简短笔记存 session["notes"]，下一轮注入 system prompt
        # 让 LLM 知道"已做过什么"，避免重复调用 + 跨轮失忆。
        notes = session.get("notes", []) if session else []
        if notes:
            notes_lines = ["## 已完成步骤备忘（不要重复已做过的）\n"]
            for n in notes:
                notes_lines.append(f"- {n}")
            sections.append("\n".join(notes_lines))

        # 长路径 Temporal 观察者状态注入（boundary-pinning §3 第三件套）
        # WorkflowObserver 收到 workflow_completed/failed/paused 事件后，
        # 事件已缓存在 WorkflowEventBus（按 session_id 索引）。本节把摘要
        # 注入 system prompt，让 LLM 在用户下一轮发言时立刻感知到 workflow
        # 已完成/失败/暂停，无需用户提示"workflow 跑完了"或主动 query。
        # 设计原则：只读不写，不主动启动 ReAct——避免 LLM 无限循环。
        if self._workflow_event_bus is not None and session:
            wf_summaries = self._workflow_event_bus.get_session_workflow_summary(
                session.get("session_id", "")
            )
            if wf_summaries:
                sections.append(format_workflow_status_section(wf_summaries))

        return [
            {
                "role": "system",
                "content": (
                    "你是 WeldEvent 工业质检Agent系统的决策引擎。\n\n"
                    + "\n\n".join(sections)
                ),
            }
        ]
