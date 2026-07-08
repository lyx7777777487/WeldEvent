"""Bridge — EventConnector + WorkflowLauncher tests (boundary-pinning §6.2).

Pins the L1→L2 hand-off contract: a WorkflowSpec produced by design_workflow
is submitted to Temporal via EventConnector → WorkflowLauncher → WorkflowLaunchPort.

P2-11 fix (2026-06-26): 完全重写匹配新 API。旧测试调 legacy
on_decision_published/_should_launch/WorkflowTemplate，已随 §6.2 改向废弃。
"""

from __future__ import annotations

import pytest

from cognitiveplane.bridge.event_connector import EventConnector
from cognitiveplane.bridge.workflow_launcher import (
    WorkflowLaunchPort,
    WorkflowLaunchResult,
    WorkflowLauncher,
)
from cognitiveplane.shared.dto_workflow import (
    CallerContext,
    WorkflowNode,
    WorkflowSpec,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_spec(
    *,
    objective: str = "Inspect weld seam for defects",
    nodes: list[WorkflowNode] | None = None,
    case_id: str = "case-0002",
) -> WorkflowSpec:
    """Build a minimal valid WorkflowSpec for testing."""
    if nodes is None:
        nodes = [
            WorkflowNode(
                node_id="node-1",
                type="tool_task",
                capability="defect_detection",
                caller_context=CallerContext(case_id=case_id),
            ),
        ]
    return WorkflowSpec(
        workflow_id="wf-test-001",
        objective=objective,
        nodes=nodes,
    )


class _RecordingPort(WorkflowLaunchPort):
    """Fake port that records submitted specs and other operations."""

    def __init__(
        self,
        *,
        accept: bool = True,
        gate_ok: bool = True,
        cancel_ok: bool = True,
        status: dict | None = None,
    ) -> None:
        self.submitted: list[WorkflowSpec] = []
        self._accept = accept
        self._gate_ok = gate_ok
        self._cancel_ok = cancel_ok
        self._status = status or {
            "status": "RUNNING",
            "completed_nodes": ["n1"],
            "failed_nodes": [],
            "node_results": {},
            "error": None,
        }
        self.queried: list[str] = []
        self.gate_signals: list[tuple[str, str, bool]] = []
        self.cancelled: list[tuple[str, str]] = []

    async def submit(self, spec: WorkflowSpec) -> WorkflowLaunchResult:
        self.submitted.append(spec)
        return WorkflowLaunchResult(
            workflow_id=spec.workflow_id,
            run_id="run-fake-001",
            accepted=self._accept,
            adapter="fake",
            error=None if self._accept else "fake rejection",
        )

    async def query_status(self, workflow_id: str) -> dict:
        self.queried.append(workflow_id)
        return self._status

    async def send_human_gate_signal(
        self, workflow_id: str, node_id: str, approved: bool
    ) -> bool:
        self.gate_signals.append((workflow_id, node_id, approved))
        return self._gate_ok

    async def cancel_workflow(self, workflow_id: str, reason: str = "user requested") -> bool:
        self.cancelled.append((workflow_id, reason))
        return self._cancel_ok


class _RecordingGatewayWrite:
    """Fake CognitiveGatewayWritePort that records notify_workflow_trigger calls."""

    def __init__(self) -> None:
        self.triggered: list[tuple[str, dict]] = []

    async def notify_workflow_trigger(self, case_id, workflow_config: dict) -> None:
        self.triggered.append((str(case_id.value), workflow_config))

    # Other abstract methods not needed for bridge tests
    async def publish_decision(self, decision): ...
    async def publish_escalation(self, escalation): ...
    async def publish_instruction(self, instruction): ...


# ---------------------------------------------------------------------------
# EventConnector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connector_submits_workflow_spec_to_launcher() -> None:
    """正常路径: WorkflowSpec → EventConnector → Launcher → Port."""
    port = _RecordingPort()
    launcher = WorkflowLauncher(port=port)
    connector = EventConnector(launcher=launcher)
    spec = _make_spec()

    result = await connector.on_workflow_spec(spec)

    assert result.accepted is True
    assert result.workflow_id == "wf-test-001"
    assert result.run_id == "run-fake-001"
    assert result.adapter == "fake"
    assert len(port.submitted) == 1
    assert port.submitted[0].workflow_id == "wf-test-001"


@pytest.mark.asyncio
async def test_connector_skips_spec_with_no_nodes() -> None:
    """空 nodes 的 spec 不提交，返回 accepted=False."""
    port = _RecordingPort()
    launcher = WorkflowLauncher(port=port)
    connector = EventConnector(launcher=launcher)
    spec = _make_spec(nodes=[])

    result = await connector.on_workflow_spec(spec)

    assert result.accepted is False
    assert result.adapter == "null"
    assert "no nodes" in (result.error or "").lower()
    assert len(port.submitted) == 0


@pytest.mark.asyncio
async def test_connector_propagates_launch_failure() -> None:
    """Port 拒绝时，result.accepted=False 透传."""
    port = _RecordingPort(accept=False)
    launcher = WorkflowLauncher(port=port)
    connector = EventConnector(launcher=launcher)
    spec = _make_spec()

    result = await connector.on_workflow_spec(spec)

    assert result.accepted is False
    assert result.error == "fake rejection"
    assert len(port.submitted) == 1


@pytest.mark.asyncio
async def test_connector_notifies_gateway_before_launch() -> None:
    """P2-9: 提交 Temporal 前先调 gateway.notify_workflow_trigger."""
    port = _RecordingPort()
    launcher = WorkflowLauncher(port=port)
    gateway = _RecordingGatewayWrite()
    connector = EventConnector(launcher=launcher, gateway_write=gateway)
    spec = _make_spec(case_id="case-bridge-001")

    await connector.on_workflow_spec(spec)

    assert len(gateway.triggered) == 1
    case_id, config = gateway.triggered[0]
    assert case_id == "case-bridge-001"
    assert config["workflow_id"] == "wf-test-001"
    assert config["objective"] == "Inspect weld seam for defects"
    assert config["node_count"] == 1
    # Temporal 也被提交（gateway 通知不阻断）
    assert len(port.submitted) == 1


@pytest.mark.asyncio
async def test_connector_gateway_failure_does_not_block_launch() -> None:
    """P2-9: gateway 通知失败不阻断 Temporal 提交."""
    port = _RecordingPort()
    launcher = WorkflowLauncher(port=port)

    class _FailingGateway(_RecordingGatewayWrite):
        async def notify_workflow_trigger(self, case_id, workflow_config):
            raise RuntimeError("WeldMap unavailable")

    gateway = _FailingGateway()
    connector = EventConnector(launcher=launcher, gateway_write=gateway)
    spec = _make_spec()

    result = await connector.on_workflow_spec(spec)

    assert result.accepted is True  # Temporal 仍提交成功
    assert len(port.submitted) == 1


@pytest.mark.asyncio
async def test_connector_without_gateway_skips_notification() -> None:
    """gateway_write=None 时跳过通知（开发/测试降级）."""
    port = _RecordingPort()
    launcher = WorkflowLauncher(port=port)
    connector = EventConnector(launcher=launcher, gateway_write=None)
    spec = _make_spec()

    result = await connector.on_workflow_spec(spec)

    assert result.accepted is True
    assert len(port.submitted) == 1


@pytest.mark.asyncio
async def test_connector_aclose_forwards_to_launcher() -> None:
    """P2-3: aclose 转发到 launcher."""
    port = _RecordingPort()
    launcher = WorkflowLauncher(port=port)
    connector = EventConnector(launcher=launcher)

    # 不应抛异常
    await connector.aclose()


@pytest.mark.asyncio
async def test_connector_is_healthy_forwards_to_launcher() -> None:
    """P2-R3-5: is_healthy 转发到 launcher."""
    port = _RecordingPort()
    launcher = WorkflowLauncher(port=port)
    connector = EventConnector(launcher=launcher)

    healthy = await connector.is_healthy()
    # _RecordingPort 没有 is_healthy 方法 → launcher 默认返回 True
    assert healthy is True


@pytest.mark.asyncio
async def test_connector_query_status_forwards_to_launcher() -> None:
    """query_status 转发到 launcher → port，返回状态 dict."""
    port = _RecordingPort()
    launcher = WorkflowLauncher(port=port)
    connector = EventConnector(launcher=launcher)

    status = await connector.query_status("wf-query-001")

    assert status["status"] == "RUNNING"
    assert status["completed_nodes"] == ["n1"]
    assert len(port.queried) == 1
    assert port.queried[0] == "wf-query-001"


@pytest.mark.asyncio
async def test_connector_send_human_gate_signal_forwards_to_launcher() -> None:
    """send_human_gate_signal 转发到 launcher → port (boundary-pinning §6.2 契约 5)."""
    port = _RecordingPort(gate_ok=True)
    launcher = WorkflowLauncher(port=port)
    connector = EventConnector(launcher=launcher)

    ok = await connector.send_human_gate_signal("wf-gate-001", "node-gate", approved=True)

    assert ok is True
    assert len(port.gate_signals) == 1
    wf_id, node_id, approved = port.gate_signals[0]
    assert wf_id == "wf-gate-001"
    assert node_id == "node-gate"
    assert approved is True


@pytest.mark.asyncio
async def test_connector_cancel_workflow_forwards_to_launcher() -> None:
    """cancel_workflow 转发到 launcher → port."""
    port = _RecordingPort(cancel_ok=True)
    launcher = WorkflowLauncher(port=port)
    connector = EventConnector(launcher=launcher)

    ok = await connector.cancel_workflow("wf-cancel-001", reason="user abort")

    assert ok is True
    assert len(port.cancelled) == 1
    wf_id, reason = port.cancelled[0]
    assert wf_id == "wf-cancel-001"
    assert reason == "user abort"


# ---------------------------------------------------------------------------
# WorkflowLauncher + port
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_launcher_uses_null_port() -> None:
    """无 port 时用 _NullWorkflowLaunchPort，返回 accepted=False."""
    launcher = WorkflowLauncher()
    spec = _make_spec()

    result = await launcher.launch(spec)

    assert result.accepted is False
    assert result.adapter == "null"
    assert "adapter" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_custom_port_is_honoured() -> None:
    """自定义 port 的 submit 被调用."""
    port = _RecordingPort()
    launcher = WorkflowLauncher(port=port)
    spec = _make_spec()

    result = await launcher.launch(spec)

    assert result.accepted is True
    assert result.adapter == "fake"
    assert port.submitted[0] is spec


@pytest.mark.asyncio
async def test_launcher_aclose_forwards_to_port() -> None:
    """P2-3: launcher.aclose 转发到 port（若 port 支持）."""

    class _ClosablePort(_RecordingPort):
        def __init__(self) -> None:
            super().__init__()
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    port = _ClosablePort()
    launcher = WorkflowLauncher(port=port)

    await launcher.aclose()

    assert port.closed is True


@pytest.mark.asyncio
async def test_launcher_is_healthy_forwards_to_port() -> None:
    """P2-R3-5: launcher.is_healthy 转发到 port（若 port 支持）."""

    class _HealthyPort(_RecordingPort):
        async def is_healthy(self) -> bool:
            return False  # 模拟不健康

    port = _HealthyPort()
    launcher = WorkflowLauncher(port=port)

    healthy = await launcher.is_healthy()

    assert healthy is False
