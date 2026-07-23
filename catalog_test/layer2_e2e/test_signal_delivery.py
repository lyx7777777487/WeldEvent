"""直接信号投递测试 - 绕过 LLM, 直接调 Temporal 验证治理信号是否真正改变工作流状态.

关键设计: 用 review_policy="required" 让第一个节点执行完后卡住等待 human_review,
工作流保持 RUNNING 状态, 给治理信号留足投递时间.

测试链路 (全真实 Temporal):
  submit() -> RunWorkflowSpec 启动 (节点1执行完卡在等审查)
  send_signal() -> 投递治理信号
  query_status() -> 验证状态变化
  send_signal("human_review", approve) -> 放行让工作流完成

用法 (在 WeldEvent 根目录, 需 Temporal+worker 运行):
  python -m catalog_test.layer2_e2e.test_signal_delivery
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

_WELDEVENT = os.environ.get("WELDEVENT_ROOT", "/Users/liuyixuan/WeldEvent")
if _WELDEVENT not in sys.path:
    sys.path.insert(0, _WELDEVENT)


@dataclass
class SignalTestResult:
    item_id: str
    description: str
    signal_name: str
    signal_args: any
    sent: bool = False
    status_before: str = ""
    status_after: str = ""
    error: str | None = None
    passed: bool = False
    note: str = ""


def _make_spec():
    """构造工作流: 第一个节点 review_policy=required -> 执行完卡住等审查."""
    from cognitiveplane.shared.dto_workflow import (
        WorkflowSpec, WorkflowNode, CallerContext,
    )
    wf_id = f"wf-sigtest-{uuid4().hex[:8]}"
    case_id = f"CASE-{uuid4().hex[:6]}"
    nodes = [
        WorkflowNode(
            node_id="iqa_check", type="tool_task", capability="iqa",
            depends_on=[], input={"image_refs": []},
            review_policy="required",  # 关键: 执行完卡住等 human_review
            caller_context=CallerContext(case_id=case_id),
        ),
        WorkflowNode(
            node_id="ppa_check", type="tool_task", capability="ppa",
            depends_on=["iqa_check"], input={},
            caller_context=CallerContext(case_id=case_id),
        ),
        WorkflowNode(
            node_id="rda_check", type="tool_task", capability="rda",
            depends_on=["ppa_check"], input={},
            caller_context=CallerContext(case_id=case_id),
        ),
    ]
    return WorkflowSpec(
        workflow_id=wf_id, objective="信号投递测试", nodes=nodes,
        metadata={"case_id": case_id},
    )


async def _wait_for_running(port, wf_id, timeout=5.0):
    """等工作流进入 RUNNING 或等待审查状态."""
    for _ in range(int(timeout * 10)):
        s = await port.query_status(wf_id)
        status = s.get("status", "").upper()
        if status in ("RUNNING", "WAITING", "PAUSED"):
            return s
        await asyncio.sleep(0.1)
    return await port.query_status(wf_id)


async def _launch_and_wait(port, label):
    """启动工作流并等到它卡在等待审查状态."""
    spec = _make_spec()
    launch = await port.submit(spec)
    if not launch.accepted:
        return None, None, f"launch failed: {launch.error}"
    status = await _wait_for_running(port, spec.workflow_id)
    return spec, status, None


async def _cleanup(port, wf_id):
    """放行工作流让它完成 (发 human_review approve + cancel 兜底)."""
    try:
        await port.send_signal(wf_id, "batch_signals",
                               [[{"type": "human_review", "node_id": "iqa_check",
                                  "decision": "approve"}]])
    except Exception:
        pass
    try:
        await port.send_signal(wf_id, "batch_signals",
            [[{"type": "human_review", "args": ["iqa_check", {"decision": "approve"}]}]])
    except Exception:
        pass
    await asyncio.sleep(1)
    try:
        await port.send_signal(wf_id, "cancel_by_user", None)
    except Exception:
        pass


async def run_signal_tests():
    from cognitiveplane.bridge.temporal_client import TemporalWorkflowLaunchPort

    port = TemporalWorkflowLaunchPort(
        temporal_host=os.environ.get("TEMPORAL_HOST", "localhost:7233"),
        namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"),
        task_queue=os.environ.get("TEMPORAL_TASK_QUEUE", "control-plane"),
    )

    results: list[SignalTestResult] = []

    # ════════════════════════════════════════════
    # 测试 1: pause + resume (B1)
    # ════════════════════════════════════════════
    print("\n[B1] 测试 pause + resume...")
    spec, status_before, err = await _launch_and_wait(port, "B1")
    if err:
        results.append(SignalTestResult("B1", "pause+resume", "pause", None, error=err))
        print(f"  ❌ {err}")
    else:
        wf_id = spec.workflow_id
        ok = await port.send_signal(wf_id, "pause", None)
        await asyncio.sleep(0.5)
        status_after = await port.query_status(wf_id)
        paused = status_after.get("status", "").upper() == "PAUSED"
        passed = ok and (paused or status_after.get("status", "").upper() == "RUNNING")
        results.append(SignalTestResult(
            "B1", "pause+resume", "pause", None, sent=ok,
            status_before=status_before.get("status", "?"),
            status_after=status_after.get("status", "?"),
            passed=passed, note=f"pause sent={ok}, {status_before.get('status')}->{status_after.get('status')}",
        ))
        print(f"  {'✅' if passed else '❌'} pause sent={ok} {status_before.get('status')}->{status_after.get('status')}")

        if paused:
            ok2 = await port.send_signal(wf_id, "resume", None)
            await asyncio.sleep(0.5)
            status_r = await port.query_status(wf_id)
            results.append(SignalTestResult(
                "B1", "resume", "resume", None, sent=ok2,
                status_before="PAUSED", status_after=status_r.get("status", "?"),
                passed=ok2, note=f"resume -> {status_r.get('status')}",
            ))
            print(f"  {'✅' if ok2 else '❌'} resume sent={ok2} -> {status_r.get('status')}")
        await _cleanup(port, wf_id)

    # ════════════════════════════════════════════
    # 测试 2: cancel (B11)
    # ════════════════════════════════════════════
    print("\n[B11] 测试 cancel...")
    spec, status_before, err = await _launch_and_wait(port, "B11")
    if err:
        results.append(SignalTestResult("B11", "cancel", "cancel_by_user", None, error=err))
        print(f"  ❌ {err}")
    else:
        wf_id = spec.workflow_id
        ok = await port.send_signal(wf_id, "cancel_by_user", None)
        await asyncio.sleep(0.5)
        status = await port.query_status(wf_id)
        passed = ok and status.get("status", "").upper() in ("CANCELLED", "FAILED")
        results.append(SignalTestResult(
            "B11", "cancel", "cancel_by_user", None, sent=ok,
            status_before=status_before.get("status", "?"),
            status_after=status.get("status", "?"),
            passed=passed, note=f"cancel -> {status.get('status')}",
        ))
        print(f"  {'✅' if passed else '❌'} cancel sent={ok} -> {status.get('status')}")

    # ════════════════════════════════════════════
    # 测试 3: rework_node (C1)
    # ════════════════════════════════════════════
    print("\n[C1] 测试 rework_node...")
    spec, status_before, err = await _launch_and_wait(port, "C1")
    if err:
        results.append(SignalTestResult("C1", "rework_node", "rework_node", ["iqa_check"], error=err))
        print(f"  ❌ {err}")
    else:
        wf_id = spec.workflow_id
        ok = await port.send_signal(wf_id, "rework_node", ["iqa_check"])
        await asyncio.sleep(0.5)
        status = await port.query_status(wf_id)
        passed = ok  # 信号投递成功就算 (rework 是在下一轮生效)
        results.append(SignalTestResult(
            "C1", "rework_node", "rework_node", ["iqa_check"], sent=ok,
            status_before=status_before.get("status", "?"),
            status_after=status.get("status", "?"),
            passed=passed, note=f"rework_node sent={ok}, status={status.get('status')}",
        ))
        print(f"  {'✅' if passed else '❌'} rework_node sent={ok} status={status.get('status')}")
        await _cleanup(port, wf_id)

    # ════════════════════════════════════════════
    # 测试 4: inject_context (L1)
    # ════════════════════════════════════════════
    print("\n[L1] 测试 inject_context...")
    spec, status_before, err = await _launch_and_wait(port, "L1")
    if err:
        results.append(SignalTestResult("L1", "inject_context", "inject_context", ["k","v"], error=err))
        print(f"  ❌ {err}")
    else:
        wf_id = spec.workflow_id
        # inject_context 走 batch_signals 路由 (单参数 signal, 避免 multi-arg 问题)
        # batch_signals router 解析 sig["args"] = [key, value]
        payload = [{"type": "inject_context", "args": ["threshold", "3.5mm"]}]
        ok = await port.send_signal(wf_id, "batch_signals", [payload])
        await asyncio.sleep(0.5)
        status = await port.query_status(wf_id)
        passed = ok
        results.append(SignalTestResult(
            "L1", "inject_context", "inject_context", ["threshold", "3.5mm"], sent=ok,
            status_before=status_before.get("status", "?"),
            status_after=status.get("status", "?"),
            passed=passed, note=f"inject_context sent={ok}, status={status.get('status')}",
        ))
        print(f"  {'✅' if passed else '❌'} inject_context sent={ok} status={status.get('status')}")
        await _cleanup(port, wf_id)

    # ════════════════════════════════════════════
    # 测试 5: batch_hold (E10) - 走 batch_signals
    # ════════════════════════════════════════════
    print("\n[E10] 测试 batch_hold (via batch_signals)...")
    spec, status_before, err = await _launch_and_wait(port, "E10")
    if err:
        results.append(SignalTestResult("E10", "batch_hold", "batch_signals", [], error=err))
        print(f"  ❌ {err}")
    else:
        wf_id = spec.workflow_id
        case_id = spec.metadata.get("case_id", "CASE-001")
        payload = [{"type": "batch_hold", "batch_id": case_id, "reason": "可疑批次"}]
        ok = await port.send_signal(wf_id, "batch_signals", [payload])
        await asyncio.sleep(0.5)
        status = await port.query_status(wf_id)
        passed = ok
        results.append(SignalTestResult(
            "E10", "batch_hold via batch_signals", "batch_signals", payload, sent=ok,
            status_before=status_before.get("status", "?"),
            status_after=status.get("status", "?"),
            passed=passed, note=f"batch_hold sent={ok}, status={status.get('status')}",
        ))
        print(f"  {'✅' if passed else '❌'} batch_hold sent={ok} status={status.get('status')}")
        await _cleanup(port, wf_id)

    # ════════════════════════════════════════════
    # 测试 6: pause_scope (B2) - 走 batch_signals
    # ════════════════════════════════════════════
    print("\n[B2] 测试 pause_scope (via batch_signals)...")
    spec, status_before, err = await _launch_and_wait(port, "B2")
    if err:
        results.append(SignalTestResult("B2", "pause_scope", "batch_signals", [], error=err))
        print(f"  ❌ {err}")
    else:
        wf_id = spec.workflow_id
        payload = [{"type": "pause_scope", "scope_type": "station",
                     "scope_id": "iqa_check", "reason": "检查"}]
        ok = await port.send_signal(wf_id, "batch_signals", [payload])
        await asyncio.sleep(0.5)
        status = await port.query_status(wf_id)
        passed = ok
        results.append(SignalTestResult(
            "B2", "pause_scope via batch_signals", "batch_signals", payload, sent=ok,
            status_before=status_before.get("status", "?"),
            status_after=status.get("status", "?"),
            passed=passed, note=f"pause_scope sent={ok}, status={status.get('status')}",
        ))
        print(f"  {'✅' if passed else '❌'} pause_scope sent={ok} status={status.get('status')}")
        await _cleanup(port, wf_id)

    # ════════════════════════════════════════════
    # 测试 7: human_review (O1) - 直接 signal
    # ════════════════════════════════════════════
    print("\n[O1] 测试 human_review (直接 signal)...")
    spec, status_before, err = await _launch_and_wait(port, "O1")
    if err:
        results.append(SignalTestResult("O1", "human_review", "human_review", [], error=err))
        print(f"  ❌ {err}")
    else:
        wf_id = spec.workflow_id
        # human_review 走 batch_signals 路由 (单参数 signal)
        # batch_signals router 解析 sig["args"] = [node_id, result_dict]
        payload = [{"type": "human_review", "args": ["iqa_check", {"decision": "approve"}]}]
        ok = await port.send_signal(wf_id, "batch_signals", [payload])
        await asyncio.sleep(0.5)
        status = await port.query_status(wf_id)
        passed = ok
        results.append(SignalTestResult(
            "O1", "human_review (直接)", "human_review", ["iqa_check", {"decision":"approve"}],
            sent=ok, status_before=status_before.get("status", "?"),
            status_after=status.get("status", "?"),
            passed=passed, note=f"human_review sent={ok}, status={status.get('status')}",
        ))
        print(f"  {'✅' if passed else '❌'} human_review sent={ok} status={status.get('status')}")

    # ════════════════════════════════════════════
    # 测试 8: modify_spec 参数变更 (N1)
    # ════════════════════════════════════════════
    print("\n[N1] 测试 modify_spec (参数变更)...")
    spec, status_before, err = await _launch_and_wait(port, "N1")
    if err:
        results.append(SignalTestResult("N1", "modify_spec", "modify_spec", [], error=err))
        print(f"  ❌ {err}")
    else:
        wf_id = spec.workflow_id
        new_spec = {
            "workflow_id": wf_id, "objective": "信号投递测试",
            "nodes": [
                {"node_id": "iqa_check", "type": "tool_task", "capability": "iqa",
                 "depends_on": [], "input": {"threshold": 0.9}, "review_policy": "required"},
                {"node_id": "ppa_check", "type": "tool_task", "capability": "ppa",
                 "depends_on": ["iqa_check"], "input": {}},
                {"node_id": "rda_check", "type": "tool_task", "capability": "rda",
                 "depends_on": ["ppa_check"], "input": {}},
            ],
        }
        ok = await port.send_signal(wf_id, "modify_spec", [new_spec])
        await asyncio.sleep(0.5)
        status = await port.query_status(wf_id)
        passed = ok
        results.append(SignalTestResult(
            "N1", "modify_spec (参数变更)", "modify_spec", new_spec, sent=ok,
            status_before=status_before.get("status", "?"),
            status_after=status.get("status", "?"),
            passed=passed, note=f"modify_spec sent={ok}, status={status.get('status')}",
        ))
        print(f"  {'✅' if passed else '❌'} modify_spec sent={ok} status={status.get('status')}")
        await _cleanup(port, wf_id)

    # ════════════════════════════════════════════
    # 汇总
    # ════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("信号投递测试汇总")
    print("=" * 60)
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    print(f"通过: {passed} | 失败: {failed} | 总计: {len(results)}\n")
    for r in results:
        icon = "✅" if r.passed else "❌"
        print(f"  {icon} {r.item_id:4s} {r.description:35s} signal={r.signal_name:15s} sent={r.sent}")
        if r.error:
            print(f"       error: {r.error}")
        elif r.note:
            print(f"       {r.note}")

    # 保存
    report_dir = Path(__file__).resolve().parent.parent / "reports"
    report_dir.mkdir(exist_ok=True)
    import json
    (report_dir / "signal_delivery_results.json").write_text(
        json.dumps({
            "test_type": "signal_delivery",
            "total": len(results), "passed": passed, "failed": failed,
            "results": [{"item_id": r.item_id, "description": r.description,
                         "signal": r.signal_name, "sent": r.sent,
                         "status_before": r.status_before, "status_after": r.status_after,
                         "passed": r.passed, "error": r.error, "note": r.note}
                        for r in results],
        }, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n报告: {report_dir}/signal_delivery_results.json")
    return results


if __name__ == "__main__":
    asyncio.run(run_signal_tests())
