"""评估报告 API 端点 - 供前端 eval.html 调用.

挂到 FastAPI app, 提供:
  POST /api/eval/run   - 触发并行跑场景, 返回报告 JSON
  GET  /api/eval/report - 拿最近一次报告 (不重跑)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter

from cognitiveplane.tests.dialogue_scenarios.runner import run_all_scenarios

logger = logging.getLogger("eval_api")

router = APIRouter(prefix="/api/eval", tags=["eval"])

# 最近一次报告缓存 (避免每次拉都重跑)
_last_report: dict[str, Any] | None = None
_running: bool = False


@router.post("/run")
async def run_scenarios() -> dict[str, Any]:
    """触发并行跑所有场景, 返回报告."""
    global _last_report, _running
    if _running:
        return {"error": "评估正在运行中, 请稍后", "running": True}
    _running = True
    try:
        _last_report = await run_all_scenarios()
        return _last_report
    finally:
        _running = False


@router.get("/report")
async def get_report() -> dict[str, Any]:
    """拿最近一次报告 (不重跑)."""
    if _last_report is None:
        return {"error": "还没有跑过评估, 请先 POST /api/eval/run"}
    return _last_report


@router.get("/status")
async def status() -> dict[str, Any]:
    return {"running": _running, "has_report": _last_report is not None}
