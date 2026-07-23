"""情况覆盖矩阵追踪器 - 评估尺子的可执行核心.

把 docs/superpowers/specs/2026-07-20-execution-situation-catalog.md 的 315 条情况
映射到 (tier, coverage, test) 三元组, 自动扫描测试文件发现 test_<ID>_* 命名的测试,
输出覆盖度报告. 每次提交更新, 让"情况覆盖几条"可视可追.

用法:
    python -m controlplane.tests.situation_coverage.coverage_tracker
    python -m controlplane.tests.situation_coverage.coverage_tracker --json
    python -m controlplane.tests.situation_coverage.coverage_tracker --check  # CI 门禁: tested 项必须真有测试
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ── 路径 ──────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[3]
_SITUATIONS_YAML = Path(__file__).parent / "situations.yaml"
# 扫描这几个平面的测试目录
_TEST_DIRS = [
    _ROOT / "controlplane" / "tests",
    _ROOT / "cognitiveplane" / "tests",
    _ROOT / "executionplane" / "tests",
]

_TEST_NAME_RE = re.compile(r"\bdef\s+(test_([A-Z]\d+)_[A-Za-z0-9_\u4e00-\u9fff]*)\s*\(")


@dataclass
class Situation:
    id: str
    desc: str
    tier: str
    coverage: str  # tested | covered | pending
    declared_test: str | None = None


@dataclass
class CoverageReport:
    total: int = 0
    tested: int = 0
    covered: int = 0
    pending: int = 0
    by_tier: dict[str, dict[str, int]] = field(default_factory=dict)
    # tested 项声明了 test 但扫描没找到 -> CI 门禁失败
    missing_tests: list[str] = field(default_factory=list)
    # 测试文件里有 test_<ID>_* 但 YAML 没标 tested -> 待更新 YAML
    undeclared_tests: list[str] = field(default_factory=list)


def load_situations() -> list[Situation]:
    data = yaml.safe_load(_SITUATIONS_YAML.read_text(encoding="utf-8"))
    return [Situation(
        id=s["id"], desc=s["desc"], tier=s["tier"],
        coverage=s["coverage"], declared_test=s.get("test"),
    ) for s in data]


_ALL_TEST_RE = re.compile(r"\bdef\s+(test_[A-Za-z0-9_\u4e00-\u9fff]*)\s*\(")


def scan_tests() -> tuple[dict[str, list[str]], set[str]]:
    """扫描测试文件.

    返回 (by_situation_id, all_test_names):
      by_situation_id: {situation_id: [test_function_name, ...]} 仅 test_<ID>_* 命名
      all_test_names: 所有 def test_* 函数名集合 (用于核对 declared_test)
    """
    by_sid: dict[str, list[str]] = {}
    all_names: set[str] = set()
    for d in _TEST_DIRS:
        if not d.exists():
            continue
        for f in d.rglob("test_*.py"):
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for m in _TEST_NAME_RE.finditer(text):
                fn_name, sid = m.group(1), m.group(2)
                by_sid.setdefault(sid, []).append(fn_name)
            for m in _ALL_TEST_RE.finditer(text):
                all_names.add(m.group(1))
    return by_sid, all_names


def build_report(situations: list[Situation], found_tests: dict[str, list[str]],
                    all_test_names: set[str]) -> CoverageReport:
    rep = CoverageReport(total=len(situations))
    for s in situations:
        rep.by_tier.setdefault(s.tier, {"tested": 0, "covered": 0, "pending": 0, "total": 0})
        rep.by_tier[s.tier]["total"] += 1
        rep.by_tier[s.tier][s.coverage] += 1
        if s.coverage == "tested":
            rep.tested += 1
            # 门禁: tested 项必须有对应测试.
            # 先看 declared_test (YAML 里 test: 字段, 含非 test_<ID>_ 命名的),
            # 再看扫描是否发现 test_<ID>_* 命名的.
            declared = s.declared_test
            found_for_id = found_tests.get(s.id, [])
            if declared and declared in all_test_names:
                pass  # 显式声明的测试存在
            elif found_for_id:
                pass  # 按 test_<ID>_ 命名发现了
            else:
                rep.missing_tests.append(s.id)
        elif s.coverage == "covered":
            rep.covered += 1
        else:
            rep.pending += 1
    # 反向: 测试文件里有 test_<ID>_* 但 YAML 没标 tested
    tested_ids = {s.id for s in situations if s.coverage == "tested"}
    for sid in found_tests:
        if sid not in tested_ids:
            rep.undeclared_tests.append(sid)
    return rep


def format_report(situations: list[Situation], found_tests: dict[str, list[str]], rep: CoverageReport, all_test_names: set[str] | None = None) -> str:
    pct = lambda n: f"{n/rep.total*100:5.1f}%" if rep.total else "  0.0%"
    lines = [
        "=" * 60,
        "WeldEvent 情况覆盖矩阵报告",
        f"源: {_SITUATIONS_YAML.relative_to(_ROOT)}",
        "=" * 60,
        "",
        f"总计: {rep.total} 条情况",
        f"  tested  (有测试断言迁移/不变量): {rep.tested:3d}  ({pct(rep.tested)})",
        f"  covered (代码有机制无直接测试):  {rep.covered:3d}  ({pct(rep.covered)})",
        f"  pending (待补充确认/待实现):     {rep.pending:3d}  ({pct(rep.pending)})",
        "",
        "按评估层 (tier) 分布:",
    ]
    for tier in ["L0", "L1", "L2", "L3"]:
        t = rep.by_tier.get(tier, {"tested": 0, "covered": 0, "pending": 0, "total": 0})
        lines.append(f"  {tier}: tested={t['tested']:2d}/{t['total']:<3d} covered={t['covered']:<3d} pending={t['pending']:<3d}")
    lines.append("")
    if rep.missing_tests:
        lines.append(f"⚠️  CI 门禁失败: {len(rep.missing_tests)} 个 tested 项找不到对应测试:")
        for sid in rep.missing_tests:
            lines.append(f"    - {sid}")
    else:
        lines.append("✅ CI 门禁通过: 所有 tested 项都有对应测试")
    if rep.undeclared_tests:
        lines.append("")
        lines.append(f"ℹ️  {len(rep.undeclared_tests)} 个测试在文件里但 YAML 未标 tested (待同步):")
        for sid in rep.undeclared_tests:
            tests = found_tests.get(sid, [])
            lines.append(f"    - {sid}: {', '.join(tests)}")
    lines.append("")
    lines.append("已测试情况明细:")
    for s in situations:
        if s.coverage == "tested":
            tests = found_tests.get(s.id, [])
            if s.declared_test and (not tests or s.declared_test not in tests):
                tests = [s.declared_test] + tests
            t = ", ".join(tests) if tests else "(未找到)"
            lines.append(f"  {s.id} {s.desc}: {t}")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def main() -> int:
    situations = load_situations()
    found, all_test_names = scan_tests()
    rep = build_report(situations, found, all_test_names)
    if "--json" in sys.argv:
        import json
        print(json.dumps({
            "total": rep.total, "tested": rep.tested,
            "covered": rep.covered, "pending": rep.pending,
            "by_tier": rep.by_tier,
            "missing_tests": rep.missing_tests,
            "undeclared_tests": rep.undeclared_tests,
        }, ensure_ascii=False, indent=2))
        return 0
    print(format_report(situations, found, rep, all_test_names))
    # CI 门禁: --check 时 tested 项缺测试就失败
    if "--check" in sys.argv and rep.missing_tests:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
