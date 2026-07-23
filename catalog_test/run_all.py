#!/usr/bin/env python3
"""一键运行全部清单测试 (L1 + L2) 并生成报告.

用法:
  # 仅 L1 (不需要系统运行, 快)
  python -m catalog_test.run_all --l1

  # L1 + L2 (需要 API+Temporal+worker 运行)
  WELDEVENT_RUN_E2E=1 python -m catalog_test.run_all --all

  # 并行 L1 (10 并发)
  python -m catalog_test.run_all --l1 --parallel 10
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, os.environ.get("WELDEVENT_ROOT", "/Users/liuyixuan/WeldEvent"))


def run_l1(parallel: int = 0) -> tuple[int, int, list[dict]]:
    """运行 L1 工具级测试, 返回 (passed, failed, errors)."""
    print("=" * 60)
    print("L1 工具级测试 (WorkflowControlTool + FakeConnector)")
    print("=" * 60)

    cmd = [
        sys.executable, "-m", "pytest",
        str(Path(__file__).parent / "layer1_tool"),
        "-v", "--tb=short", "--timeout=30",
    ]
    if parallel > 0:
        try:
            import pytest_xdist  # noqa: F401
            cmd.extend(["-n", str(parallel)])
        except ImportError:
            print("(pytest-xdist 未安装, 串行运行)")

    result = subprocess.run(cmd, capture_output=True, text=True)
    out = result.stdout
    # 只打印最后 2000 字符避免刷屏
    print(out[-2000:] if len(out) > 2000 else out)

    # 解析 pytest summary: "232 passed in 27.55s" 或 "1 failed, 231 passed"
    summary_line = ""
    for line in out.split("\n"):
        if "passed" in line or "failed" in line:
            summary_line = line

    m_passed = re.search(r'(\d+) passed', summary_line)
    m_failed = re.search(r'(\d+) failed', summary_line)
    passed = int(m_passed.group(1)) if m_passed else 0
    failed = int(m_failed.group(1)) if m_failed else 0

    # 提取失败项
    errors = []
    for line in out.split("\n"):
        if "FAILED" in line and "::" in line:
            parts = line.split("::")
            if len(parts) >= 2:
                test_id = parts[-1].split()[0]
                errors.append({"item_id": test_id, "action": "", "error": line.strip()})

    return passed, failed, errors


async def run_l2() -> list[dict]:
    """运行 L2 端到端测试, 返回场景结果列表."""
    print("\n" + "=" * 60)
    print("L2 端到端测试 (真实 HTTP 对话)")
    print("=" * 60)

    from catalog_test.layer2_e2e.client import WeldEventClient
    from catalog_test.layer2_e2e.recorder import DialogRecorder
    from catalog_test.layer2_e2e.scenarios import ALL_E2E_SCENARIOS

    async with WeldEventClient() as client:
        if not await client.health():
            print("API 不可达, 跳过 L2 测试")
            print("启动: cd /Users/liuyixuan/WeldEvent && python -m cognitiveplane.app")
            print("       python -m controlplane.worker")
            return []

        recorder = DialogRecorder()
        for scenario in ALL_E2E_SCENARIOS:
            print(f"\n--- {scenario.scenario_id} [{scenario.catalog_item}] {scenario.description} ---")
            try:
                ok = await asyncio.wait_for(
                    scenario.runner(client, recorder), timeout=120
                )
                status = "PASS" if ok else "FAIL"
            except asyncio.TimeoutError:
                status = "FAIL"
                if recorder._current:
                    recorder.finish_scenario("FAIL", "超时 (120s)")
                else:
                    recorder.start_scenario(scenario.scenario_id, scenario.catalog_item, scenario.description)
                    recorder.finish_scenario("FAIL", "超时 (120s)")
            except Exception as e:
                status = "ERROR"
                if recorder._current:
                    recorder.finish_scenario("ERROR", str(e))
                else:
                    recorder.start_scenario(scenario.scenario_id, scenario.catalog_item, scenario.description)
                    recorder.finish_scenario("ERROR", str(e))
            print(f"  -> {status}")

        report_dir = Path(__file__).parent / "reports"
        report_dir.mkdir(exist_ok=True)
        recorder.save_json(report_dir / "e2e_results.json")
        recorder.save_markdown(report_dir / "e2e_report.md")
        print(f"\nL2 报告: {report_dir}/e2e_report.md")
        return [r.to_dict() for r in recorder.results]


def main():
    parser = argparse.ArgumentParser(description="WeldEvent 清单测试运行器")
    parser.add_argument("--l1", action="store_true", help="运行 L1 工具级测试")
    parser.add_argument("--l2", action="store_true", help="运行 L2 端到端测试")
    parser.add_argument("--all", action="store_true", help="运行全部 (L1+L2)")
    parser.add_argument("--parallel", type=int, default=0, help="L1 并行度")
    args = parser.parse_args()

    if not (args.l1 or args.l2 or args.all):
        args.all = True

    start = time.time()
    l1_passed = l1_failed = 0
    l1_errors: list[dict] = []
    l2_results: list[dict] = []

    if args.l1 or args.all:
        l1_passed, l1_failed, l1_errors = run_l1(args.parallel)

    if args.l2 or args.all:
        if os.environ.get("WELDEVENT_RUN_E2E", "").strip() in ("1", "true", "True"):
            l2_results = asyncio.run(run_l2())
        else:
            print("\nL2 跳过 (设 WELDEVENT_RUN_E2E=1 启用)")

    from catalog_test.layer2_e2e.report import generate_combined_report
    report = generate_combined_report(l1_passed, l1_failed, l1_errors, l2_results)
    report_path = Path(__file__).parent / "reports" / "combined_report.md"
    report_path.parent.mkdir(exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"全部完成 ({elapsed:.1f}s)")
    print(f"报告: {report_path}")
    print(f"L1: {l1_passed} passed, {l1_failed} failed")
    if l2_results:
        l2p = sum(1 for r in l2_results if r.get("status") == "PASS")
        print(f"L2: {l2p}/{len(l2_results)} passed")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
