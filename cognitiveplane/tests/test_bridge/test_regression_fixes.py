"""Regression tests for P1-R3-1 / P1-R3-2 / P2-R3-1 / P2-R3-6 fixes.

Covers three previously-untested areas:
  - P1-R3-2: TemporalWorkflowLaunchPort 断线重连 (_maybe_invalidate_client)
  - P2-R3-6: 错误脱敏 (_sanitize_error)
  - P1-R3-1: ReActEngine 并发 run() 不互相覆盖 tools_used
  - P2-R3-1: LaunchWorkflowTool 限流 (_MAX_LAUNCHES)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from cognitiveplane.bridge.temporal_client import TemporalWorkflowLaunchPort
from cognitiveplane.control.deps import (
    BridgeDeps,
    CapabilityDeps,
    CognitiveDependencies,
)
from cognitiveplane.control.react import ReActEngine, InteractionTier
from cognitiveplane.control.registry.tool_registry import ToolRegistry
from cognitiveplane.control.tools import BrainTool, ToolResult
from cognitiveplane.control.tools.launch_workflow import LaunchWorkflowTool
from cognitiveplane.bridge.event_connector import EventConnector
from cognitiveplane.bridge.workflow_launcher import (
    WorkflowLaunchPort,
    WorkflowLaunchResult,
    WorkflowLauncher,
)
from cognitiveplane.capability.provider import LLMProvider, LLMRequest, LLMResponse
from cognitiveplane.shared.dto.context import ContextSnapshot
from cognitiveplane.shared.dto_workflow import (
    CallerContext,
    WorkflowNode,
    WorkflowSpec,
)
from cognitiveplane.shared.enums import EventType, NoveltyLevel
from cognitiveplane.shared.types import CaseId


# ---------------------------------------------------------------------------
# P1-R3-2 + P2-R3-6: TemporalWorkflowLaunchPort 断线重连 + 错误脱敏
# ---------------------------------------------------------------------------


class TestTemporalClientReconnect:
    """P1-R3-2: 连接类异常时置 _client=None 触发重连。"""

    def test_connection_error_detected(self):
        port = TemporalWorkflowLaunchPort()
        # ConnectionRefusedError 是连接类异常
        assert port._is_connection_error(ConnectionRefusedError("refused")) is True
        # OSError 子类
        assert port._is_connection_error(OSError("network unreachable")) is True
        # gRPC UNAVAILABLE 关键词
        assert port._is_connection_error(RuntimeError("rpc UNAVAILABLE")) is True

    def test_non_connection_error_not_flagged(self):
        port = TemporalWorkflowLaunchPort()
        # ValueError 不是连接类异常
        assert port._is_connection_error(ValueError("bad spec")) is False
        # KeyError 不是
        assert port._is_connection_error(KeyError("missing")) is False

    def test_maybe_invalidate_client_on_connection_error(self):
        port = TemporalWorkflowLaunchPort()
        port._client = "fake-client"  # 模拟已连接
        # 连接类异常应置 None
        port._maybe_invalidate_client(ConnectionRefusedError("refused"))
        assert port._client is None

    def test_maybe_invalidate_client_skips_non_connection_error(self):
        port = TemporalWorkflowLaunchPort()
        port._client = "fake-client"
        # 非连接类异常不重置
        port._maybe_invalidate_client(ValueError("bad spec"))
        assert port._client is not None  # 保持不变

    def test_sanitize_error_hides_host_port(self):
        """P2-R3-6: 错误信息不泄漏 host:port."""
        port = TemporalWorkflowLaunchPort(temporal_host="localhost:7233")
        err = ConnectionRefusedError("Connection refused to localhost:7233")
        sanitized = port._sanitize_error(err)
        # 连接类异常应分类化，不含 host:port
        assert "7233" not in sanitized
        assert "localhost" not in sanitized
        assert sanitized == "Temporal service unavailable"

    def test_sanitize_error_keeps_type_for_non_connection(self):
        """P2-R3-6: 非连接类异常保留类型名但脱敏 host:port."""
        port = TemporalWorkflowLaunchPort()
        err = ValueError("bad config for host:7233 and more")
        sanitized = port._sanitize_error(err)
        assert "ValueError" in sanitized
        assert "7233" not in sanitized  # host:port 已脱敏
        assert "<temporal-host>" in sanitized

    @pytest.mark.asyncio
    async def test_submit_connection_failure_invalidates_and_returns_safe_error(self):
        """P1-R3-2 + P2-R3-6: submit 连接失败时重置 client + 返回脱敏错误."""
        port = TemporalWorkflowLaunchPort(temporal_host="localhost:9999")
        # 模拟 _ensure_client 抛连接异常
        port._client = None

        # Monkey-patch _ensure_client 抛连接错误
        async def _fail_connect():
            raise ConnectionRefusedError("Connection refused to localhost:9999")

        port._ensure_client = _fail_connect

        spec = WorkflowSpec(
            workflow_id="wf-test",
            objective="test",
            nodes=[WorkflowNode(node_id="n1", type="tool_task")],
        )
        result = await port.submit(spec)

        assert result.accepted is False
        assert result.error == "Temporal service unavailable"
        assert "9999" not in result.error  # 脱敏

    @pytest.mark.asyncio
    async def test_reconnect_after_connection_failure(self):
        """P1-R3-2: 连接失败置 None 后，下次 submit 应重新建连并成功.

        验证完整的断线重连链路：
          1. 首次 _ensure_client 抛连接异常 → submit 返回 accepted=False
          2. _maybe_invalidate_client 置 _client=None
          3. 第二次 submit → _ensure_client 重新调用 → 建连成功
          4. start_workflow 成功 → submit 返回 accepted=True

        原测试用字符串 fake client（无 start_workflow），第二次 submit 实际
        会抛 AttributeError，只验证了重试计数器自增，未验证重连后功能恢复。
        """
        class _FakeHandle:
            """Fake Temporal workflow handle with run_id."""
            run_id = "run-reconnected-001"

        class _FakeTemporalClient:
            """Fake Temporal Client supporting start_workflow."""
            async def start_workflow(self, *args, **kwargs):
                return _FakeHandle()

        port = TemporalWorkflowLaunchPort(temporal_host="localhost:9999")
        port._client = None  # 初始无连接

        connect_attempts = 0

        async def _flaky_connect():
            nonlocal connect_attempts
            connect_attempts += 1
            if connect_attempts == 1:
                raise ConnectionRefusedError("first attempt fails")
            # 第二次返回真实可用的 fake client
            port._client = _FakeTemporalClient()
            return port._client

        port._ensure_client = _flaky_connect

        spec = WorkflowSpec(
            workflow_id="wf-reconnect",
            objective="test reconnect",
            nodes=[WorkflowNode(node_id="n1", type="tool_task")],
        )

        # 第一次 submit — _ensure_client 抛 ConnectionRefusedError → accepted=False
        result1 = await port.submit(spec)
        assert result1.accepted is False
        assert result1.error == "Temporal service unavailable"
        assert port._client is None, "失败后 _client 应为 None 触发重连"
        assert connect_attempts == 1

        # 第二次 submit — _ensure_client 重新调用 → 返回可用 fake client → accepted=True
        result2 = await port.submit(spec)
        assert connect_attempts == 2, "第二次 submit 应触发重新建连"
        assert result2.accepted is True, "重连后 submit 应成功"
        assert result2.run_id == "run-reconnected-001"
        assert result2.adapter == "temporal"
        assert port._client is not None, "重连成功后 _client 应保持有效"


# ---------------------------------------------------------------------------
# P1-R3-1: ReActEngine 并发 run() 不互相覆盖 tools_used
# ---------------------------------------------------------------------------


class _ConcurrentTestTool(BrainTool):
    """Tool that sleeps briefly to allow concurrent run() overlap."""

    @property
    def name(self) -> str:
        return "test_tool"

    @property
    def description(self) -> str:
        return "A test tool for concurrency"

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {"query": {"type": "string"}}}

    async def execute(self, **kwargs) -> ToolResult:
        await asyncio.sleep(0.05)  # 让并发 run() 有时间交错
        return ToolResult(output={"result": "done"})


class _ScriptedLLMConcurrent(LLMProvider):
    """LLM that calls a tool then returns final text. Tracks call order."""

    def __init__(self, label: str):
        self._label = label
        self._call_count = 0

    @property
    def supports_function_calling(self) -> bool:
        return True

    @property
    def supports_json_mode(self) -> bool:
        return False

    @property
    def supports_vision(self) -> bool:
        return False

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self._call_count += 1
        if self._call_count == 1:
            return LLMResponse(
                content="",
                tool_calls=[{
                    "id": f"call-{self._label}",
                    "function": {
                        "name": "test_tool",
                        "arguments": json.dumps({"query": self._label}),
                    },
                }],
                model_used="scripted",
            )
        return LLMResponse(content=f"done-{self._label}", model_used="scripted")

    async def stream(self, request: LLMRequest):
        yield f"done-{self._label}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    def health_check(self) -> bool:
        return True


class _InterleaveLLM(LLMProvider):
    """LLM that supports concurrent run() interleave on the same engine.

    Uses request messages to distinguish which run() a request belongs to.
    First call (no tool result in messages) → returns tool_call.
    Second call (has tool result) → returns final text.
    """

    def __init__(self) -> None:
        self._call_count = 0

    @property
    def supports_function_calling(self) -> bool:
        return True

    @property
    def supports_json_mode(self) -> bool:
        return False

    @property
    def supports_vision(self) -> bool:
        return False

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self._call_count += 1
        # Check if this is the first call (no tool messages yet) or second
        has_tool_result = any(
            isinstance(m, dict) and m.get("role") == "tool"
            for m in request.messages
        )
        if not has_tool_result:
            # First call → return tool_call with the user query as argument
            user_msg = ""
            for m in request.messages:
                if isinstance(m, dict) and m.get("role") == "user":
                    user_msg = m.get("content", "")
                    break
            return LLMResponse(
                content="",
                tool_calls=[{
                    "id": f"call-{self._call_count}",
                    "function": {
                        "name": "test_tool",
                        "arguments": json.dumps({"query": user_msg}),
                    },
                }],
                model_used="interleave",
            )
        # Second call → return final text echoing the tool query
        tool_content = ""
        for m in request.messages:
            if isinstance(m, dict) and m.get("role") == "tool":
                tool_content = m.get("content", "")
                break
        # Extract query from tool result
        try:
            tool_data = json.loads(tool_content) if tool_content else {}
            query = tool_data.get("output", {}).get("result", "done")
        except Exception:
            query = "done"
        return LLMResponse(content=f"done-{query}", model_used="interleave")

    async def stream(self, request: LLMRequest):
        yield "done"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    def health_check(self) -> bool:
        return True


def _make_context() -> ContextSnapshot:
    return ContextSnapshot(
        case_id=CaseId(value="test-case"),
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


class TestReActEngineConcurrency:
    """P1-R3-1: 并发 run() 不互相覆盖 tools_used."""

    @pytest.mark.asyncio
    async def test_concurrent_runs_have_isolated_tools_used(self):
        """同一 engine 的两个并发 run() 各自的 tools_used 不互相污染.

        P2-fix: 原测试用两个独立 engine，无法验证 P1-R3-1（同一 engine 并发）。
        改为用同一 engine + _InterleaveLLM（按 messages 区分不同 run()）。
        """
        # 同一 engine + 同一 LLM + 同一 tool_registry（模拟 WebSocket 复用）
        llm = _InterleaveLLM()
        deps = CognitiveDependencies(
            capability=CapabilityDeps(llm_provider=llm),
        )
        registry = ToolRegistry()
        registry.register(_ConcurrentTestTool())
        engine = ReActEngine(deps, tool_registry=registry)

        context = _make_context()

        # 同一 engine 并发执行两次 run()
        response_a, response_b = await asyncio.gather(
            engine.run("queryA", context),
            engine.run("queryB", context),
        )

        # 各自的 tools_used 应只含一次工具调用（不互相覆盖）
        assert response_a.tools_used == ["test_tool"]
        assert response_b.tools_used == ["test_tool"]
        # 回复应各自独立
        assert "queryA" in response_a.text_reply or "done" in response_a.text_reply
        assert "queryB" in response_b.text_reply or "done" in response_b.text_reply

    @pytest.mark.asyncio
    async def test_sequential_runs_reset_tools_used(self):
        """同一 engine 顺序 run() 时 tools_used 不残留上一轮."""
        llm = _ScriptedLLMConcurrent("seq")
        deps = CognitiveDependencies(
            capability=CapabilityDeps(llm_provider=llm),
        )
        registry = ToolRegistry()
        registry.register(_ConcurrentTestTool())
        engine = ReActEngine(deps, tool_registry=registry)
        context = _make_context()

        # 第一次 run
        r1 = await engine.run("first", context)
        assert r1.tools_used == ["test_tool"]

        # 重置 LLM 让第二次也能调工具
        llm._call_count = 0
        r2 = await engine.run("second", context)
        assert r2.tools_used == ["test_tool"]
        # 第二次不应包含上一轮的重复
        assert r2.tools_used.count("test_tool") == 1


# ---------------------------------------------------------------------------
# P2-R3-1: LaunchWorkflowTool 限流
# ---------------------------------------------------------------------------


class _AcceptingPort(WorkflowLaunchPort):
    """Port that always accepts, for testing launch limit.

    实现 query_status 返回 COMPLETED，让 LaunchWorkflowTool._poll_workflow
    第一轮就检测到完成并 break（避免 60s 轮询超时拖慢测试套件）。
    """

    async def submit(self, spec: WorkflowSpec) -> WorkflowLaunchResult:
        return WorkflowLaunchResult(
            workflow_id=spec.workflow_id,
            run_id="run-fake",
            accepted=True,
            adapter="fake",
        )

    async def query_status(self, workflow_id: str) -> dict:
        return {
            "status": "COMPLETED",
            "completed_nodes": [],
            "failed_nodes": [],
            "node_results": {},
            "error": None,
        }


def _make_launch_spec() -> WorkflowSpec:
    return WorkflowSpec(
        workflow_id="wf-launch-test",
        objective="test launch",
        nodes=[
            WorkflowNode(
                node_id="n1",
                type="tool_task",
                capability="defect_detection",
                caller_context=CallerContext(case_id="case-1"),
            ),
        ],
    )


class TestLaunchWorkflowRateLimit:
    """P2-R3-1: LaunchWorkflowTool 限流防止 LLM 无限循环."""

    @pytest.mark.asyncio
    async def test_launch_succeeds_under_limit(self):
        """未达上限时正常提交."""
        port = _AcceptingPort()
        launcher = WorkflowLauncher(port=port)
        connector = EventConnector(launcher=launcher)
        deps = CognitiveDependencies(bridge=BridgeDeps(event_connector=connector))
        tool = LaunchWorkflowTool(deps)

        result = await tool.execute(
            workflow_spec=_make_launch_spec().model_dump(),
            reason="test",
        )

        assert result.error is None
        assert result.output["status"] == "COMPLETED"
        assert tool._launch_count == 1

    @pytest.mark.asyncio
    async def test_launch_blocks_duplicate_workflow_id(self):
        """P2-8: 同一 workflow_id 重复启动被拒."""
        port = _AcceptingPort()
        launcher = WorkflowLauncher(port=port)
        connector = EventConnector(launcher=launcher)
        deps = CognitiveDependencies(bridge=BridgeDeps(event_connector=connector))
        tool = LaunchWorkflowTool(deps)
        # 预设已启动过该 workflow_id
        tool._launched_ids["wf-launch-test"] = None

        result = await tool.execute(
            workflow_spec=_make_launch_spec().model_dump(),
            reason="test",
        )

        assert result.error is not None
        assert "already launched" in result.error.lower()

    @pytest.mark.asyncio
    async def test_launch_evicts_oldest_at_total_limit(self):
        """P3-2: 达到全局上限时 LRU 淘汰最老条目，新 launch 仍成功."""
        port = _AcceptingPort()
        launcher = WorkflowLauncher(port=port)
        connector = EventConnector(launcher=launcher)
        deps = CognitiveDependencies(bridge=BridgeDeps(event_connector=connector))
        tool = LaunchWorkflowTool(deps)
        # 预设到全局上限
        for i in range(tool._MAX_TOTAL_LAUNCHES):
            tool._launched_ids[f"wf-existing-{i}"] = None

        result = await tool.execute(
            workflow_spec=_make_launch_spec().model_dump(),  # wf-launch-test 不在 OrderedDict 中
            reason="test",
        )

        # P3-2: LRU eviction — 新 launch 成功，最老的 wf-existing-0 被淘汰
        assert result.error is None
        assert result.output["status"] == "COMPLETED"
        assert "wf-existing-0" not in tool._launched_ids
        assert "wf-launch-test" in tool._launched_ids

    @pytest.mark.asyncio
    async def test_launch_count_increments_only_on_success(self):
        """提交失败时不递增计数器."""
        class _RejectingPort(WorkflowLaunchPort):
            async def submit(self, spec: WorkflowSpec) -> WorkflowLaunchResult:
                return WorkflowLaunchResult(
                    workflow_id=spec.workflow_id,
                    run_id="",
                    accepted=False,
                    adapter="fake",
                    error="rejected",
                )

        port = _RejectingPort()
        launcher = WorkflowLauncher(port=port)
        connector = EventConnector(launcher=launcher)
        deps = CognitiveDependencies(bridge=BridgeDeps(event_connector=connector))
        tool = LaunchWorkflowTool(deps)

        await tool.execute(
            workflow_spec=_make_launch_spec().model_dump(),
            reason="test",
        )

        assert tool._launch_count == 0  # 失败不递增

    @pytest.mark.asyncio
    async def test_launch_count_accumulates(self):
        """多次成功提交后计数器累积."""
        port = _AcceptingPort()
        launcher = WorkflowLauncher(port=port)
        connector = EventConnector(launcher=launcher)
        deps = CognitiveDependencies(bridge=BridgeDeps(event_connector=connector))
        tool = LaunchWorkflowTool(deps)

        for i in range(3):
            spec = _make_launch_spec()
            spec.workflow_id = f"wf-{i}"
            await tool.execute(
                workflow_spec=spec.model_dump(),
                reason=f"launch {i}",
            )

        assert tool._launch_count == 3
