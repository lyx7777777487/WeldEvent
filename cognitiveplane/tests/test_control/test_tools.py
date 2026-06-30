"""Control tools — per-tool unit tests.

Each test constructs the tool with a stub port, calls execute(),
and verifies the ToolResult shape. No I/O, no LLM, no database.
"""

from __future__ import annotations

import pytest
from datetime import UTC, datetime
from uuid import UUID, uuid4

from cognitiveplane.control.tools import ToolResult
from cognitiveplane.control.tools.search_standards import SearchStandardsTool
from cognitiveplane.control.tools.search_cases import SearchCasesTool
from cognitiveplane.control.tools.search_process import SearchProcessTool
from cognitiveplane.control.tools.read_weldmap import ReadWeldMapTool
# AdjustParameterTool removed 2026-06-26 (industrial verb → executionplane ToolPool, boundary-pinning §2.1)
from cognitiveplane.control.tools.request_confirmation import RequestConfirmationTool
from cognitiveplane.control.tools.escalate import EscalateTool
from cognitiveplane.control.tools.explain_decision import ExplainDecisionTool
from cognitiveplane.control.tools.archive_memory import ArchiveMemoryTool
from cognitiveplane.shared.dto.knowledge import (
    StandardsResult,
    CaseLibraryResult,
    ProcessKnowledgeResult,
)
from cognitiveplane.shared.dto_decision.outputs import ParameterSet
from cognitiveplane.shared.dto.context import WeldMapSnapshot
from cognitiveplane.shared.dto.gateway import PublishResult
from cognitiveplane.shared.dto_decision import BrainDecision, DecisionOutput
from cognitiveplane.shared.dto_decision.recommendation import RoutingRecommendation
from cognitiveplane.shared.dto.memory import MemoryContent
from cognitiveplane.shared.enums import (
    AggregatedValidationResult,
    BrainStateType,
    DecisionPointType,
    EventType,
    MemoryType,
    PersonaType,
    ReasoningMode,
    RoutingDecision,
)
from cognitiveplane.shared.types import CaseId, DecisionId, MemoryId
from cognitiveplane.shared.ports.knowledge import (
    StandardsQueryInput,
    StandardsQueryOutput,
    CaseLibraryQueryInput,
    CaseLibraryQueryOutput,
    ProcessKnowledgeInput,
    ProcessKnowledgeOutput,
)
from cognitiveplane.shared.ports.gateway import GatewayReadPort, GatewayWritePort
from cognitiveplane.shared.ports.memory import MemoryWriteInput, MemoryWriteOutput
from cognitiveplane.gateway.ports import CognitiveGatewayWritePort
from cognitiveplane.control.ports import BrainDecisionRepository


# ---------------------------------------------------------------------------
# Stub ports matching actual port ABC signatures
# ---------------------------------------------------------------------------


class _StubStandardsPort:
    async def query(self, input_data: StandardsQueryInput) -> StandardsQueryOutput:
        return StandardsQueryOutput(results=[
            StandardsResult(standard_id="ISO-5817", section="3", clause="3.1", text="quality levels", relevance=0.9)
        ])


class _StubCasesPort:
    async def query(self, input_data: CaseLibraryQueryInput) -> CaseLibraryQueryOutput:
        return CaseLibraryQueryOutput(results=[
            CaseLibraryResult(case_id="C-001", defect_description="porosity", resolution="reduce speed", outcome="resolved", similarity_score=0.8)
        ])


class _StubProcessPort:
    async def query(self, input_data: ProcessKnowledgeInput) -> ProcessKnowledgeOutput:
        return ProcessKnowledgeOutput(results=[
            ProcessKnowledgeResult(
                process_id="P-001",
                recommended_parameters=ParameterSet(parameters={"current": "200A"}),
                quality_criteria={},
                common_defects=[],
            )
        ])


class _StubGatewayRead(GatewayReadPort):
    async def read_workflow_state(self, case_id: CaseId):
        return None

    async def read_case(self, case_id: CaseId):
        return None

    async def read_measurements(self, case_id, parameter_filter=None):
        return []

    async def read_events(self, case_id, since=None):
        return []

    async def read_audit_trail(self, case_id):
        return []

    async def read_weldmap_snapshot(self, case_id: CaseId) -> WeldMapSnapshot:
        return WeldMapSnapshot(
            case_id=case_id, workflow_state={}, measurements=[], decisions=[], events=[],
            snapshot_at=datetime.now(UTC),
        )


