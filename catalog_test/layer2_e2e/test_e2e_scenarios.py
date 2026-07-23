"""L2 端到端测试 - 真实 HTTP 对话测试清单功能.

前置条件 (全部满足才运行, 否则 skip):
  - API server 运行在 localhost:8000
  - Temporal 运行在 localhost:7233
  - L2 worker 运行
  - test.jpg 存在
  - LLM API key 有效

运行方式:
  WELDEVENT_RUN_E2E=1 python -m pytest catalog_test/layer2_e2e/ -v -s
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

_RUN_E2E = os.environ.get("WELDEVENT_RUN_E2E", "").strip() in ("1", "true", "True")
_SKIP_REASON = (
    "set WELDEVENT_RUN_E2E=1 启用端到端测试 "
    "(需要: API server :8000 + Temporal :7233 + worker + LLM key + test.jpg)"
)
pytestmark = pytest.mark.skipif(not _RUN_E2E, reason=_SKIP_REASON)

from catalog_test.layer2_e2e.client import WeldEventClient
from catalog_test.layer2_e2e.recorder import DialogRecorder
from catalog_test.layer2_e2e.scenarios import ALL_E2E_SCENARIOS


async def _check_system_ready() -> tuple[bool, str]:
    """检查系统是否就绪."""
    try:
        import httpx
    except ImportError:
        return False, "httpx 未安装"

    # 1. API server
    try:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as c:
            r = await c.get("http://127.0.0.1:8000/api/v1/health")
            if r.status_code != 200:
                return False, f"API 返回 {r.status_code}"
    except Exception as e:
        return False, f"API 不可达: {e}"

    # 2. test.jpg
    img = Path(os.environ.get(
        "WELDEVENT_TEST_IMAGE", "/Users/liuyixuan/WeldEvent/test.jpg"
    ))
    if not img.exists():
        return False, f"测试图片不存在: {img}"

    return True, "ok"


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def client_and_recorder():
    ready, msg = await _check_system_ready()
    if not ready:
        pytest.skip(f"系统未就绪: {msg}")

    async with WeldEventClient() as client:
        recorder = DialogRecorder()
        yield client, recorder

        # 保存报告
        report_dir = Path(__file__).parent.parent / "reports"
        report_dir.mkdir(exist_ok=True)
        recorder.save_json(report_dir / "e2e_results.json")
        recorder.save_markdown(report_dir / "e2e_report.md")


@pytest.mark.parametrize("scenario", ALL_E2E_SCENARIOS, ids=lambda s: s.scenario_id)
@pytest.mark.asyncio
async def test_e2e_scenario(scenario, client_and_recorder):
    """运行单个 E2E 场景."""
    client, recorder = client_and_recorder
    ok = await scenario.runner(client, recorder)
    assert ok, f"场景 {scenario.scenario_id} ({scenario.catalog_item}) 失败"


@pytest.mark.asyncio
async def test_e2e_summary(client_and_recorder):
    """汇总: 所有场景跑完后输出统计."""
    _, recorder = client_and_recorder
    # 这个测试总是 pass, 只是触发报告保存
    passed = sum(1 for r in recorder.results if r.status == "PASS")
    failed = sum(1 for r in recorder.results if r.status == "FAIL")
    total = len(recorder.results)
    print(f"\n{'='*60}")
    print(f"E2E 测试汇总: {passed}/{total} 通过, {failed} 失败")
    print(f"{'='*60}")
    assert True
