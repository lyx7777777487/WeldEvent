"""端到端测试：L1 chat/upload → LLM design_workflow(image_refs) → launch_workflow
→ Temporal RunWorkflowSpec → L3 IqaActivity（真实 CV 规则 + 预处理）。

验证：
  1. L1 /api/v1/chat/upload 接收图片
  2. LLM 调 design_workflow，并把 image_refs 传进去
  3. LLM 调 launch_workflow，bridge 把 spec 提交到 Temporal
  4. Temporal RunWorkflowSpec 调 execute_node
  5. execute_node 调 L3 ActivityPool → IqaActivity 真实执行（不是 preprocessing_error）

P2-2 fix: 从脚本式改造为 pytest。
  - 默认 skip（需 WELDEVENT_RUN_E2E=1 显式启用），避免在普通测试运行时因缺环境而失败。
  - sys.exit(1) → pytest.fail()；print [PASS]/[FAIL] → assert。
  - 环境缺失（无 L1 服务 / 无 Temporal / 无测试图片）→ pytest.skip。
  - 逻辑错误（LLM 未调 launch / IQA 未真实执行）→ pytest.fail。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import pytest

# 测试图片
IMAGE_PATH = Path("/tmp/test_weld.jpg")
L1_BASE = "http://127.0.0.1:8000"
TEMPORAL_HOST = "localhost:7233"
WORKER_LOG = "/tmp/worker.log"

# 默认 skip：需 WELDEVENT_RUN_E2E=1 显式启用（需完整 L1+Temporal+worker 环境）
_RUN_E2E = os.environ.get("WELDEVENT_RUN_E2E", "").strip() in ("1", "true", "True")
_SKIP_REASON = (
    "set WELDEVENT_RUN_E2E=1 to run this end-to-end test "
    "(requires L1 service on :8000, Temporal on :7233, /tmp/test_weld.jpg)"
)
pytestmark = pytest.mark.skipif(not _RUN_E2E, reason=_SKIP_REASON)


def banner(msg: str) -> None:
    print(f"\n{'=' * 70}\n{msg}\n{'=' * 70}")


def step(n: int | str, msg: str) -> None:
    print(f"\n[Step {n}] {msg}")


async def _check_l1_reachable() -> bool:
    """探测 L1 服务是否可达（/api/v1/health）。"""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
            resp = await client.get(f"{L1_BASE}/api/v1/health")
            return resp.status_code == 200
    except Exception:
        return False


async def _check_temporal_reachable() -> bool:
    """探测 Temporal 是否可达。"""
    try:
        from temporalio.client import Client
        await asyncio.wait_for(
            Client.connect(TEMPORAL_HOST, namespace="default"),
            timeout=3.0,
        )
        return True
    except Exception:
        return False


async def upload_design_and_launch() -> dict:
    """Step 1+2+3: 单轮上传 + design + launch（ReAct 多轮工具调用）"""
    step(1, "上传图片 + 让 LLM 设计并启动工作流（单轮多工具调用）")

    import httpx
    if not IMAGE_PATH.exists():
        pytest.skip(f"测试图片不存在: {IMAGE_PATH}")

    with open(IMAGE_PATH, "rb") as f:
        files = [("files", ("test_weld.jpg", f.read(), "image/jpeg"))]

    # 单轮消息：要求 LLM 设计 + 立即启动（ReAct 会在同一 turn 内多轮工具调用）
    form_data = {
        "message": (
            "请基于上传的图片设计一个焊缝缺陷检测工作流，并立即启动它。"
            "重要：调用 design_workflow 时请把系统提示里的 image_refs 清单传进去，"
            "这样工作流执行时才能拿到图片。设计完直接调 launch_workflow 启动，"
            "无需等我确认。"
        ),
        "operator_id": "operator-001",
    }

    async with httpx.AsyncClient(timeout=180.0, trust_env=False) as client:
        resp = await client.post(
            f"{L1_BASE}/api/v1/chat/upload",
            data=form_data,
            files=files,
        )
    print(f"  HTTP status: {resp.status_code}")
    assert resp.status_code == 200, f"upload 返回非 200: {resp.status_code}, body={resp.text[:500]}"

    data = resp.json()
    print(f"  session_id: {data.get('session_id')}")
    print(f"  tools_used: {data.get('tools_used')}")
    print(f"  tier: {data.get('tier')}")
    if data.get("error"):
        print(f"  error: {data['error']}")
    print(f"  reply (前 1500 字):\n{data.get('reply', '')[:1500]}")
    return data


def find_workflow_id_in_reply(reply: str) -> str | None:
    """从 LLM 回复中提取 workflow_id（wf- 开头的十六进制串）。"""
    if not reply:
        return None
    # 匹配 wf-xxxxxxxx 或 wf-xxxxxxxx-xxxx 等
    matches = re.findall(r"\bwf-[a-f0-9]{4,}[a-f0-9\-]*\b", reply, re.IGNORECASE)
    if matches:
        return matches[0]
    # 也匹配引号里的 workflow_id
    m = re.search(r'workflow_id["\']?\s*[:=]\s*["\']?(wf-[a-f0-9\-]+)', reply, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


async def query_temporal_workflow(workflow_id: str) -> dict:
    """Step 5: 直接通过 temporalio client 查询 workflow 状态。"""
    step(5, f"查询 Temporal workflow: {workflow_id}")
    from temporalio.client import Client

    client = await Client.connect(TEMPORAL_HOST, namespace="default")
    handle = client.get_workflow_handle(workflow_id)

    # 先等 workflow 完成（最多 30 秒）
    print(f"  等待 workflow 完成（最多 30 秒）...")
    try:
        await asyncio.wait_for(handle.result(), timeout=30.0)
        print(f"  workflow 已完成")
    except asyncio.TimeoutError:
        print(f"  WARN: workflow 30 秒内未完成，尝试 query 当前状态")
    except Exception as e:
        print(f"  workflow.result() 异常: {type(e).__name__}: {e}")
        print(f"  尝试 query 当前状态...")

    # 查询 query_status
    try:
        status = await handle.query("query_status")
        if isinstance(status, dict):
            return status
        print(f"  query_status 返回非 dict: {type(status)}")
        return {"raw": str(status)}
    except Exception as e:
        print(f"  query_status 失败: {type(e).__name__}: {e}")
        # 尝试直接拿 describe
        try:
            desc = await handle.describe()
            return {"status": str(desc.status), "error": f"query failed: {e}"}
        except Exception as e2:
            return {"error": f"query={e}, describe={e2}"}


def print_workflow_result(wf_result: dict) -> None:
    """打印 workflow 查询结果详情。"""
    step(6, "Temporal workflow 结果详情")
    print(json.dumps(wf_result, indent=2, ensure_ascii=False, default=str)[:3000])


def print_worker_log_tail() -> None:
    """打印 worker.log 末尾（看 execute_node / L3 activity 调用）。"""
    step(7, "Worker log 末尾")
    try:
        with open(WORKER_LOG, "r") as f:
            lines = f.readlines()
        tail = lines[-40:] if len(lines) > 40 else lines
        print("".join(tail))
    except FileNotFoundError:
        print(f"  (worker log 不存在: {WORKER_LOG})")


@pytest.mark.asyncio
async def test_l1_l2_l3_end_to_end_flow() -> None:
    """端到端：L1 upload → LLM design+launch → Temporal → L3 IQA 真实执行."""
    banner("L1 -> LLM -> L2 Temporal -> L3 IQA 端到端测试")

    # 环境探测 — 任一不可达则 skip（非逻辑错误）
    if not await _check_l1_reachable():
        pytest.skip(f"L1 service not reachable at {L1_BASE}")
    if not await _check_temporal_reachable():
        pytest.skip(f"Temporal not reachable at {TEMPORAL_HOST}")

    # Step 1+2+3: 单轮上传 + 设计 + 启动
    resp = await upload_design_and_launch()
    session_id = resp.get("session_id")
    assert session_id, "响应缺少 session_id"

    tools_used = resp.get("tools_used", [])
    has_design = "design_workflow" in tools_used
    has_launch = "launch_workflow" in tools_used
    print(f"\n  design_workflow called: {has_design}")
    print(f"  launch_workflow called: {has_launch}")

    assert has_design, f"LLM 未调用 design_workflow (tools_used={tools_used})"
    assert has_launch, f"LLM 未调用 launch_workflow (tools_used={tools_used})"

    # Step 4: 提取 workflow_id
    workflow_id = find_workflow_id_in_reply(resp.get("reply", ""))
    step(4, "提取 workflow_id")
    assert workflow_id, (
        f"未能从 LLM 回复中提取 workflow_id\n"
        f"reply (前 800): {resp.get('reply', '')[:800]}\n"
        f"tools_used: {tools_used}"
    )
    print(f"  OK workflow_id: {workflow_id}")

    # Step 5+6: 查询 Temporal workflow
    wf_result = await query_temporal_workflow(workflow_id)

    # 打印详情
    print_workflow_result(wf_result)

    # Worker log
    print_worker_log_tail()

    # 总结
    banner("总结")
    result = wf_result
    if isinstance(result.get("result"), str):
        try:
            result["result"] = json.loads(result["result"])
        except Exception:
            pass
    inner = result.get("result") or result

    completed = inner.get("completed_nodes", [])
    failed = inner.get("failed_nodes", [])
    nrs = inner.get("node_results", {})

    # 判定 IQA 是否真实执行成功
    iqa_success = False
    iqa_detail = ""
    for node_id, nr_raw in nrs.items():
        nr = json.loads(nr_raw) if isinstance(nr_raw, str) else nr_raw
        data = nr.get("data", {}) if isinstance(nr, dict) else {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}
        # P2-2 fix: 明确断言 route_decision 存在且无 preprocessing_error
        if "route_decision" in data and "preprocessing_error" not in data:
            iqa_success = True
            iqa_detail = f"route={data['route_decision']} confidence={data.get('confidence')}"
            break

    print(f"  workflow_id: {workflow_id}")
    print(f"  workflow status: {inner.get('status') or wf_result.get('status')}")
    print(f"  completed_nodes: {completed}")
    print(f"  failed_nodes: {failed}")
    print(f"  tools_used: {tools_used}")
    print(f"  IQA 真实执行: {'OK ' + iqa_detail if iqa_success else 'FAIL'}")

    assert iqa_success, (
        f"IQA 未真实执行成功 — 端到端链路未打通\n"
        f"completed_nodes={completed} failed_nodes={failed}\n"
        f"node_results={json.dumps(nrs, ensure_ascii=False, default=str)[:1000]}"
    )
    print(f"\n  [PASS] 端到端链路打通: L1 upload → LLM design+launch → Temporal → L3 IQA")
