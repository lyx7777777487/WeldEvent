"""human_reviewer — 把 WorkflowTemplate dict 渲染成人能看懂的树，并接收反馈。

不调 LLM。CLI 阻塞 input()（用 asyncio.to_thread 包，不阻塞 event loop）。
"""

import asyncio
from typing import Any


def render(template: dict) -> str:
    """把 template 渲染成树形文本。"""
    lines = [f"工作流: {template.get('id')}@{template.get('version')}"]
    cps = {cp["id"]: cp for cp in template.get("control_points", [])}
    transitions = template.get("transitions", [])

    for i, cp_id in enumerate(_ordered_cp_ids(template), start=1):
        cp = cps[cp_id]
        activity = cp.get("activity_binding", {}).get("activity_name", "?")
        branches = _branches_from(transitions, cp_id)
        if branches:
            arrow = "  ".join(f"{c}→{t or '终'}" for c, t in branches)
            lines.append(f"  [{i}] {cp_id} ({activity})  → {arrow}")
        else:
            lines.append(f"  [{i}] {cp_id} ({activity})  → 完成")

    return "\n".join(lines)


async def ask_feedback(prompt: str = "满意? (y / 改进意见): ") -> str:
    """非阻塞读 user 输入。"""
    return await asyncio.to_thread(input, prompt)


def _ordered_cp_ids(template: dict) -> list[str]:
    """按 entry_point + transition 顺序排 CP。第一版单 CP 直接返回。"""
    entry = template.get("entry_point")
    cps = [cp["id"] for cp in template.get("control_points", [])]
    if entry in cps:
        cps.remove(entry)
        cps.insert(0, entry)
    return cps


def _branches_from(transitions: list[dict], from_cp: str) -> list[tuple[str, str | None]]:
    out = []
    for t in transitions:
        if t.get("from_cp") != from_cp:
            continue
        for b in t.get("branches", []):
            out.append((b.get("condition", "default"), b.get("to_cp")))
    return out