class _StubGatewayWrite(GatewayWritePort):
    def __init__(self) -> None:
        self.published: list = []

    async def publish_decision(self, decision):
        self.published.append(("decision", decision))
        return PublishResult(success=True, weldmap_path="/decisions/1", timestamp=datetime.now(UTC))

    async def publish_explanation(self, explanation):
        return PublishResult(success=True, weldmap_path="/explanations/1", timestamp=datetime.now(UTC))

    async def publish_parameter_patch(self, patch):
        self.published.append(("patch", patch))
        return PublishResult(success=True, weldmap_path="/params/1", timestamp=datetime.now(UTC))

    async def publish_escalation(self, escalation):
        self.published.append(("escalation", escalation))
        return PublishResult(success=True, weldmap_path="/escalations/1", timestamp=datetime.now(UTC))

    async def publish_recheck_request(self, request):
        return PublishResult(success=True, weldmap_path="/recheck/1", timestamp=datetime.now(UTC))

    async def publish_consensus_request(self, request):
        return PublishResult(success=True, weldmap_path="/consensus/1", timestamp=datetime.now(UTC))

    async def publish_risk_alert(self, alert):
        return PublishResult(success=True, weldmap_path="/risk/1", timestamp=datetime.now(UTC))

    async def publish_feedback(self, feedback):
        return PublishResult(success=True, weldmap_path="/feedback/1", timestamp=datetime.now(UTC))

    async def publish_memory_promotion_request(self, request):
        return PublishResult(success=True, weldmap_path="/promotion/1", timestamp=datetime.now(UTC))


class _StubCognitiveWrite(CognitiveGatewayWritePort):
    def __init__(self) -> None:
        self.published: list = []

    async def publish_decision(self, decision):
        self.published.append(("decision", decision))
        return PublishResult(success=True, weldmap_path="/decisions/1", timestamp=datetime.now(UTC))

    async def publish_escalation(self, escalation):
        self.published.append(("escalation", escalation))
        return PublishResult(success=True, weldmap_path="/escalations/1", timestamp=datetime.now(UTC))

    async def publish_instruction(self, instruction):
        self.published.append(("instruction", instruction))
        return PublishResult(success=True, weldmap_path="/instructions/1", timestamp=datetime.now(UTC))

    async def notify_workflow_trigger(self, case_id, workflow_config):
        self.published.append(("workflow_trigger", case_id))


class _StubDecisionRepo(BrainDecisionRepository):
    def __init__(self) -> None:
        self._store: dict[UUID, BrainDecision] = {}

    async def save(self, decision: BrainDecision) -> None:
        self._store[decision.decision_id.value] = decision

    async def find_by_id(self, decision_id: DecisionId) -> BrainDecision | None:
        return self._store.get(decision_id.value)

    async def find_by_trigger_event(self, event_type, case_id):
        return []

    async def find_pending_decisions(self, max_results=10):
        return []


class _StubMemoryWrite:
    async def write(self, input_data: MemoryWriteInput) -> MemoryWriteOutput:
        return MemoryWriteOutput(memory_id=MemoryId(value=UUID(int=99)))


