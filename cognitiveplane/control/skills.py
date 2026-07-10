"""Agent Skills 模块化 — 工业领域专业化技能注册与选择.

设计原则:
  1. 每个 Skill = 专业知识(prompt) + 可用工具白名单 + 触发条件
  2. Skill 与 ToolRegistry 解耦：Skill 决定"哪些工具对当前场景可见"
  3. 声明式加载：从 .weldevent/skills/*.md 加载 Skill 声明（YAML frontmatter + Markdown）
  4. 统一架构：Skill 和 SubAgent 使用同一套声明格式，由 DeclarationLoader 统一加载
  5. 语义匹配：优先用 LLM 做意图分类，关键词匹配作为快速回退
  6. 向后兼容：未匹配到 skill 时 fallback 到通用模式（所有 LLM 可见工具）

参考来源:
  - Anthropic Claude Code SKILL.md (2026)
  - OpenAI Codex sub-agent discovery
  - Microsoft Agent Framework / Semantic Kernel plugins
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from cognitiveplane.control.declaration import DeclarationLoader, Declaration

if TYPE_CHECKING:
    from cognitiveplane.adapters.llm.types import LLMProvider

logger = logging.getLogger("skills")


@dataclass
class Skill:
    """单个 Agent Skill。

    name: 技能唯一标识
    description: 人类可读描述，也用于 LLM 选择时的语义匹配
    system_prompt: 附加到 ReAct system prompt 的专业化指令
    allowed_tools: 该技能允许 LLM 调用的工具白名单
    triggers: 触发关键词列表（快速回退）
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


def _declaration_to_skill(decl: Declaration) -> Skill:
    """将 Declaration 转换为 Skill（适配现有 SkillRegistry 接口）。"""
    return Skill(
        name=decl.name,
        description=decl.description,
        system_prompt=decl.build_system_prompt(),
        allowed_tools=decl.tools.copy(),
        triggers=decl.triggers.copy(),
        priority=decl.priority,
    )


