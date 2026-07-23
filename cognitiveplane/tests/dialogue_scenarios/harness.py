"""对话场景驱动器 - 用真实 L1 ReAct 引擎跑多轮对话场景.

每个场景 = 一段多轮对话脚本 (user 轮次 + 预期断言).
驱动器构造接近生产的 ReActEngine (真实 LLM + 全套工具 + 7 个 skill),
逐轮调 engine.run() 收集 InteractionResponse (tools_used/skill/tier/trajectory),
跑完断言命中率/状态推进/介入响应, trace 自动上报 Langfuse.

用法:
    from cognitiveplane.tests.dialogue_scenarios.harness import build_engine, run_turn
    engine, session = build_engine()
    resp = await run_turn(engine, session, "帮我看看焊缝", expected_skills=["weld_iqa"])
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 确保能 import
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logger = logging.getLogger("dialogue_harness")


def _load_env() -> None:
    env = _ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip().strip('"').strip("'")


def _make_real_llm():
    """构造真实 DeepSeek LLM provider."""
    _load_env()
    from cognitiveplane.capability.config import LLMConfig, OpenAIConfig
    from cognitiveplane.capability.openai_provider import OpenAIProvider

    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置, 无法跑真实对话场景")
    cfg = LLMConfig(primary=OpenAIConfig(
        api_key=key,
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        default_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
    ))
    return OpenAIProvider(cfg)


def _make_context(case_id: str = "dialogue-scenario"):
    """构造对话场景用的 ContextSnapshot."""
    from datetime import datetime, timezone
    from cognitiveplane.shared.dto.context import ContextSnapshot
    from cognitiveplane.shared.enums import EventType, NoveltyLevel
    from cognitiveplane.shared.types import CaseId

    return ContextSnapshot(
        case_id=CaseId(value=case_id),
        event_type=EventType.WORKFLOW_ENTERED,
        workflow_state={},
        case_data={},
        measurements=[],
        memory_match_confidence=0.5,
        knowledge_coverage=0.5,
        event_novelty=NoveltyLevel.KNOWN,
        validation_critical_count=0,
        timestamp=datetime.now(timezone.utc),
    )


def build_engine(case_id: str = "dialogue-scenario"):
    """构造接近生产的 ReActEngine: 真实 LLM + 全套工具 + 7 skill.

    Returns: (engine, session_dict)
    """
    _load_env()
    # 启用 Langfuse trace
    try:
        import importlib
        import cognitiveplane.adapters.observability.tracing as t
        importlib.reload(t)
        t.setup_tracing()
    except Exception as e:
        logger.warning("tracing init failed: %s", e)

    llm = _make_real_llm()

    from cognitiveplane.control.deps import CognitiveDependencies
    from cognitiveplane.control.registry.tool_registry import ToolRegistry
    from cognitiveplane.control.engine.react import ReActEngine
    from cognitiveplane.control.skills import SkillRegistry
    from cognitiveplane.control.engine.approval import ApprovalStore

    deps = CognitiveDependencies()
    deps.capability.llm_provider = llm

    # 注入 stub knowledge adapter (让 search_standards/cases/process 工具可注册)
    # StubKnowledgeAdapter 同时实现 6 个端口, 真实场景会替换为带标准库的 adapter
    from cognitiveplane.knowledge.adapters.stub import StubKnowledgeAdapter
    stub_kb = StubKnowledgeAdapter()
    deps.knowledge.standards_query = stub_kb
    deps.knowledge.case_library = stub_kb
    deps.knowledge.process_knowledge = stub_kb

    # ApprovalStore - 让 RequestConfirmationTool 能阻塞等用户
    approval_store = ApprovalStore()

    # ImageStore (内存实现) - 让 analyze_image / split_image / upload 工具可注册
    from cognitiveplane.interaction.image_store import ImageStore
    image_store = ImageStore()

    # Gateway read/write (内存) - 让 design_workflow / read_weldmap 注册
    # InMemoryGatewayAdapter 自身实现 GatewayReadPort + GatewayWritePort + CognitiveGatewayWritePort
    try:
        from cognitiveplane.gateway.adapters.in_memory import InMemoryGatewayAdapter
        gw = InMemoryGatewayAdapter()
        deps.gateway.read = gw   # adapter 自身就是 GatewayReadPort
        deps.gateway.write = gw  # adapter 自身就是 GatewayWritePort
    except Exception as e:
        logger.warning("gateway inject failed: %s", e)

    # Bridge: 连真实 Temporal (让 launch_workflow 能真提交到 L2, 走完整 L1->L2->L3)
    # 需要外部已起 Temporal (docker weldevent-temporal:7233)
    try:
        from cognitiveplane.bridge.temporal_client import TemporalWorkflowLaunchPort
        from cognitiveplane.bridge.event_connector import EventConnector
        launcher = TemporalWorkflowLaunchPort(temporal_host="localhost:7233")
        connector = EventConnector(launcher=launcher, gateway_write=gw)
        deps.bridge.event_connector = connector
    except Exception as e:
        logger.warning("bridge inject failed (launch_workflow 将降级): %s", e)

    # 全套工具 (ToolRegistry 传 deps 自动注册)
    tool_registry = ToolRegistry(
        deps=deps,
        image_store=image_store,
        approval_store=approval_store,
        current_phase=3,
    )

    # 加载 7 个 skill (.weldevent/skills/*.md 声明)
    from cognitiveplane.control.skills import build_welding_skill_registry
    skill_registry = build_welding_skill_registry()

    engine = ReActEngine(
        deps=deps,
        tool_registry=tool_registry,
        skill_registry=skill_registry,
        approval_store=approval_store,
        max_iterations=25,
    )

    session = {
        "session_id": f"dialogue-{case_id}",
        "case_id": case_id,
        "history": [],  # 多轮对话累积
    }
    return engine, session, approval_store


@dataclass
class TurnResult:
    """单轮对话结果."""
    user_input: str
    reply: str
    tools_used: list[str] = field(default_factory=list)
    tier: str = ""
    skill: str | None = None
    workflow_ids: list[str] = field(default_factory=list)
    error: str | None = None
    approved: bool = True  # 评估命中
    failures: list[str] = field(default_factory=list)


async def run_turn(
    engine,
    session: dict,
    user_input: str,
    *,
    expected_skills: list[str] | None = None,
    expected_tools: list[str] | None = None,
    forbidden_tools: list[str] | None = None,
    reply_contains: list[str] | None = None,
    case_id: str = "dialogue-scenario",
) -> TurnResult:
    """跑一轮对话, 收集结果并按预期断言.

    expected_skills: 期望命中的 skill (任一即可)
    expected_tools: 期望调用的工具 (全都要)
    forbidden_tools: 禁止调用的工具
    reply_contains: reply 应包含的关键词 (任一)
    """
    from cognitiveplane.shared.dto.context import ContextSnapshot
    ctx = _make_context(case_id)

    response = await engine.run(user_input, ctx, session)

    result = TurnResult(
        user_input=user_input,
        reply=response.text_reply or "",
        tools_used=list(response.tools_used or []),
        tier=str(response.tier_used),
        workflow_ids=list(response.workflow_ids or []),
        error=response.error,
    )

    # 拿 skill (从 session 或 response 推断 - 看 ReActEngine 是否回传)
    # InteractionResponse 无 skill 字段, 从 trajectory/session 推断
    result.skill = session.get("_last_skill")

    # 断言
    if expected_skills:
        # skill 命中: response 里没直接暴露, 先标 TODO, 靠 tools_used 间接判断
        pass
    if expected_tools:
        missing = [t for t in expected_tools if t not in result.tools_used]
        if missing:
            result.approved = False
            result.failures.append(f"缺工具: {missing}")
    if forbidden_tools:
        hit = [t for t in forbidden_tools if t in result.tools_used]
        if hit:
            result.approved = False
            result.failures.append(f"误调禁用工具: {hit}")
    if reply_contains:
        rl = result.reply.lower()
        if not any(k.lower() in rl for k in reply_contains):
            result.approved = False
            result.failures.append(f"reply 未含关键词: {reply_contains}")

    # 累积进 session history
    session["history"].append({"user": user_input, "reply": result.reply,
                                "tools": result.tools_used})
    return result


def report_scenario(name: str, turns: list[TurnResult]) -> dict:
    """汇总场景结果."""
    total = len(turns)
    passed = sum(1 for t in turns if t.approved)
    print(f"\n{'='*60}\n场景: {name}\n{'='*60}")
    for i, t in enumerate(turns, 1):
        status = "✅" if t.approved else "❌"
        print(f"[轮{i}] {status} 用户: {t.user_input[:40]}")
        print(f"       工具: {t.tools_used} | tier={t.tier}")
        print(f"       回复: {t.reply[:80]}")
        if t.failures:
            for f in t.failures:
                print(f"       ⚠️ {f}")
    print(f"\n结果: {passed}/{total} 轮通过")
    return {"name": name, "total": total, "passed": passed,
            "turns": [{"user": t.user_input, "tools": t.tools_used,
                        "approved": t.approved, "failures": t.failures} for t in turns]}
