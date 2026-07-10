"""统一声明加载器 — 借鉴 Claude Code SKILL.md 声明式格式。

加载 .weldevent/skills/ 和 .weldevent/agents/ 下的所有 .md 声明文件，
按 mode 字段区分 inline（skill）和 delegated（subagent），统一解析 YAML frontmatter。

替代：
  - skills.py 中硬编码的 WELDING_SKILLS 列表（已迁移）
  - subagent.py 中 SubAgentDiscovery（已迁移）

设计原则：
  - 用户写 .md 即可扩展 skill/agent —— 不改代码，不重启部署
  - 统一格式（YAML frontmatter + Markdown body），Skill 和 Agent 用同一套解析器
  - mode 字段区分行为：inline=自动匹配注入, delegated=显式委派执行
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

# 默认声明目录
SKILLS_DIR = Path(".weldevent/skills")
AGENTS_DIR = Path(".weldevent/agents")

Mode = Literal["inline", "delegated"]


@dataclass
class Declaration:
    """统一声明 — 从 .weldevent/skills/ 或 .weldevent/agents/ 的 .md 文件解析。

    字段：
      name: 唯一标识（如 weld_iqa）
      description: 简短描述（用于匹配）
      mode: inline（skill，自动匹配注入）或 delegated（agent，显式委派）
      triggers: 触发关键词列表（仅 mode=inline 有效）
      tools: 工具白名单
      priority: 匹配优先级（数字越大越优先）
      system_prompt: Markdown body（去除 frontmatter 后的内容）
      max_iterations: 最大迭代次数（仅 mode=delegated 有效，默认 5）
      source: 来源文件路径
    """

    name: str
    description: str
    mode: Mode = "inline"
    triggers: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    priority: int = 0
    system_prompt: str = ""
    max_iterations: int = 5
    source: str = ""

    SYSTEM_PROMPT_MAX_CHARS = 16000

    def build_system_prompt(self) -> str:
        """构建注入 system prompt 的文本。超过上限自动截断。"""
        content = self.system_prompt[:self.SYSTEM_PROMPT_MAX_CHARS]
        if len(self.system_prompt) > self.SYSTEM_PROMPT_MAX_CHARS:
            content += "\n\n... (截断)"
        return content


class DeclarationLoader:
    """统一声明加载器 — 从 .md 文件加载 Skill + Agent 声明。

    用法:
        loader = DeclarationLoader()
        skills = loader.load_skills()   # mode=inline
        agents = loader.load_agents()   # mode=delegated
        all_decls = loader.load_all()   # 全部
        decl = loader.get("weld_iqa")   # 按 name 查
    """

    YAML_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

    def __init__(
        self,
        skills_dir: Path = SKILLS_DIR,
        agents_dir: Path = AGENTS_DIR,
    ) -> None:
        self._skills_dir = skills_dir
        self._agents_dir = agents_dir
        self._cache: dict[str, Declaration] | None = None

    # ── 公共 API ──

    def load_all(self, force: bool = False) -> dict[str, Declaration]:
        """加载所有声明（skills + agents）。返回 {name: Declaration}。"""
        if self._cache is not None and not force:
            return self._cache

        self._cache = {}
        for md_dir in (self._skills_dir, self._agents_dir):
            if not md_dir.is_dir():
                continue
            for md_file in sorted(md_dir.glob("*.md")):
                try:
                    decl = self._parse_file(md_file)
                    if decl and decl.name:
                        self._cache[decl.name] = decl
                except Exception:
                    logger.debug("Failed to parse declaration: %s", md_file, exc_info=True)

        return self._cache

    def load_skills(self) -> dict[str, Declaration]:
        """加载 mode=inline 的声明（Skill）。"""
        return {k: v for k, v in self.load_all().items() if v.mode == "inline"}

    def load_agents(self) -> dict[str, Declaration]:
        """加载 mode=delegated 的声明（Agent）。"""
        return {k: v for k, v in self.load_all().items() if v.mode == "delegated"}

    def get(self, name: str) -> Declaration | None:
        """按 name 获取声明。"""
        return self.load_all().get(name)

    def get_skill(self, name: str) -> Declaration | None:
        decl = self.get(name)
        return decl if decl and decl.mode == "inline" else None

    def get_agent(self, name: str) -> Declaration | None:
        decl = self.get(name)
        return decl if decl and decl.mode == "delegated" else None

    # ── 解析 ──

    def _parse_file(self, path: Path) -> Declaration | None:
        content = path.read_text(encoding="utf-8")
        frontmatter, body = self._split_frontmatter(content)
        if frontmatter is None:
            return None

        name = frontmatter.get("name", path.stem)
        if not name:
            return None

        return Declaration(
            name=name,
            description=frontmatter.get("description", ""),
            mode=frontmatter.get("mode", "inline"),
            triggers=frontmatter.get("triggers", []),
            tools=frontmatter.get("tools", []),
            priority=frontmatter.get("priority", 0),
            max_iterations=frontmatter.get("max_iterations", 5),
            system_prompt=body.strip(),
            source=str(path),
        )

    def _split_frontmatter(self, content: str) -> tuple[dict | None, str]:
        """解析 YAML frontmatter。返回 (parsed_dict, body)。"""
        import yaml
        match = self.YAML_FRONTMATTER_PATTERN.match(content)
        if not match:
            return None, content
        try:
            parsed = yaml.safe_load(match.group(1))
            if not isinstance(parsed, dict):
                return None, content
            return parsed, content[match.end():]
        except Exception:
            return None, content