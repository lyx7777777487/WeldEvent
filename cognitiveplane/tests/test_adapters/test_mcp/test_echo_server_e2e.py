"""E2E test — echo_server → Registry → ToolRegistry → ReActEngine → mock LLM → tool_call → result.

Spec §6.1 C.4 — 验证整条 MCP 管道端到端跑通.

测试策略:
1. EchoToolCallMockProvider 强制第一轮返回 echo_tool 的 tool_call
2. ReActEngine 调 ToolRegistry 执行 echo_tool
3. MCPAdapter 委托 echo_server, 返回 {"echo": "hello from e2e"}
4. tool_result 注入下一轮 LLM 消息
5. 第二轮 mock LLM 读 tool 消息, 给最终答复, 验证它看到了 tool_result

关键修正 vs plan 草稿:
- MockLLMProvider.supports_function_calling 默认 False → 路由到 Tier-2 (structured
  output) 而非 Tier-1 ReAct. 必须覆写为 True 才能走 function calling 路径.
- LLMProvider.complete 签名是 (request: LLMRequest) -> LLMResponse, 无 **kwargs.
- ReActEngine.__init__ 已接受 tool_registry (第 2 位置参数), 无需 tool_registry_override.
- ReActEngine.run 要求 context: ContextSnapshot (非 None), 需构造真实快照.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from cognitiveplane.adapters.mcp.stubs.echo_server import create_echo_server
from cognitiveplane.adapters.mcp.tool_policy_classifier import (
    MCPToolPolicyClassifier,
)
from cognitiveplane.capability.mock import MockLLMProvider
from cognitiveplane.capability.provider import LLMRequest, LLMResponse
from cognitiveplane.control.deps import (
    CapabilityDeps,
    CognitiveDependencies,
    ControlDeps,
    GatewayDeps,
    GovernanceDeps,
    KnowledgeDeps,
    MemoryDeps,
)
from cognitiveplane.control.event_log import EventLog
from cognitiveplane.control.hooks import SafetyHook
from cognitiveplane.control.mcp_registry import MCPRegistry
from cognitiveplane.control.react import ReActEngine
from cognitiveplane.control.tool_registry import ToolRegistry
from cognitiveplane.shared.dto_context import ContextSnapshot
from cognitiveplane.shared.enums import EventType, NoveltyLevel
from cognitiveplane.shared.types import CaseId


class EchoToolCallMockProvider(MockLLMProvider):
    """Mock LLM — 第一轮强制 echo_tool 调用, 第二轮基于 tool_result 给最终答复."""

    def __init__(self) -> None:
        super().__init__()
        self._call_count = 0
        self._last_tool_result: dict[str, Any] | None = None

    @property
    def supports_function_calling(self) -> bool:
        # 必须覆写 — MockLLMProvider 默认 False 会路由到 Tier-2
        return True

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self._call_count += 1
        if self._call_count == 1:
            return LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "echo_tool",
                            "arguments": json.dumps({"text": "hello from e2e"}),
                        },
                    }
                ],
                model_used="mock-echo",
            )
        # 第二轮 — 看到 tool_result, 给最终答复
        tool_messages = [m for m in request.messages if m.get("role") == "tool"]
        if tool_messages:
            try:
                self._last_tool_result = json.loads(tool_messages[0].get("content", "{}"))
            except json.JSONDecodeError:
                self._last_tool_result = {"raw": tool_messages[0].get("content")}
        return LLMResponse(
            content=f"Echo result: {self._last_tool_result}",
            tool_calls=None,
            model_used="mock-echo",
        )


def _make_context() -> ContextSnapshot:
    return ContextSnapshot(
        case_id=CaseId(value="e2e-test"),
        event_type=EventType.WORKFLOW_ENTERED,
        workflow_state={},
        case_data={},
        measurements=[],
        memory_match_confidence=0.0,
        knowledge_coverage=0.0,
        event_novelty=NoveltyLevel.UNKNOWN,
        validation_critical_count=0,
        timestamp=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_echo_server_e2e_pipeline():
    """C.4 验收 — 整条管道端到端跑通."""
    llm = EchoToolCallMockProvider()

    classifier = MCPToolPolicyClassifier(yaml_path=None)
    event_log = EventLog(case_id=CaseId(value="e2e-test"))
    registry = MCPRegistry(classifier=classifier, event_log=event_log)
    echo_server = create_echo_server()
    await registry.register_server(echo_server)

    tool_registry = ToolRegistry()
    await registry.register_all(tool_registry)

    definitions = tool_registry.get_llm_tool_definitions()
    tool_names = [d["function"]["name"] for d in definitions]
    assert "echo_tool" in tool_names, f"echo_tool not in {tool_names}"

    deps = CognitiveDependencies(
        capability=CapabilityDeps(llm_provider=llm),
        control=ControlDeps(mcp_registry=registry),
        knowledge=KnowledgeDeps(),
        memory=MemoryDeps(),
        gateway=GatewayDeps(),
        governance=GovernanceDeps(),
    )
    engine = ReActEngine(
        deps,
        tool_registry=tool_registry,
        hooks=[SafetyHook()],
        event_log=event_log,
    )

    response = await engine.run(
        user_input="Please echo 'hello from e2e'",
        context=_make_context(),
        session={"session_id": "e2e-session"},
    )

    assert "hello from e2e" in response.text_reply, (
        f"Expected 'hello from e2e' in reply, got: {response.text_reply}"
    )
    assert "echo_tool" in response.tools_used, (
        f"Expected echo_tool in tools_used, got: {response.tools_used}"
    )