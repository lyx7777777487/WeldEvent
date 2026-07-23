"""对话记录器 - 记录用户与系统的完整交互过程.

记录: 对话内容 / 何时弹窗 / 用户选择 / 工具调用 / workflow 状态
输出: 结构化 JSON + 人类可读 Markdown
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from catalog_test.layer2_e2e.client import ChatTurn


@dataclass
class ScenarioResult:
    """单个测试场景的结果."""
    scenario_id: str
    catalog_item: str            # "B2", "C4" 等
    description: str
    status: str = "PENDING"     # PASS / FAIL / SKIP / ERROR
    turns: list[dict] = field(default_factory=list)  # 对话记录
    popups: list[dict] = field(default_factory=list)  # 弹窗记录
    user_choices: list[dict] = field(default_factory=list)  # 用户选择
    tools_used: list[str] = field(default_factory=list)
    workflow_ids: list[str] = field(default_factory=list)
    error: str | None = None
    duration_sec: float = 0.0
    started_at: float = 0.0

    def add_turn(self, turn: ChatTurn, is_user: bool = False) -> None:
        self.turns.append({
            "role": turn.role,
            "content": turn.content,
            "tools_used": turn.tools_used,
            "workflow_ids": turn.workflow_ids,
            "error": turn.error,
            "is_user": is_user,
            "timestamp": turn.timestamp,
        })
        for t in turn.tools_used:
            if t not in self.tools_used:
                self.tools_used.append(t)
        for w in turn.workflow_ids:
            if w not in self.workflow_ids:
                self.workflow_ids.append(w)

    def add_popup(self, approval_id: str, question: str,
                  options: list[str] | None = None) -> None:
        self.popups.append({
            "approval_id": approval_id,
            "question": question,
            "options": options or [],
            "timestamp": time.time(),
        })

    def add_choice(self, approval_id: str, decision: str,
                   feedback: str | None = None) -> None:
        self.user_choices.append({
            "approval_id": approval_id,
            "decision": decision,
            "feedback": feedback,
            "timestamp": time.time(),
        })

    def to_dict(self) -> dict:
        return asdict(self)


class DialogRecorder:
    """记录整个测试过程的对话."""

    def __init__(self) -> None:
        self.results: list[ScenarioResult] = []
        self._current: ScenarioResult | None = None

    def start_scenario(self, scenario_id: str, catalog_item: str,
                       description: str) -> ScenarioResult:
        self._current = ScenarioResult(
            scenario_id=scenario_id, catalog_item=catalog_item,
            description=description, started_at=time.time(),
        )
        return self._current

    @property
    def current(self) -> ScenarioResult:
        if self._current is None:
            raise RuntimeError("no active scenario")
        return self._current

    def finish_scenario(self, status: str, error: str | None = None) -> None:
        if self._current:
            self._current.status = status
            self._current.error = error
            self._current.duration_sec = time.time() - self._current.started_at
            self.results.append(self._current)
            self._current = None

    def save_json(self, path: str | Path) -> None:
        data = {
            "total_scenarios": len(self.results),
            "passed": sum(1 for r in self.results if r.status == "PASS"),
            "failed": sum(1 for r in self.results if r.status == "FAIL"),
            "skipped": sum(1 for r in self.results if r.status == "SKIP"),
            "errored": sum(1 for r in self.results if r.status == "ERROR"),
            "scenarios": [r.to_dict() for r in self.results],
        }
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def save_markdown(self, path: str | Path) -> None:
        """生成人类可读的 Markdown 报告."""
        lines = ["# WeldEvent 清单 E2E 测试报告\n"]
        lines.append(f"测试时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

        passed = sum(1 for r in self.results if r.status == "PASS")
        failed = sum(1 for r in self.results if r.status == "FAIL")
        skipped = sum(1 for r in self.results if r.status == "SKIP")
        total = len(self.results)

        lines.append(f"## 汇总\n")
        lines.append(f"| 指标 | 数量 |")
        lines.append(f"|---|---|")
        lines.append(f"| 总场景 | {total} |")
        lines.append(f"| 通过 | {passed} |")
        lines.append(f"| 失败 | {failed} |")
        lines.append(f"| 跳过 | {skipped} |")
        lines.append(f"| 通过率 | {passed}/{total if total else 1} |")
        lines.append("")

        for r in self.results:
            icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️", "ERROR": "💥"}.get(r.status, "?")
            lines.append(f"## {icon} {r.catalog_item} - {r.description}\n")
            lines.append(f"状态: **{r.status}** | 耗时: {r.duration_sec:.1f}s\n")
            if r.error:
                lines.append(f"错误: `{r.error}`\n")

            if r.turns:
                lines.append("### 对话记录\n")
                for t in r.turns:
                    role = "👤 用户" if t.get("is_user") else "🤖 系统"
                    lines.append(f"**{role}**: {t['content'][:500]}")
                    if t.get("tools_used"):
                        lines.append(f"  - 工具: {', '.join(t['tools_used'])}")
                    if t.get("workflow_ids"):
                        lines.append(f"  - 工作流: {', '.join(t['workflow_ids'])}")
                    if t.get("error"):
                        lines.append(f"  - ⚠️ 错误: {t['error']}")
                    lines.append("")

            if r.popups:
                lines.append("### 弹窗记录\n")
                for p in r.popups:
                    lines.append(f"- 弹窗 `{p['approval_id']}`: {p['question']}")
                    if p.get("options"):
                        lines.append(f"  - 选项: {', '.join(p['options'])}")
                lines.append("")

            if r.user_choices:
                lines.append("### 用户选择\n")
                for c in r.user_choices:
                    lines.append(f"- 选择 `{c['decision']}` (弹窗 {c['approval_id']})")
                    if c.get("feedback"):
                        lines.append(f"  - 反馈: {c['feedback']}")
                lines.append("")

            lines.append("---\n")

        Path(path).write_text("\n".join(lines), encoding="utf-8")
