"""Project document discovery — 借鉴 Codex agents_md.rs + Claude Code CLAUDE.md.

从 cwd 向上查找 .git / pyproject.toml 定位项目 root，收集路径上所有
WELDEVENT.md + WELDEVENT.local.md 文件，按预算截断后注入 system prompt。

与 WorldviewBuilder 的现有 _read_md_if_exists(WELDEVENT_MD_PATH) 互补：
  - 现有逻辑：只读 cwd 下的 WELDEVENT.md（相对路径）
  - 本模块：沿路径发现多层 WELDEVENT.md + path-scoped rules
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 预算上限（字节），参考 Codex project_doc_max_bytes
DISCOVERY_MAX_BYTES = 8192
# 每条 WELDEVENT.md 的单独上限（字节），防止单文件占满预算
SINGLE_FILE_MAX_BYTES = 4096
# 跳过这些目录名（不向上查找时忽略）
SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", ".tox"})


def _find_project_root(start: Path | None = None) -> Path | None:
    """从 start 向上查找项目 root。

    定位标识（按优先级）：
      1. .git 目录
      2. pyproject.toml 文件
      3. 触及文件系统根目录停止

    返回 None 如果找不到任何标识。
    """
    current = (start or Path.cwd()).resolve()
    while True:
        if (current / ".git").is_dir() or (current / "pyproject.toml").is_file():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _collect_path_docs(start: Path, root: Path) -> list[Path]:
    """从 start 向上到 root（含），收集路径上所有 WELDEVENT.md 文件。

    返回按路径深度排序的列表（最深层优先，与 LLM 阅读顺序一致）。
    """
    docs: list[Path] = []
    current = start.resolve()
    root = root.resolve()

    while True:
        weldevent_md = current / "WELDEVENT.md"
        if weldevent_md.is_file():
            docs.append(weldevent_md)
        weldevent_local = current / "WELDEVENT.local.md"
        if weldevent_local.is_file():
            docs.append(weldevent_local)

        if current == root:
            break
        parent = current.parent
        if parent == current or parent in [current] + [p.parent for p in [current]]:
            # 到达文件系统根
            break
        current = parent

    # 反转：最深层（cwd）优先，LLM 先看到最近的约定
    docs.reverse()
    return docs


def _collect_path_rules(start: Path, root: Path) -> list[Path]:
    """从 start 向上到 root（含），收集路径上 .weldevent/rules/*.md 文件。

    返回按路径深度排序的列表（最深层优先）。
    """
    rules: list[Path] = []
    current = start.resolve()
    root = root.resolve()

    while True:
        rules_dir = current / ".weldevent" / "rules"
        if rules_dir.is_dir():
            for md_file in sorted(rules_dir.glob("*.md")):
                if md_file.is_file():
                    rules.append(md_file)

        if current == root:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    rules.reverse()
    return rules


def _read_with_budget(paths: list[Path], max_bytes: int, single_max: int) -> str:
    """按 paths 顺序读取文件内容，累计不超过 max_bytes。

    每个文件单独不超过 single_max，超过则截断并标注。
    返回合并后的文本（空字符串如果无文件或全超预算）。
    """
    parts: list[str] = []
    remaining = max_bytes

    for p in paths:
        try:
            content = p.read_text(encoding="utf-8").strip()
        except Exception:
            logger.debug("project_doc read failed: %s", p, exc_info=True)
            continue

        if not content:
            continue

        if len(content) > single_max:
            content = content[:single_max] + f"\n\n... (截断，原文 {len(content)} 字节)"
        if len(content) > remaining:
            content = content[:remaining] + "\n\n... (超出总预算，后续文件已省略)"
            parts.append(content)
            break

        parts.append(content)
        remaining -= len(content)

    return "\n\n---\n\n".join(parts)


def discover_project_docs(cwd: Path | None = None) -> tuple[str | None, dict]:
    """发现并收集项目文档。

    Returns:
        (section_text, metadata)
        section_text: 合并后的文档内容（None 表示无文档）
        metadata: {"sources": [...], "total_bytes": int, "truncated": bool}
    """
    start = (cwd or Path.cwd()).resolve()
    root = _find_project_root(start)
    if root is None:
        return None, {"sources": [], "total_bytes": 0, "truncated": False}

    md_paths = _collect_path_docs(start, root)
    # path-scoped rules 暂时不注入（P2 增强项），只收集 WELDEVENT.md
    # rules_paths = _collect_path_rules(start, root)

    if not md_paths:
        return None, {"sources": [], "total_bytes": 0, "truncated": False}

    text = _read_with_budget(md_paths, DISCOVERY_MAX_BYTES, SINGLE_FILE_MAX_BYTES)
    if not text:
        return None, {"sources": [], "total_bytes": 0, "truncated": False}

    metadata = {
        "sources": [str(p.relative_to(root)) for p in md_paths],
        "total_bytes": len(text.encode("utf-8")),
        "truncated": len(text.encode("utf-8")) >= DISCOVERY_MAX_BYTES - 100,
    }
    return text, metadata