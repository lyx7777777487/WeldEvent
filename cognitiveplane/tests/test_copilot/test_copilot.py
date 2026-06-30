"""Tests for the copilot/ package — 5 sub-modules with mock ports."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from cognitiveplane.copilot import (
    CopilotExplain,
    CopilotGovern,
    CopilotInvestigate,
    CopilotOperate,
    CopilotQA,
    QAQuery,
)
from cognitiveplane.copilot.explain import DecisionNotFoundError
from cognitiveplane.shared.dto.collaboration import (
    HumanReviewRequest,
    ReviewContent,
)
from cognitiveplane.shared.dto.context import WeldMapSnapshot
from cognitiveplane.shared.dto_decision import BrainDecision, DecisionOutput
from cognitiveplane.shared.dto_decision.outputs import (
    ParameterRecommendation,
    ParameterSet,
)
from cognitiveplane.shared.dto.gateway import (
    CaseData,
    PublishResult,
    WorkflowState,
)
from cognitiveplane.shared.dto.knowledge import (
    CaseLibraryResult,
    KnowledgeResult,
    ProcessKnowledgeResult,
    StandardsResult,
)
from cognitiveplane.shared.dto.memory import MemoryContent, MemorySearchResult
from cognitiveplane.shared.enums import (
    BrainStateType,
    CollaborationLayer,
    DecisionPointType,
    EventType,
    InstructionType,
    KnowledgeType,
    MemoryType,
    PersonaType,
    PromotionStatus,
    ReasoningMode,
    ReviewStatus,
    ReviewType,
)
from cognitiveplane.shared.ports.collaboration import HumanReviewRequestRepository
from cognitiveplane.shared.ports.knowledge import (
    CaseLibraryQueryOutput,
    CaseLibraryQueryPort,
    ProcessKnowledgeOutput,
    ProcessKnowledgePort,
    RAGQueryOutput,
    RAGQueryPort,
    StandardsQueryOutput,
    StandardsQueryPort,
)
from cognitiveplane.shared.ports.memory import MemorySearchOutput, MemorySearchPort
from cognitiveplane.shared.types import (
    CaseId,
    DecisionId,
    KnowledgeId,
    MemoryId,
    ReviewRequestId,
)


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


class _StubRAG(RAGQueryPort):
    def __init__(self, results=None):
        self._results = results or []

    async def query(self, input_data):
        return RAGQueryOutput(results=self._results)


class _StubStandards(StandardsQueryPort):
    def __init__(self, results=None):
        self._results = results or []

    async def query(self, input_data):
        return StandardsQueryOutput(results=self._results)


class _StubProcess(ProcessKnowledgePort):
    def __init__(self, results=None):
        self._results = results or []

    async def query(self, input_data):
        return ProcessKnowledgeOutput(results=self._results)


class _StubCases(CaseLibraryQueryPort):
    def __init__(self, results=None):
        self._results = results or []

    async def query(self, input_data):
        return CaseLibraryQueryOutput(results=self._results)


class _StubMemorySearch(MemorySearchPort):
    def __init__(self, results=None):
        self._results = results or []

    async def search(self, input_data):
        return MemorySearchOutput(results=self._results)


class _StubDecisionRepo:
    def __init__(self, decision: BrainDecision | None = None):
        self._d = decision

    async def save(self, decision):
        self._d = decision

    async def find_by_id(self, decision_id):
        if self._d is None:
            return None
        if str(self._d.decision_id.value) == str(decision_id.value):
            return self._d
        return None

    async def find_by_trigger_event(self, event_type, case_id):
        return []

    async def find_pending_decisions(self, max_results=10):
        return []


class _StubReviewRepo(HumanReviewRequestRepository):
    def __init__(self):
        self._items: list[HumanReviewRequest] = []

    async def save(self, request):
        self._items = [r for r in self._items if r.request_id != request.request_id]
        self._items.append(request)

    async def find_by_id(self, request_id):
        for r in self._items:
            if r.request_id == request_id:
                return r
        return None

    async def find_pending_by_layer(self, layer):
        return [
            r
            for r in self._items
            if r.collaboration_layer == layer and r.status == ReviewStatus.PENDING
        ]

    async def find_by_decision_id(self, decision_id):
        return [r for r in self._items if r.decision_id == decision_id]


class _StubGatewayRead:
    async def read_weldmap_snapshot(self, case_id):
        return WeldMapSnapshot(
            case_id=case_id,
            workflow_state={"status": "RUNNING"},
            measurements=[],
            decisions=[],
            events=[],
            snapshot_at=datetime.now(timezone.utc),
        )

    async def read_workflow_state(self, case_id):
        return WorkflowState(
            case_id=case_id,
            status="RUNNING",
            parameters={},
            updated_at=datetime.now(timezone.utc),
        )

    async def read_case_data(self, case_id):
        return CaseData(
            case_id=case_id,
            case_type="weld",
            creation_date=datetime.now(timezone.utc),
            status="OPEN",
            metadata={},
        )


class _StubGatewayWrite:
    def __init__(self):
        self.published: list = []

    async def publish_decision(self, decision):
        return PublishResult(
            success=True,
            weldmap_path="/dec",
            timestamp=datetime.now(timezone.utc),
        )

    async def publish_escalation(self, escalation):
        return PublishResult(
            success=True,
            weldmap_path="/esc",
            timestamp=datetime.now(timezone.utc),
        )

    async def publish_instruction(self, instruction):
        self.published.append(instruction)
        return PublishResult(
            success=True,
            weldmap_path="/inst",
            timestamp=datetime.now(timezone.utc),
        )

    async def notify_workflow_trigger(self, case_id, workflow_config):
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _decision() -> BrainDecision:
    return BrainDecision(
        decision_id=DecisionId(value=uuid4()),
        case_id=CaseId(value="case-1"),
        trigger_event_type=EventType.WORKFLOW_ENTERED,
        decision_point=DecisionPointType.DP0,
        persona=PersonaType.COPILOT,
        reasoning_mode=ReasoningMode.ROUTINE,
        state=BrainStateType.VALIDATION,
        outputs=[
            DecisionOutput(
                content=ParameterRecommendation(
                    parameters=ParameterSet(parameters={"current": "120A"}),
                    rationale="ok",
                    confidence=0.85,
                    constraints_applied=[],
                ),
                confidence=0.85,
            )
        ],
        confidence=0.85,
        created_at=datetime.now(timezone.utc),
    )


def _review(decision_id: DecisionId, layer: CollaborationLayer, status: ReviewStatus):
    return HumanReviewRequest(
        request_id=ReviewRequestId(value=uuid4()),
        decision_id=decision_id,
        collaboration_layer=layer,
        review_type=ReviewType.APPROVAL,
        status=status,
        content=ReviewContent(
            decision_id=decision_id,
            summary="s",
            reasoning_summary="r",
            risk_summary="rk",
            recommendation="rec",
            confidence=0.8,
        ),
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# CopilotQA
# ---------------------------------------------------------------------------


class TestCopilotQA:
    @pytest.mark.asyncio
    async def test_baseline_no_evidence(self):
        qa = CopilotQA(_StubRAG(), _StubStandards(), _StubProcess(), _StubCases())
        out = await qa.ask(QAQuery(text="What is ISO 5817?"))
        assert "No evidence" in out.answer or "rephrase" in out.answer
        assert out.confidence == 0.0

    @pytest.mark.asyncio
    async def test_aggregated_evidence(self):
        rag = _StubRAG(
            [
                KnowledgeResult(
                    knowledge_id=KnowledgeId(value=uuid4()),
                    knowledge_type=KnowledgeType.STANDARD,
                    content="ISO 5817 levels",
                    relevance_score=0.9,
                    source_reference="ISO",
                )
            ]
        )
        std = _StubStandards(
            [
                StandardsResult(
                    standard_id="ISO5817",
                    section="B",
                    clause="1",
                    text="Quality levels",
                    relevance=0.8,
                )
            ]
        )
        cases = _StubCases(
            [
                CaseLibraryResult(
                    case_id="C1",
                    defect_description="porosity",
                    resolution="reweld",
                    outcome="ok",
                    similarity_score=0.7,
                )
            ]
        )
        qa = CopilotQA(rag, std, _StubProcess(), cases)
        out = await qa.ask(QAQuery(text="ISO 5817?"))
        assert out.confidence > 0.0
        assert len(out.rag_results) == 1
        assert len(out.standards) == 1
        assert len(out.cases) == 1


# ---------------------------------------------------------------------------
# CopilotExplain
# ---------------------------------------------------------------------------


class TestCopilotExplain:
    @pytest.mark.asyncio
    async def test_explain_existing(self):
        d = _decision()
        repo = _StubDecisionRepo(d)
        cop = CopilotExplain(repo)
        out = await cop.explain(d.decision_id)
        assert out.persona == "COPILOT"
        assert out.confidence == 0.85
        assert out.output_count == 1
        assert "case-1" in out.explanation

    @pytest.mark.asyncio
    async def test_missing_decision_raises(self):
        cop = CopilotExplain(_StubDecisionRepo(None))
        with pytest.raises(DecisionNotFoundError):
            await cop.explain(DecisionId(value=uuid4()))


# ---------------------------------------------------------------------------
# CopilotInvestigate
# ---------------------------------------------------------------------------


class TestCopilotInvestigate:
    @pytest.mark.asyncio
    async def test_combines_signals(self):
        cases = _StubCases(
            [
                CaseLibraryResult(
                    case_id="X1",
                    defect_description="undercut",
                    resolution="lower current",
                    outcome="ok",
                    similarity_score=0.85,
                )
            ]
        )
        proc = _StubProcess(
            [
                ProcessKnowledgeResult(
                    process_id="P1",
                    recommended_parameters=ParameterSet(parameters={}),
                    quality_criteria={},
                    common_defects=["undercut", "porosity"],
                )
            ]
        )
        mem = _StubMemorySearch(
            [
                MemorySearchResult(
                    memory_id=MemoryId(value=uuid4()),
                    content=MemoryContent(
                        summary="prior undercut case",
                        details={},
                        feature_vector=[0.0],
                    ),
                    similarity_score=0.7,
                    promotion_status=PromotionStatus.PROMOTED,
                    created_at=datetime.now(timezone.utc),
                )
            ]
        )
        cop = CopilotInvestigate(cases, proc, mem)
        out = await cop.investigate("undercut at toe", defect_type="undercut")
        assert len(out.candidate_causes) >= 1
        # top candidate should be the case match (score 0.85)
        assert out.candidate_causes[0].likelihood >= 0.7
        assert "undercut" in out.summary


# ---------------------------------------------------------------------------
# CopilotGovern
# ---------------------------------------------------------------------------


class TestCopilotGovern:
    @pytest.mark.asyncio
    async def test_summarize_pending_layers(self):
        review_repo = _StubReviewRepo()
        decision_repo = _StubDecisionRepo()
        d_id = DecisionId(value=uuid4())
        await review_repo.save(_review(d_id, CollaborationLayer.L1, ReviewStatus.PENDING))
        await review_repo.save(_review(d_id, CollaborationLayer.L1, ReviewStatus.APPROVED))
        await review_repo.save(_review(d_id, CollaborationLayer.L2, ReviewStatus.PENDING))
        cop = CopilotGovern(review_repo, decision_repo)
        summary = await cop.summarize_pending()
        assert summary.pending_by_layer["L1"] == 1
        assert summary.pending_by_layer["L2"] == 1
        assert summary.pending_by_layer["L0"] == 0

    @pytest.mark.asyncio
    async def test_decision_review_history(self):
        d = _decision()
        review_repo = _StubReviewRepo()
        await review_repo.save(_review(d.decision_id, CollaborationLayer.L1, ReviewStatus.APPROVED))
        cop = CopilotGovern(review_repo, _StubDecisionRepo(d))
        summary = await cop.decision_review_history(d.decision_id)
        assert summary.decision_review_count == 1

    def test_is_compliant(self):
        d_id = DecisionId(value=uuid4())
        approved = _review(d_id, CollaborationLayer.L1, ReviewStatus.APPROVED)
        denied = _review(d_id, CollaborationLayer.L1, ReviewStatus.DENIED)
        assert CopilotGovern.is_compliant([approved]) is True
        assert CopilotGovern.is_compliant([denied]) is False
        assert CopilotGovern.is_compliant([]) is True


# ---------------------------------------------------------------------------
# CopilotOperate
# ---------------------------------------------------------------------------


class TestCopilotOperate:
    @pytest.mark.asyncio
    async def test_recommend_without_publish(self):
        gw_read = _StubGatewayRead()
        gw_write = _StubGatewayWrite()
        cop = CopilotOperate(gw_read, gw_write)
        case_id = CaseId(value="case-Z")
        out = await cop.recommend(case_id, intent=InstructionType.ANNOTATE)
        assert out.case_id.value == "case-Z"
        assert out.instruction.instruction_type == InstructionType.ANNOTATE
        assert out.publish_result is None
        assert gw_write.published == []

    @pytest.mark.asyncio
    async def test_recommend_with_publish(self):
        gw_read = _StubGatewayRead()
        gw_write = _StubGatewayWrite()
        cop = CopilotOperate(gw_read, gw_write)
        out = await cop.recommend(
            CaseId(value="case-P"),
            intent=InstructionType.ADJUST_PARAMETER,
            payload={"current": "115A"},
            rationale="lower current",
            publish=True,
        )
        assert out.publish_result is not None
        assert out.publish_result.success is True
        assert len(gw_write.published) == 1
