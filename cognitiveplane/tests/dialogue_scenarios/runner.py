"""并行场景执行器 + 报告生成器.

asyncio.gather 并发跑多个场景 (每场景独立 engine, 避免状态串),
DeepSeek 调用用 semaphore 限并发防 429, 跑完汇总成结构化报告 JSON.
前端 eval.html 拉 JSON 渲染成场景列表 + 每轮预期vs实际 + 通过/失败.

用法:
    from cognitiveplane.tests.dialogue_scenarios.runner import run_all_scenarios
    report = asyncio.run(run_all_scenarios())  # dict, 可 JSON 序列化
"""
from __future__ import annotations

import asyncio
import logging
import time
import traceback
from typing import Any

from cognitiveplane.tests.dialogue_scenarios.harness import build_engine, run_turn, TurnResult

logger = logging.getLogger("scenario_runner")

# 并发上限 - DeepSeek rate limit 保护 (太高会 429)
_MAX_CONCURRENCY = 3
_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
    return _semaphore


async def _run_scenario_with_semaphore(scenario_module) -> dict[str, Any]:
    """单场景执行, 用 semaphore 限并发."""
    name = getattr(scenario_module, "SCENARIO_NAME", scenario_module.__name__)
    async with _get_semaphore():
        start = time.time()
        try:
            result = await scenario_module.run()
            elapsed = time.time() - start
            result["elapsed_sec"] = round(elapsed, 1)
            return result
        except Exception as e:
            logger.exception("scenario %s crashed", name)
            return {
                "name": name,
                "total": 0, "passed": 0,
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[:2000],
                "elapsed_sec": round(time.time() - start, 1),
                "turns": [],
            }


async def run_all_scenarios(scenario_names: list[str] | None = None) -> dict[str, Any]:
    """并行跑所有 (或指定) 场景, 返回汇总报告.

    scenario_names: 指定场景模块名 (不含 scenario_ 前缀和 .py 后缀).
                    None = 跑全部已实现的场景.
    Returns: {
        "total_scenarios": int, "passed_scenarios": int, "total_turns": int,
        "passed_turns": int, "started_at": str, "duration_sec": float,
        "scenarios": [ {name, total, passed, turns: [...], elapsed_sec, error?} ]
    }
    """
    import importlib
    import pkgutil
    from cognitiveplane.tests import dialogue_scenarios

    # 发现所有 scenario_*.py 模块
    if scenario_names is None:
        scenario_names = []
        for _, modname, _ in pkgutil.iter_modules(dialogue_scenarios.__path__):
            if modname.startswith("scenario_") and modname != "scenario_base":
                scenario_names.append(modname)

    modules = []
    for name in scenario_names:
        try:
            mod = importlib.import_module(f"cognitiveplane.tests.dialogue_scenarios.{name}")
            if hasattr(mod, "run"):
                modules.append(mod)
        except Exception as e:
            logger.warning("import scenario %s failed: %s", name, e)

    if not modules:
        return {"error": "no scenarios found", "scenarios": []}

    print(f"[runner] 并发跑 {len(modules)} 个场景 (并发上限 {_MAX_CONCURRENCY})")
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    t0 = time.time()
    results = await asyncio.gather(*[_run_scenario_with_semaphore(m) for m in modules])
    duration = round(time.time() - t0, 1)

    total_scenarios = len(results)
    passed_scenarios = sum(1 for r in results if r.get("passed") == r.get("total") and r.get("total", 0) > 0 and not r.get("error"))
    total_turns = sum(r.get("total", 0) for r in results)
    passed_turns = sum(r.get("passed", 0) for r in results)

    return {
        "total_scenarios": total_scenarios,
        "passed_scenarios": passed_scenarios,
        "total_turns": total_turns,
        "passed_turns": passed_turns,
        "started_at": started_at,
        "duration_sec": duration,
        "concurrency": _MAX_CONCURRENCY,
        "scenarios": results,
    }


def report_to_markdown(report: dict) -> str:
    """报告转 markdown (供终端/文件查看)."""
    if report.get("error"):
        return f"# 评估报告\n\n❌ {report['error']}\n"
    lines = [
        "# WeldEvent 对话场景评估报告",
        "",
        f"- 时间: {report['started_at']}",
        f"- 耗时: {report['duration_sec']}s (并发 {report['concurrency']})",
        f"- 场景: {report['passed_scenarios']}/{report['total_scenarios']} 通过",
        f"- 轮次: {report['passed_turns']}/{report['total_turns']} 通过",
        "",
    ]
    for s in report["scenarios"]:
        status = "✅" if s.get("passed") == s.get("total") and not s.get("error") else "❌"
        lines.append(f"## {status} {s['name']}  ({s.get('passed',0)}/{s.get('total',0)}, {s.get('elapsed_sec',0)}s)")
        if s.get("error"):
            lines.append(f"\n💥 异常: {s['error']}\n")
            continue
        for i, t in enumerate(s.get("turns", []), 1):
            ts = "✅" if t.get("approved") else "❌"
            lines.append(f"\n[轮{i}] {ts} 用户: {t.get('user','')[:50]}")
            lines.append(f"  工具: {t.get('tools',[])}")
            if t.get("failures"):
                for f in t["failures"]:
                    lines.append(f"  ⚠️ {f}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys, json
    logging.basicConfig(level=logging.WARNING)
    report = asyncio.run(run_all_scenarios())
    if "--json" in sys.argv:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(report_to_markdown(report))
    sys.exit(0 if report.get("passed_scenarios", 0) == report.get("total_scenarios", 0) else 1)
