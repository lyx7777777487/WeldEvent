"""汇总报告生成器 - 合并 L1 + L2 结果."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from catalog_test.catalog_map import (
    ALL_ITEMS, TESTABLE_ITEMS, L1_ITEMS, L2_ITEMS, stats,
    BY_CATEGORY,
)


def generate_combined_report(l1_passed: int, l1_failed: int,
                              l1_errors: list[dict],
                              l2_results: list[dict] | None = None) -> str:
    """生成合并的 Markdown 报告."""
    lines = ["# WeldEvent 清单测试报告\n"]
    lines.append(f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    s = stats()
    lines.append("## 清单覆盖总览\n")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append(f"| 清单总条目 | {s['total']} |")
    lines.append(f"| 大类数 | {s['categories']} |")
    lines.append(f"| 可测条目 (✅+🆕) | {s['testable']} |")
    lines.append(f"| ❓ 未实现 | {s['by_coverage'].get('❓', 0)} |")
    lines.append(f"| 🆕 新增 | {s['by_coverage'].get('🆕', 0)} |")
    lines.append(f"| ✅ 已覆盖 | {s['by_coverage'].get('✅', 0)} |")
    lines.append("")

    # L1
    l1_total = l1_passed + l1_failed
    lines.append("## L1 工具级测试\n")
    lines.append(f"| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append(f"| L1 可测条目 | {len(L1_ITEMS)} |")
    lines.append(f"| 通过 | {l1_passed} |")
    lines.append(f"| 失败 | {l1_failed} |")
    lines.append(f"| 通过率 | {l1_passed}/{l1_total if l1_total else 1} |")
    lines.append("")

    if l1_errors:
        lines.append("### L1 失败详情\n")
        for e in l1_errors:
            lines.append(f"- **{e['item_id']}** ({e['action']}): {e['error']}")
        lines.append("")

    # L2
    if l2_results:
        l2_passed = sum(1 for r in l2_results if r.get("status") == "PASS")
        l2_failed = sum(1 for r in l2_results if r.get("status") == "FAIL")
        l2_skip = sum(1 for r in l2_results if r.get("status") in ("SKIP", "ERROR"))
        lines.append("## L2 端到端测试\n")
        lines.append(f"| 指标 | 数值 |")
        lines.append("|---|---|")
        lines.append(f"| L2 可测条目 | {len(L2_ITEMS)} |")
        lines.append(f"| E2E 场景 | {len(l2_results)} |")
        lines.append(f"| 通过 | {l2_passed} |")
        lines.append(f"| 失败 | {l2_failed} |")
        lines.append(f"| 跳过/错误 | {l2_skip} |")
        lines.append("")

        for r in l2_results:
            icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️", "ERROR": "💥"}.get(r.get("status"), "?")
            lines.append(f"- {icon} **{r.get('catalog_item', '?')}** - {r.get('description', '')}")
            if r.get("error"):
                lines.append(f"  - 错误: `{r['error']}`")
        lines.append("")

    # 按大类汇总
    lines.append("## 按大类汇总\n")
    lines.append("| 类 | 描述 | 总条目 | 可测 | L1 | L2 |")
    lines.append("|---|---|---|---|---|---|")
    cat_names = {
        "A": "节点级执行结果", "B": "节点暂停", "C": "回溯/回退",
        "D": "依赖与拓扑", "E": "数据与质量", "F": "并发与并行",
        "G": "时间与超时", "H": "外部依赖", "I": "状态与一致性",
        "J": "资源与成本", "K": "治理触发", "L": "上下文补充与版本",
        "M": "中断与节点重做", "N": "工作流更改", "O": "人工审查",
        "P": "工作流重新评估", "Q": "经验回流", "S": "跨工作流编排",
        "T": "安全合规与可观测", "U": "启动前边界态", "V": "交付后与终态",
    }
    for cat in sorted(BY_CATEGORY.keys()):
        items = BY_CATEGORY[cat]
        total = len(items)
        testable = sum(1 for i in items if i.testable)
        l1 = sum(1 for i in items if i.l1_action and i.testable)
        l2 = sum(1 for i in items if "L2" in i.layer and i.testable)
        name = cat_names.get(cat, cat)
        lines.append(f"| {cat} | {name} | {total} | {testable} | {l1} | {l2} |")
    lines.append("")

    # 未实现清单
    not_impl = [i for i in ALL_ITEMS if not i.testable]
    lines.append(f"## 未实现条目 (❓) - {len(not_impl)} 条\n")
    lines.append("这些条目在当前系统中尚未实现, 是后续开发重点:\n")
    by_cat: dict[str, list] = {}
    for i in not_impl:
        by_cat.setdefault(i.category, []).append(i)
    for cat in sorted(by_cat.keys()):
        ids = ", ".join(i.item_id for i in by_cat[cat])
        lines.append(f"- **{cat}**: {ids}")
    lines.append("")

    return "\n".join(lines)
