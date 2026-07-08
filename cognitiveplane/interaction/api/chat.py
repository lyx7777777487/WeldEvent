"""FastAPI chat router — POST /api/v1/chat and WebSocket /ws.

支持两种上传模式:
  - JSON: 纯文本聊天（无文件）
  - multipart/form-data: 带文件上传（工业场景大文件）

Plan §2.3 双轨:
  - 消息层: thumbnail data URL 直接进 LLM content (LLM 直接看图)
  - 工具层: LLM 调 analyze_image(image_ref=...) 拿原图 (只传引用)

Plan §A.3:
  - 上传图统一生成 Thumbnail 注入消息层 (成本闸门)
  - 原图保留在 ImageStore (内存，阶段 3+ 切 MinIO)

Endpoint handlers live in sibling modules (chat_handlers, stream_handlers,
approval_handlers, workflow_handlers, session_handlers). This module owns
the dependency-injection setup and thin ``@router`` registration wrappers.

Source: 7-plane redesign spec §7 FastAPI + plan §2.3 + §A.3.
"""

from fastapi import APIRouter, File, Form, UploadFile, WebSocket
from pydantic import BaseModel
from typing import Optional

from cognitiveplane.control.deps import CognitiveDependencies


class ChatRequest(BaseModel):
    """Incoming chat message (JSON mode — no files)."""
    message: str
    operator_id: str = "operator-001"
    case_id: str | None = None
    session_id: str | None = None


class ChatResponse(BaseModel):
    """Outgoing chat response."""
    reply: str
    session_id: str
    tools_used: list[str] = []
    tier: str = "react_function_calling"
    error: str | None = None
    # P3-5 fix: 结构化回传 launch_workflow 产出的 workflow_id，
    # 避免消费者从 LLM 回复文本正则提取（脆弱）。
    workflow_ids: list[str] = []


