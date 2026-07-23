#!/usr/bin/env python3
"""WeldEvent Cognitive Plane — FastAPI composition root.

Usage:
    python -m cognitiveplane.app
    python -m cognitiveplane.app --port 8000

配置: 编辑 cognitiveplane/.env 文件设置 API Key
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

# 自动加载 .env 文件 (仓库根目录)
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file)
    except ImportError:
        # 手动解析 .env
        with open(_env_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if key and value and key not in os.environ:
                    os.environ[key] = value

from cognitiveplane.bootstrap import (
    bootstrap_llm,
    build_dependencies,
    build_tool_registry_for_mcp,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FastAPI Composition Root
# ---------------------------------------------------------------------------


def create_app():
    """Create FastAPI application with typed CognitiveDependencies."""
    from fastapi import FastAPI

    from cognitiveplane.interaction.api.chat import create_chat_router
    from cognitiveplane.interaction.api.notifications import create_notifications_router

    llm_available = bootstrap_llm()
    deps = build_dependencies(llm_available)

    # P2-3 fix: lifespan — shutdown 时关闭 Temporal Client 连接
    # MCP server lifespan 也合并进来 — streamable_http_app 的 task group
    # 必须在 lifespan 中初始化，否则 MCP 请求会 500
    # (RuntimeError: Task group is not initialized)
    from contextlib import asynccontextmanager

    # 提前构建 MCP app（需要在 lifespan 之前构建，才能合并其 lifespan）
    from cognitiveplane.control.registry.mcp_server import build_mcp_server_and_app
    _mcp_app, _mcp_instance = build_mcp_server_and_app(
        build_tool_registry_for_mcp(deps)
    )

    # C4 fix: 提前构建 chat router 以获取 session_manager，
    # 让 lifespan 闭包能管理其 TTL 清理 task 生命周期。
    chat_router, _session_manager = create_chat_router(deps)

    @asynccontextmanager
    async def lifespan(app):
        # 启动 tracing（Langfuse v3 — 无 key 时自动降级为 NoOpTracer）
        # 必须在 ReAct/LLM 装饰器被调用前初始化，否则 trace 不被发送。
        from cognitiveplane.adapters.observability.tracing import (
            setup_tracing,
            flush as _flush_tracing,
        )
        setup_tracing()
        # C4: 启动 session TTL 清理后台 task（防止内存泄漏）
        await _session_manager.start_cleanup_task()

        # boundary-pinning §3 第三件套 — 长路径 Temporal 观察者启动。
        # WorkflowObserver 订阅 WorkflowEventBus 的 publish hook，
        # 收到 workflow_completed/failed/paused 事件时通过 NotificationStore
        # 推送给前端（NotificationType.WORKFLOW_UPDATE）。
        # 设计原则：只观察不主动启动 ReAct，让用户/前端决定是否触发下一轮推理。
        # ReActEngine 从 WorkflowEventBus 读状态摘要注入 system prompt（见 react.py
        # _build_system_prompt 末尾的 _format_workflow_status_section）。
        from cognitiveplane.control.workflow_observer import WorkflowObserver
        from cognitiveplane.interaction.workflow_events import (
            get_workflow_event_bus,
        )
        _workflow_observer = WorkflowObserver(
            event_bus=get_workflow_event_bus(),
            notification_store=None,
        )
        await _workflow_observer.start()

        # 装配 MCPRegistry — 注册 Label Studio MCP server
        # 让 10 个标注工具对 L1 Brain LLM 可见（查询类直接调，写入类走
        # approval gate 拦截）。phase_override=3 让标注工具越过默认 phase=4 隐藏。
        # Label Studio server 不可达时不阻断 app 启动 — 降级为无标注工具运行。
        import logging as _app_logging
        _app_log = _app_logging.getLogger("app")
        try:
            from cognitiveplane.adapters.mcp.label_studio_server import (
                create_label_studio_mcp_server,
            )
            _ls_server = await create_label_studio_mcp_server()
            await chat_router.mcp_registry.register_server(_ls_server)
            await chat_router.mcp_registry.register_all(chat_router.tool_registry)
            # 调试：打印所有注册工具及可见性
            _tr = chat_router.tool_registry
            _app_log.info(
                "[APP] Label Studio MCP server registered — annotation tools "
                "exposed to LLM (query=Tier-A auto, write=approval gate)"
            )
            _app_log.info(
                "[DEBUG] ToolRegistry current_phase=%s total_tools=%d",
                _tr._current_phase, len(_tr._tools),
            )
            for _name, _tool in _tr._tools.items():
                _ph = getattr(_tool, "phase", 1)
                _vis = _tr.is_llm_visible(_name)
                _app_log.info(
                    "[DEBUG] tool=%s phase=%s visible=%s", _name, _ph, _vis,
                )
        except Exception as e:
            _app_log.warning(
                "[APP] Label Studio MCP server register failed: %s — "
                "annotation tools unavailable, app continues without them", e
            )

        # 启动 MCP app 的 lifespan（初始化 task group）
        # streamable_http_app 返回 Starlette，lifespan_context 在 router 上
        async with _mcp_app.router.lifespan_context(app):
            yield
            # shutdown: 停止 WorkflowObserver（卸载 publish hook）
            try:
                await _workflow_observer.stop()
            except Exception:
                logger.warning("workflow_observer stop failed", exc_info=True)
            # shutdown: 释放 Temporal gRPC 连接
            if deps.bridge and deps.bridge.event_connector:
                try:
                    await deps.bridge.event_connector.aclose()
                except Exception:
                    logger.warning("lifespan cleanup step failed", exc_info=True)
            # shutdown: flush Langfuse trace（确保已积累的 span 发送到 server）
            # 无 Langfuse key 时此调用为 no-op。
            _flush_tracing()
            # C4: 停止 session TTL 清理后台 task
            await _session_manager.stop_cleanup_task()

    app = FastAPI(title="WeldEvent Cognitive Plane", version="0.2.0", lifespan=lifespan)

    # CORS — P2-13 fix: 通配符 origin + credentials 会被浏览器拒绝
    # P3-7 fix: 不再静默使用硬编码默认值。未配置 CORS_ORIGINS 时回退到
    # localhost 开发域并打 WARNING 日志，提醒生产部署必须显式配置。
    from fastapi.middleware.cors import CORSMiddleware
    import logging as _logging
    _cors_logger = _logging.getLogger("cognitiveplane.app")
    cors_env = os.environ.get("CORS_ORIGINS", "").strip()
    if cors_env:
        cors_origins = [o.strip() for o in cors_env.split(",") if o.strip()]
    else:
        cors_origins = ["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:3000"]
        _cors_logger.warning(
            "CORS_ORIGINS env var not set — falling back to localhost dev origins. "
            "For production deployment, set CORS_ORIGINS to a comma-separated list "
            "of allowed origins (e.g. 'https://app.example.com'). Current fallback: %s",
            cors_origins,
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 静态文件 — Chat UI
    from fastapi.staticfiles import StaticFiles
    import pathlib
    static_dir = pathlib.Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/api/v1/health")
    async def health() -> dict:
        # P2-R3-5: 探测 Temporal 连通性
        temporal_ok = True
        if deps.bridge and deps.bridge.event_connector:
            try:
                temporal_ok = await deps.bridge.event_connector.is_healthy()
            except Exception:
                temporal_ok = False
        return {
            "status": "ok" if temporal_ok else "degraded",
            "version": "0.2.0",
            "temporal": "connected" if temporal_ok else "unreachable",
        }

    app.include_router(chat_router)
    app.include_router(create_notifications_router())

    # 评估报告路由 - 对话场景并行执行 + 结果展示
    from cognitiveplane.tests.dialogue_scenarios.eval_api import router as _eval_router
    app.include_router(_eval_router)

    @app.get("/eval")
    async def _eval_page():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/static/eval.html")

    # MCP server - 协议化工具层（2026 MCP 标准）
    # mcp_app 已在 lifespan 之前构建（_mcp_app），这里只负责挂载
    # task group 初始化在 lifespan 中完成（见上方 lifespan 函数）
    try:
        from cognitiveplane.control.registry.mcp_server import mount_mcp_server
        mount_mcp_server(app, _mcp_app)
    except Exception as e:
        import logging as _logging
        _logging.getLogger("app").warning("[APP] MCP server mount failed: %s", e)

    return app


app = create_app()

if __name__ == "__main__":
    import sys
    import uvicorn

    port = 8000
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    uvicorn.run(create_app(), host="0.0.0.0", port=port)