def _make_decision() -> BrainDecision:
    return BrainDecision(
        decision_id=DecisionId(value=UUID(int=42)),
        case_id=CaseId(value="case-001"),
        trigger_event_type=EventType.WORKFLOW_ENTERED,
        decision_point=DecisionPointType.DP0,
        persona=PersonaType.PLANNER,
        reasoning_mode=ReasoningMode.ROUTINE,
        state=BrainStateType.PUBLICATION,
        outputs=[DecisionOutput(
            content=RoutingRecommendation(
                route=RoutingDecision.ACCEPT,
                destination="next-stage",
                confidence=0.9,
                rationale="all checks passed",
            ),
            confidence=0.9,
        )],
        validation_result=AggregatedValidationResult.APPROVED,
        confidence=0.9,
        created_at=datetime(2026, 6, 15, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Tool tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_standards_tool_returns_results():
    tool = SearchStandardsTool(_StubStandardsPort())
    result = await tool.execute(query="quality")
    assert result.error is None
    assert result.output["results"][0]["standard_id"] == "ISO-5817"


@pytest.mark.asyncio
async def test_search_cases_tool_returns_results():
    tool = SearchCasesTool(_StubCasesPort())
    result = await tool.execute(query="porosity")
    assert result.error is None
    assert result.output["results"][0]["case_id"] == "C-001"


@pytest.mark.asyncio
async def test_search_process_tool_returns_results():
    tool = SearchProcessTool(_StubProcessPort())
    result = await tool.execute(query="MIG")
    assert result.error is None
    assert result.output["results"][0]["process_id"] == "P-001"


@pytest.mark.asyncio
async def test_read_weldmap_tool_returns_snapshot():
    tool = ReadWeldMapTool(_StubGatewayRead())
    result = await tool.execute(case_id="case-001")
    assert result.error is None
    assert "workflow_state" in result.output


@pytest.mark.asyncio
async def test_request_confirmation_without_gateway():
    tool = RequestConfirmationTool()
    result = await tool.execute(question="Approve parameter change?", options=["yes", "no"])
    assert result.error is None
    assert result.output["status"] == "confirmation_requested"
    assert result.output["question"] == "Approve parameter change?"


@pytest.mark.asyncio
async def test_request_confirmation_with_gateway():
    gw = _StubCognitiveWrite()
    tool = RequestConfirmationTool(gateway_write=gw)
    result = await tool.execute(question="Approve?", options=["yes", "no"], urgency="urgent")
    assert result.error is None
    assert result.output["success"] is True
    assert len(gw.published) == 1
    assert gw.published[0][0] == "instruction"


@pytest.mark.asyncio
async def test_escalate_tool_publishes_escalation():
    gw = _StubCognitiveWrite()
    tool = EscalateTool(validation_port=None, gateway_write=gw)
    result = await tool.execute(reason="Critical validation failure", urgency="critical")
    assert result.error is None
    assert result.output["urgency"] == "CRITICAL"
    assert len(gw.published) == 1
    assert gw.published[0][0] == "escalation"


@pytest.mark.asyncio
async def test_explain_decision_without_repo():
    tool = ExplainDecisionTool()
    result = await tool.execute(decision_id="00000000-0000-0000-0000-000000000042")
    assert result.error is None
    assert "explanation" in result.output


@pytest.mark.asyncio
async def test_explain_decision_with_repo():
    repo = _StubDecisionRepo()
    decision = _make_decision()
    await repo.save(decision)

    tool = ExplainDecisionTool(decision_repo=repo)
    result = await tool.execute(decision_id=str(decision.decision_id.value), audience="engineer")
    assert result.error is None
    assert result.output["persona"] == "PLANNER"
    assert result.output["confidence"] == 0.9
    assert result.output["output_count"] == 1
    assert "routing_recommendation" in result.output["explanation"]


@pytest.mark.asyncio
async def test_explain_decision_invalid_uuid():
    repo = _StubDecisionRepo()
    tool = ExplainDecisionTool(decision_repo=repo)
    result = await tool.execute(decision_id="not-a-uuid")
    assert result.error is not None
    assert "Invalid" in result.error


@pytest.mark.asyncio
async def test_explain_decision_not_found():
    repo = _StubDecisionRepo()
    tool = ExplainDecisionTool(decision_repo=repo)
    result = await tool.execute(decision_id=str(uuid4()))
    assert result.error is not None
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_archive_memory_tool():
    tool = ArchiveMemoryTool(_StubMemoryWrite())
    result = await tool.execute(content="Test memory content", memory_type="observation")
    assert result.error is None
    assert result.output["status"] == "archived"


# ---------------------------------------------------------------------------
# Tool ABC contract tests
# ---------------------------------------------------------------------------


def test_all_tools_have_name_and_description():
    tools = [
        SearchStandardsTool(_StubStandardsPort()),
        SearchCasesTool(_StubCasesPort()),
        SearchProcessTool(_StubProcessPort()),
        ReadWeldMapTool(_StubGatewayRead()),
        RequestConfirmationTool(),
        ExplainDecisionTool(),
    ]
    for tool in tools:
        assert isinstance(tool.name, str) and len(tool.name) > 0
        assert isinstance(tool.description, str) and len(tool.description) > 0
        schema = tool.parameters_schema
        assert "properties" in schema
        assert "required" in schema


def test_all_tools_produce_function_definitions():
    tools = [
        SearchStandardsTool(_StubStandardsPort()),
        SearchCasesTool(_StubCasesPort()),
        SearchProcessTool(_StubProcessPort()),
        ReadWeldMapTool(_StubGatewayRead()),
        RequestConfirmationTool(),
        ExplainDecisionTool(),
    ]
    for tool in tools:
        fd = tool.to_function_definition()
        assert fd["type"] == "function"
        assert fd["function"]["name"] == tool.name
        assert "parameters" in fd["function"]