def create_chat_router(deps: CognitiveDependencies) -> tuple[APIRouter, "SessionManager"]:
    """Create FastAPI router for chat endpoints.

    Returns:
        (router, session_manager) — session_manager 由调用方在 app lifespan
        中管理 TTL 清理 task 的启动/停止（C4 fix）。
    """
    from cognitiveplane.control.react import ReActEngine, ApprovalStore
    from cognitiveplane.control.hooks import SafetyHook, PolicyHook
    from cognitiveplane.memory.compaction import ContextCompactor, CompactionStrategy
    from cognitiveplane.control.event_log import EventLog
    from cognitiveplane.control.skills import build_welding_skill_registry
    from cognitiveplane.governance.tool_policy import ToolPolicy
    from cognitiveplane.governance.guardrails import (
        WeldingAfterToolHook,
        WeldingOutputGuardrail,
    )
    from cognitiveplane.governance.evaluation import (
        build_evaluator,
    )
    from cognitiveplane.interaction.session import SessionManager
    from cognitiveplane.interaction.file_handler import FileHandler
    from cognitiveplane.interaction.image_store import ImageStore
    from cognitiveplane.interaction.api.image_session import ImageSessionRegistry
    from cognitiveplane.shared.types import CaseId

    router = APIRouter(prefix="/api/v1/chat", tags=["chat"])
    session_manager = SessionManager()
    tool_policy = ToolPolicy()
    hooks = [SafetyHook(), PolicyHook(tool_policy)]
    # Phase 4 Guardrails: 三层护栏中的 after_tool + output 层
    after_tool_hooks = [WeldingAfterToolHook()]
    output_guardrail = WeldingOutputGuardrail()
    # plan §A.3 + §7 contract: ImageStore is session-scoped state, NOT a Provider.
    # Constructed here and passed explicitly to ReActEngine → ToolRegistry → AnalyzeImageTool.
    image_store = ImageStore()
    image_sessions = ImageSessionRegistry(image_store)
    # plan §0.1 规则 1: 架构替 LLM 做的看不见的事 (worldview 注入) 必须记进 EventLog.
    # Phase 2 用 app 级共享 EventLog (case_id="app-shared"); Phase 3 AgentLoop 时改 per-session.
    event_log = EventLog(case_id=CaseId(value="app-shared"))
    # Phase 5 Agent Skills: 注入焊接领域预置 skill 注册表
    skill_registry = build_welding_skill_registry()
    # 架构级 approval gate — 进程级共享 ApprovalStore，供 ReAct loop 与
    # /chat/approve 端点共享 human-in-the-loop 状态
    approval_store = ApprovalStore()
    # Context Compaction — Anthropic Context Engineering 的 Compaction 技术。
    # 默认 SELF_COMPACT_SLIDING：LLM 自己总结旧历史（保留架构决策/未解决 bug/
    # 实现细节），保留最近 6 条原文。max_tokens=16000（DeepSeek 32K 的一半，
    # 留输出空间）。当 history 低于阈值时 compact() 直接返回原样，无额外开销。
    context_compactor = ContextCompactor(
        llm_provider=deps.capability.llm_provider,
        max_tokens=16000,
        default_strategy=CompactionStrategy.SELF_COMPACT_SLIDING,
    )
    # boundary-pinning §3 长路径 Temporal 观察者接入点 —
    # 把全局 WorkflowEventBus 单例注入 ReActEngine，让 _build_system_prompt
    # 能读到 workflow 状态摘要注入到下一轮 system prompt。
    from cognitiveplane.interaction.workflow_events import (
        get_workflow_event_bus,
    )
    workflow_event_bus = get_workflow_event_bus()
    engine = ReActEngine(
        deps, hooks=hooks, image_store=image_store, event_log=event_log,
        after_tool_hooks=after_tool_hooks,
        output_guardrail=output_guardrail,
        skill_registry=skill_registry,
        approval_store=approval_store,
        context_compactor=context_compactor,
        workflow_event_bus=workflow_event_bus,
    )
    # Phase 5 评估框架: LLM-as-a-Judge + Langfuse scoring
    # 在线评估（异步，不阻塞聊天返回）
    evaluator = build_evaluator(deps.capability.llm_provider, enabled=True)
    file_handler = FileHandler(llm_provider=deps.capability.llm_provider, image_store=image_store)

    # 暴露 tool_registry 给 app.py，用于挂载 MCP server（协议化工具层）
    # router.engine 也暴露，供 app.py 做健康检查等
    router.tool_registry = engine._tools
    router.engine = engine
    router.image_store = image_store
    router.image_sessions = image_sessions

    # MCPRegistry — 装配外部工业 MCP server（如 Label Studio 标注工具）
    # 在 app.py lifespan 中调 register_server + register_all 完成 async 注册
    from pathlib import Path as _Path
    from cognitiveplane.adapters.mcp.tool_policy_classifier import (
        MCPToolPolicyClassifier,
    )
    from cognitiveplane.control.registry.mcp_registry import MCPRegistry
    _yaml_path = (
        _Path(__file__).resolve().parent.parent.parent
        / "governance" / "mcp_policy.yaml"
    )
    _mcp_classifier = MCPToolPolicyClassifier(yaml_path=_yaml_path)
    router.mcp_registry = MCPRegistry(
        classifier=_mcp_classifier, event_log=event_log
    )

    # ── 加载 handler 模块（lazy import 避免循环依赖） ──
    from cognitiveplane.interaction.api.chat_handlers import (
        handle_chat, handle_chat_with_files,
    )
    from cognitiveplane.interaction.api.stream_handlers import (
        handle_chat_stream, handle_websocket_chat,
    )
    from cognitiveplane.interaction.api.approval_handlers import (
        CancelRequest, ApproveRequest,
        handle_cancel_workflow, handle_approve_tool,
    )
    from cognitiveplane.interaction.api.workflow_handlers import (
        WorkflowEventRequest, QueryWorkflowStatusRequest,
        handle_receive_workflow_event, handle_query_workflow_status,
        handle_workflow_stream,
    )
    from cognitiveplane.interaction.api.session_handlers import (
        handle_list_sessions, handle_export_session,
    )

    # ── JSON 纯文本聊天 ──

    @router.post("/", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        return await handle_chat(
            request,
            session_manager=session_manager,
            engine=engine,
            router=router,
            evaluator=evaluator,
        )

    # ── multipart/form-data 文件上传聊天 ──

    @router.post("/upload", response_model=ChatResponse)
    async def chat_with_files(
        message: str = Form(...),
        operator_id: str = Form("operator-001"),
        case_id: Optional[str] = Form(None),
        session_id: Optional[str] = Form(None),
        files: list[UploadFile] = File(default=[]),
    ) -> ChatResponse:
        return await handle_chat_with_files(
            message, operator_id, case_id, session_id, files,
            session_manager=session_manager,
            engine=engine,
            router=router,
            file_handler=file_handler,
            image_sessions=image_sessions,
            evaluator=evaluator,
        )

    # ── 流式聊天 (SSE) ──

    @router.post("/stream")
    async def chat_stream(request: ChatRequest):
        return await handle_chat_stream(
            request,
            session_manager=session_manager,
            engine=engine,
            router=router,
            evaluator=evaluator,
            deps=deps,
            hooks=hooks,
            image_store=image_store,
            event_log=event_log,
            after_tool_hooks=after_tool_hooks,
            output_guardrail=output_guardrail,
            skill_registry=skill_registry,
            approval_store=approval_store,
            context_compactor=context_compactor,
        )

    # ── WebSocket ── (Phase 3 子项目 D: AgentLoop 双 task)

    @router.websocket("/ws")
    async def websocket_chat(websocket: WebSocket) -> None:
        await handle_websocket_chat(
            websocket,
            deps=deps,
            hooks=hooks,
            skill_registry=skill_registry,
            image_store=image_store,
            after_tool_hooks=after_tool_hooks,
            output_guardrail=output_guardrail,
            engine=engine,
            session_manager=session_manager,
            image_sessions=image_sessions,
            router=router,
        )

    # ── 取消 workflow ──

    @router.post("/cancel")
    async def cancel_workflow(request: CancelRequest):
        return await handle_cancel_workflow(request, deps=deps)

    # ── Workflow 节点事件回传 ────────────────────────────────────────
    # L2 worker 执行 Temporal workflow 时,在节点 start/end/error 时调此端点
    # 推送事件到 L1。L1 收到后按 session_id 路由到 WorkflowEventBus,广播给
    # 前端 SSE 订阅者。这是"过程逐步展示"功能的 L2→L1 入口。

    @router.post("/workflow/events")
    async def receive_workflow_event(request: WorkflowEventRequest):
        return await handle_receive_workflow_event(
            request, workflow_event_bus=workflow_event_bus,
        )

    # ── Session 管理（调试用） ──

    @router.get("/sessions")
    async def list_sessions():
        """列出所有 session（调试用，导出对话历史）."""
        return await handle_list_sessions(session_manager=session_manager)

    @router.get("/sessions/{session_id}/export")
    async def export_session(session_id: str):
        """导出单个 session 的完整对话历史（调试用）."""
        return await handle_export_session(
            session_id, session_manager=session_manager,
        )

    # ── Workflow SSE 订阅 ──

    @router.get("/workflow/stream/{session_id}")
    async def workflow_stream(session_id: str):
        return await handle_workflow_stream(
            session_id, workflow_event_bus=workflow_event_bus,
        )

    # ── Workflow 状态查询 ──

    @router.post("/workflow/status")
    async def query_workflow_status(request: QueryWorkflowStatusRequest):
        return await handle_query_workflow_status(
            request, workflow_event_bus=workflow_event_bus,
        )

    # ── Human-in-the-loop 确认 ──

    @router.post("/approve")
    async def approve_tool(request: ApproveRequest):
        return await handle_approve_tool(
            request, approval_store=approval_store,
        )

    return router, session_manager