class SkillRegistry:
    """技能注册表 — 管理所有 Skill 并提供语义选择能力。

    选择策略（对齐 Claude Code 渐进式披露）：
      1. LLM 语义分类为首选 — LLM 看到所有 skill 的 name+description+triggers，
         自主决策哪个 skill 最匹配（对齐 Claude Code 的 L1 元数据常驻 + LLM 自主选择）
      2. 关键词高分捷径 — score >= 3 时直接命中（极少数高置信度场景，省 LLM 调用）
      3. 短句保持锁定 — "确认/好的/同意"等不带新意图的短回复保持当前 skill
      4. LLM 不可用时回退到关键词匹配
      5. 无匹配 → fallback 通用模式（所有工具可见）
    """

    # 高置信度关键词匹配阈值 — 超过此值直接命中，不走 LLM
    KEYWORD_FAST_PATH_THRESHOLD = 3
    # 短句长度阈值 — 低于此长度认为是确认/跟进，保持锁定 skill
    SHORT_INPUT_THRESHOLD = 8

    def __init__(self, skills: list[Skill] | None = None) -> None:
        self._skills: list[Skill] = skills or []
        self._by_name: dict[str, Skill] = {s.name: s for s in self._skills}

    def register(self, skill: Skill) -> None:
        self._by_name[skill.name] = skill
        self._skills = [s for s in self._skills if s.name != skill.name]
        self._skills.append(skill)

    def get(self, name: str) -> Skill | None:
        return self._by_name.get(name)

    def select_skill(self, user_input: str | list[dict]) -> Skill | None:
        """关键词匹配 — 快速通道，同步返回。

        用于 LLM 不可用或极高置信度（score >= KEYWORD_FAST_PATH_THRESHOLD）的场景。
        """
        text = self._coerce_text(user_input)
        best: Skill | None = None
        best_score = 0
        for skill in self._skills:
            score = skill.match_score(text)
            if score == 0:
                continue
            if (
                best is None
                or score > best_score
                or (score == best_score and skill.priority > best.priority)
            ):
                best = skill
                best_score = score

        if best:
            logger.info("[skills] keyword-matched skill=%s score=%s", best.name, best_score)
        return best

    async def select_skill_semantic(
        self,
        user_input: str | list[dict],
        llm_provider: "LLMProvider | None" = None,
        session: dict | None = None,
    ) -> Skill | None:
        """LLM 语义分类 — 首选路径，对齐 Claude Code。

        策略：
          1. 短句检测：输入很短且是确认/跟进 → 保持 session 锁定的 skill
          2. 关键词高分捷径：score >= 3 → 直接命中
          3. LLM 语义分类：构建富 prompt（name+description+triggers+适用场景），
             让 LLM 自主选择最匹配的 skill
          4. LLM 不可用 → 回退到关键词匹配
          5. 返回 None → 走通用模式
        """
        text = self._coerce_text(user_input)
        if not text.strip():
            return None

        # 1. 短句保持锁定 — "确认/好的/同意/继续/列出来" 等不带新意图
        if session and len(text.strip()) <= self.SHORT_INPUT_THRESHOLD:
            locked_name = session.get("locked_skill")
            if locked_name and locked_name in self._by_name:
                logger.info("[skills] short input '%s' keeps locked skill=%s", text, locked_name)
                return self._by_name[locked_name]

        # 2. 关键词高分捷径 — 极高置信度直接命中
        keyword_best = self.select_skill(text)
        if keyword_best is not None and keyword_best.match_score(text) >= self.KEYWORD_FAST_PATH_THRESHOLD:
            logger.info("[skills] keyword fast-path skill=%s (score>=%d)",
                        keyword_best.name, self.KEYWORD_FAST_PATH_THRESHOLD)
            return keyword_best

        # 3. LLM 语义分类（首选）
        if llm_provider is None:
            # LLM 不可用，回退到关键词结果
            return keyword_best

        try:
            classification_prompt = self._build_classification_prompt(text)
            if not classification_prompt:
                return None

            from cognitiveplane.capability.provider import LLMRequest
            request = LLMRequest(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是意图分类器。根据用户输入，从候选 skill 中选择最匹配的一个。\n"
                            "只返回 JSON: {\"skill\": \"<name>\"} 或 {\"skill\": null}。\n"
                            "如果用户意图模糊，无法确定匹配哪个 skill，返回 {\"skill\": null}。\n"
                            "不要返回其他内容。\n\n"
                            "关键判定规则：\n"
                            "- 用户提到'标注/打标注/做标注/标数据/label/annotation' → annotation_interactive\n"
                            "- 用户提到'质检/不良品/能不能做/方案' → industrial_qa_diagnosis\n"
                            "- 用户提到'焊缝图片/照片/分析缺陷' → weld_iqa\n"
                            "- 注意区分：'需要标注'是标注流程，不是质检诊断或图像分析"
                        ),
                    },
                    {
                        "role": "user",
                        "content": classification_prompt,
                    },
                ],
                max_tokens=100,  # 50→100：防止 LLM 输出前缀文本后 JSON 被截断
                temperature=0.0,
            )
            llm_response = await llm_provider.complete(request)
            response_text = llm_response.content

            result = self._parse_classification_result(response_text)
            if result and result in self._by_name:
                logger.info("[skills] LLM-classified skill=%s", result)
                return self._by_name[result]
            elif result is None:
                # LLM 明确返回 null — 意图不明确或无匹配
                logger.info("[skills] LLM returned null (no match or ambiguous)")
                # 回退到关键词低分匹配（如果有）
                return keyword_best
        except Exception:
            logger.debug("[skills] LLM classification failed, falling back to keyword", exc_info=True)

        return keyword_best

    def _build_classification_prompt(self, user_text: str) -> str:
        """构建富分类 prompt — 对齐 Claude Code L1 元数据。

        包含每个 skill 的：
        - name + description（意图导向）
        - triggers（关键词覆盖面）
        - priority（优先级，冲突时高优先级胜出）
        """
        lines = [f"用户输入: {user_text}\n\n候选 skill:\n"]
        for skill in sorted(self._skills, key=lambda s: -s.priority):
            triggers_str = ", ".join(skill.triggers[:12]) if skill.triggers else "（无）"
            lines.append(
                f"- **{skill.name}** (优先级:{skill.priority}): {skill.description}\n"
                f"  关键词: {triggers_str}"
            )
        lines.append(
            "\n返回 JSON: {\"skill\": \"<name>\"} 或 {\"skill\": null}"
        )
        return "\n".join(lines)

    def _parse_classification_result(self, response: str) -> str | None:
        """解析 LLM 分类结果。"""
        try:
            # 尝试提取 JSON
            text = response.strip()
            # 处理可能的 markdown 代码块包裹
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
            if text.startswith("{"):
                parsed = json.loads(text)
                return parsed.get("skill")
        except (json.JSONDecodeError, KeyError):
            pass
        return None

    @staticmethod
    def _coerce_text(user_input: str | list[dict]) -> str:
        """把多模态输入转成纯文本用于匹配。"""
        if isinstance(user_input, str):
            return user_input
        parts: list[str] = []
        for item in user_input:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item.get("type", "")))
        return " ".join(parts)

    @property
    def skills(self) -> list[Skill]:
        return list(self._skills)


def build_welding_skill_registry() -> SkillRegistry:
    """工厂函数 — 从 .weldevent/skills/*.md 声明文件加载 skill 集合。

    替代了之前硬编码的 WELDING_SKILLS 列表。用户只需在 .weldevent/skills/ 下
    添加 .md 文件即可扩展 skill，无需修改代码或重启。
    """
    loader = DeclarationLoader()
    skills = [_declaration_to_skill(d) for d in loader.load_skills().values()]
    if not skills:
        logger.warning("[skills] 未加载到任何 skill 声明，请检查 .weldevent/skills/ 目录")
    return SkillRegistry(skills)


__all__ = [
    "Skill",
    "SkillRegistry",
    "build_welding_skill_registry",
]
