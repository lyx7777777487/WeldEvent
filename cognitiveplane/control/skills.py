"""Agent Skills 模块化 — 焊接领域专业化技能注册与选择.

设计原则:
  1. 每个 Skill = 专业知识(prompt) + 可用工具白名单 + 触发条件
  2. Skill 与 ToolRegistry 解耦：Skill 决定"哪些工具对当前场景可见"
  3. 轻量实现：Python dataclass + 关键词匹配，不引入外部框架
  4. 向后兼容：未匹配到 skill 时 fallback 到通用模式（所有 LLM 可见工具）

参考来源:
  - Anthropic Agent Skills (Claude Code, 2026)
  - Microsoft Agent Framework / Semantic Kernel plugins
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("skills")


@dataclass
class Skill:
    """单个 Agent Skill。

    name: 技能唯一标识
    description: 人类可读描述，也用于 LLM 选择时的语义匹配
    system_prompt: 附加到 ReAct system prompt 的专业化指令
    allowed_tools: 该技能允许 LLM 调用的工具白名单
    triggers: 触发关键词列表（简单规则）
    priority: 匹配优先级，数字越大越优先
    """
    name: str
    description: str
    system_prompt: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    priority: int = 0

    def match_score(self, user_input: str) -> int:
        """根据触发关键词计算匹配分数（0 表示不匹配）。"""
        text = user_input.lower()
        score = 0
        for keyword in self.triggers:
            if keyword.lower() in text:
                score += 1
        return score


# ── 焊接领域预置 Skills ──

WELDING_SKILLS: list[Skill] = [
    Skill(
        name="standard_query",
        description="查询焊接国家标准、规范、工艺评定要求",
        system_prompt=(
            "你是焊接标准查询专家。用户询问国标、规范、工艺参数范围时，"
            "优先调用 search_standards / search_cases / explain_decision。"
            "不要调用 design_workflow、launch_workflow、analyze_image 等不相关工具。"
        ),
        allowed_tools=[
            "search_standards",
            "search_cases",
            "explain_decision",
            "read_weldmap",
            "archive_memory",
        ],
        triggers=["标准", "规范", "国标", "nb/t", "gb/t", "工艺", "参数", "预热", "电流", "电压"],
        priority=10,
    ),
    Skill(
        name="weld_iqa",
        description="焊缝图像质量评估与缺陷分析",
        system_prompt=(
            "你是焊缝图像质量评估专家。用户上传焊缝图像或要求分析图片时，"
            "必须调用 analyze_image 进行图像分析，必要时查询历史案例 search_cases。"
            "不要调用 design_workflow、launch_workflow 等不相关工具。"
        ),
        allowed_tools=[
            "analyze_image",
            "search_cases",
            "search_standards",
            "explain_decision",
            "read_weldmap",
        ],
        triggers=["图", "图片", "照片", "焊缝", "缺陷", "分析", "看看", "quality", "iqa", "image"],
        priority=20,
    ),
    Skill(
        name="workflow_design",
        description="设计并启动焊接质检工作流（IQA-PPA 自动化 + 标注交互式）",
        system_prompt=(
            "你是焊接质检工作流设计专家。用户要求「设计工作流」「启动工作流」"
            "「执行质检流程」「跑一遍 IQA-PPA-标注 全流程」时：\n"
            "  使用 design_workflow 设计方案后，直接调用 launch_workflow 启动 —— \n"
            "  架构会自动拦截 launch_workflow 并要求用户确认，你无需自己等待确认。\n"
            "  **不要在 launch_workflow 前调 request_confirmation 问\"是否启动\"——架构会拦截，直接调即可。**\n"
            "  用户确认后工作流会自动执行，前端会逐步展示每个节点的执行进度。\n"
            "  - 用户问'工作流进行到哪了' → 调 control_workflow(action=query)\n"
            "  - 用户说'暂停工作流' → 调 control_workflow(action=pause)\n"
            "  - 用户说'继续' → 调 control_workflow(action=resume)\n"
            "  - 用户说'取消工作流' → 调 control_workflow(action=cancel)\n"
            "\n"
            "## 何时弹窗问用户（调 request_confirmation）\n"
            "  当信息不足以设计出合理的工作流时，先弹窗问用户再 design_workflow：\n"
            "  - 用户只说\"做质检\"但没说范围 → 弹窗问质检范围（只 IQA / IQA+PPA / 全流程）\n"
            "  - 用户提到多种缺陷类型但未明确优先级 → 弹窗问要检测哪些缺陷\n"
            "  - IQA 返回 marginal 后是否继续 PPA → 弹窗让用户决策\n"
            "  不要在 launch_workflow 前弹窗——架构会拦截。\n"
            "\n"
            "## 架构边界（必须严格遵守）\n"
            "  工作流（L3 Temporal）只管自动化节点：IQA / PPA / detect_defects / preprocess 等。\n"
            "  **标注节点不要放进工作流！** 标注流程涉及数据集选择/作业名/标签/标注员等人工决策，\n"
            "  必须由 LLM 通过标注 MCP 工具（list_datasets/create_job/create_task/...）与用户交互式完成。\n"
            "\n"
            "  当用户要求「IQA-PPA-标注 全流程」时，正确做法：\n"
            "  1. design_workflow 只设计 IQA + PPA 两个节点（不要设计 annotation 节点）\n"
            "  2. launch_workflow 启动工作流（架构拦截要用户确认）\n"
            "  3. 工作流执行完后，**主动告知用户**：「IQA+PPA 已完成，接下来进入标注流程，\n"
            "     我会逐步引导您选择数据集、创建作业、上传图片」\n"
            "  4. launch_workflow 成功后架构会自动解锁 skill。下一轮用户说「开始标注」时,\n"
            "     会自动切换到 annotation_interactive skill,届时再调 list_datasets。\n"
            "     **在本 skill 内不要尝试调 list_datasets/create_job 等标注工具 —— 它们不在 allowed_tools 里。**\n"
            "\n"
            "  绝对禁止：\n"
            "  - 在 design_workflow 的 nodes 中设计 capability=annotation 的节点\n"
            "  - 用 auto_annotate action（黑盒执行，无人工确认）\n"
            "  - 把标注拆成多个 L3 节点（list_datasets_node/create_job_node 等）——\n"
            "    这依旧是 L3 黑盒，用户无法逐步确认\n"
        ),
        allowed_tools=[
            "design_workflow",
            "launch_workflow",
            "control_workflow",
            "read_weldmap",
            "search_standards",
            "request_confirmation",
        ],
        triggers=["工作流", "workflow", "流程", "启动", "设计", "质检流程",
                  "方案", "测试", "质检", "编排", "执行", "ppa", "iqa-ppa",
                  "暂停", "继续", "取消", "进度", "进行到哪"],
        priority=30,
    ),
    Skill(
        name="annotation_interactive",
        description="交互式标注流程 — 逐步确认每个决策点",
        system_prompt=(
            "你是标注流程交互式编排专家。用户要求标注、查看数据集、创建标注作业、"
            "上传图片到标注平台、分配标注任务、触发 AI 预标注时，你必须主动调标注 MCP 工具，"
            "而不是只给文字回复。每步写入类操作都要让用户确认。\n"
            "\n"
            "## 强制规则（违反即失败）\n"
            "  1. **收到任何标注相关请求时，第一轮必须调 list_datasets** —— 不要只回复文字！\n"
            "     即使是元问题（如「标注节点颗粒度」「怎么标注」「标注流程是什么」），\n"
            "     也要先调 list_datasets 展示现有数据集，再向用户解释流程\n"
            "  2. **绝对不要硬编码**数据集名/作业名/标签/标注员 — 每次都要询问用户\n"
            "  3. **不要走 design_workflow** — 你直接调标注 MCP 工具即可，无需编排工作流\n"
            "\n"
            "## 推荐流程（每步都要等用户响应）\n"
            "  1. 先调 list_datasets 查看现有数据集（查询类，直接执行）→ 向用户展示数据集列表\n"
            "  2. **弹窗问用户**：要用现有数据集，还是新建？调 request_confirmation(\n"
            "     question=\"选择数据集\", options=[\"用现有:xxx\",\"用现有:yyy\",\"新建数据集\"]) \n"
            "  3. 用户决定后，若需上传图片 → 调 upload_images（架构会拦截要用户确认）\n"
            "  4. 调 create_job 创建标注作业（架构拦截要用户确认）— 缺业务参数时**先弹窗**：\n"
            "     - 作业名未指定 → request_confirmation(question=\"请选择作业名\", \n"
            "       options=[\"weld-iqa-YYYYMMDD\",\"自定义名称\"])\n"
            "     - 标注类别未指定 → request_confirmation(question=\"请选择标注类别\", \n"
            "       options=[\"气孔\",\"夹渣\",\"裂纹\",\"未熔合\",\"咬边\",\"合格\",\"全部缺陷类型\"])\n"
            "     - 不要硬编码默认值\n"
            "  5. 调 create_task 创建标注任务（架构拦截）— 缺业务参数时弹窗：\n"
            "     - 要标注哪些图片 → request_confirmation(question=\"选择标注图片\", \n"
            "       options=[\"本会话已上传的全部\",\"我来指定\"])\n"
            "     - 分配给哪位标注员 → request_confirmation(question=\"指定标注员\", \n"
            "       options=[\"张工\",\"李工\",\"王工\",\"我来指定\"])\n"
            "  6. 调 assign_task 分配任务给标注员（架构拦截）\n"
            "  7. 询问是否要触发 AI 预标注 → request_confirmation(\n"
            "     question=\"是否触发 AI 预标注？\", options=[\"触发\",\"不触发，手动标注\"])\n"
            "     用户选\"触发\" → 调 trigger_ai（架构拦截，不可逆）\n"
            "  8. 调 list_tasks/get_job 查看进度（查询类，直接执行）\n"
            "\n"
            "## 关键原则\n"
            "  - **缺业务参数（作业名/标签/标注员等）时，优先用 request_confirmation 弹窗**，\n"
            "    不要只用文本回复问用户——弹窗能让用户点按钮而不是打字。\n"
            "  - **查询类工具**（list_datasets/get_dataset/list_jobs/get_job/list_tasks）"
            "你可直接调用，无拦截\n"
            "  - **写入类工具**（create_job/create_task/upload_images/trigger_ai/assign_task）\n"
            "    架构会自动拦截并要求用户确认 — 你只需调用并传参，前端会弹确认卡片\n"
            "  - 不要在写入类工具前调 request_confirmation 问\"是否执行\"——架构会拦截。\n"
            "    request_confirmation 只用于**收集业务参数**和**决策方向**，不用于\"是否执行\"。\n"
            "  - 每步执行后向用户汇报结果，再询问下一步\n"
        ),
        allowed_tools=[
            # ── 标注 MCP 工具（查询类 Tier-A，直接可调）──
            "list_datasets",
            "get_dataset",
            "list_jobs",
            "get_job",
            "list_tasks",
            # ── 标注 MCP 工具（写入类 Tier-B，走 approval gate）──
            "create_job",
            "create_task",
            "upload_images",
            "trigger_ai",
            "assign_task",
            # ── 主动提问弹窗（缺业务参数/决策方向时用）──
            "request_confirmation",
        ],
        triggers=["标注", "数据集", "dataset", "标注员", "标注作业",
                  "标签", "上传图片", "触发 ai", "ai 预标注",
                  "label", "annotation"],
        priority=35,  # 高于 workflow_design(30) 确保纯标注场景优先命中
    ),
    Skill(
        name="parameter_recommend",
        description="焊接工艺参数推荐与优化",
        system_prompt=(
            "你是焊接工艺参数推荐专家。用户询问焊接电流、电压、速度、预热等参数时，"
            "先调用 search_standards 查规范，再基于查询结果给出推荐参数建议。"
            "如果参数违反安全规程，必须拒绝并说明原因。"
        ),
        allowed_tools=[
            "search_standards",
            "explain_decision",
            "read_weldmap",
            "request_confirmation",
        ],
        triggers=["参数", "电流", "电压", "速度", "预热", "推荐", "优化", "工艺参数"],
        priority=15,
    ),
]


class SkillRegistry:
    """技能注册表 — 管理所有 Skill 并提供选择能力。"""

    def __init__(self, skills: list[Skill] | None = None) -> None:
        self._skills: list[Skill] = skills or []
        self._by_name: dict[str, Skill] = {s.name: s for s in self._skills}

    def register(self, skill: Skill) -> None:
        """注册新 skill（同名覆盖）。"""
        self._by_name[skill.name] = skill
        # 保持列表唯一性
        self._skills = [s for s in self._skills if s.name != skill.name]
        self._skills.append(skill)

    def get(self, name: str) -> Skill | None:
        return self._by_name.get(name)

    def select_skill(self, user_input: str | list[dict]) -> Skill | None:
        """根据用户输入选择最匹配的 Skill。

        当前实现：关键词匹配 + 优先级打破平手。
        未来可升级：用 LLM 意图分类或 embedding 语义匹配。
        """
        text = self._coerce_text(user_input)
        best: Skill | None = None
        best_score = 0
        for skill in self._skills:
            score = skill.match_score(text)
            if score == 0:
                continue
            # 同分看 priority，priority 高者优先
            if (
                best is None
                or score > best_score
                or (score == best_score and skill.priority > best.priority)
            ):
                best = skill
                best_score = score

        if best:
            logger.info("[skills] selected skill=%s score=%s", best.name, best_score)
        return best

    @staticmethod
    def _coerce_text(user_input: str | list[dict]) -> str:
        """把多模态输入转成纯文本用于匹配。"""
        if isinstance(user_input, str):
            return user_input
        # list[dict] 可能是 OpenAI 多模态格式，提取 text 部分
        parts: list[str] = []
        for item in user_input:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                else:
                    # image_url 等用占位描述参与关键词匹配
                    parts.append(str(item.get("type", "")))
        return " ".join(parts)

    @property
    def skills(self) -> list[Skill]:
        return list(self._skills)


def build_welding_skill_registry() -> SkillRegistry:
    """工厂函数 — 构造焊接领域预置 skill 集合。"""
    return SkillRegistry(WELDING_SKILLS)


__all__ = [
    "Skill",
    "SkillRegistry",
    "WELDING_SKILLS",
    "build_welding_skill_registry",
]
