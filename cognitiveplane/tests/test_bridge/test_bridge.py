"""Bridge — translator + connector + launcher tests (spec §6.1).

Pins the L1→L2 hand-off contract: a published BrainDecision turns into
a JSON-serialisable WorkflowTemplate, and only decisions whose primary
output declares a non-generic kind launch a workflow.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from cognitiveplane.bridge.decision_translator import (
    GENERIC_KIND,
    DecisionTranslator,
    WorkflowTemplate,
)
from cognitiveplane.bridge.event_connector import EventConnector
from cognitiveplane.bridge.workflow_launcher import (
    WorkflowLaunchPort,
    WorkflowLaunchResult,
    WorkflowLauncher,
)
from cognitiveplane.shared.dto_decision.decision import (
    BrainDecision,
    DecisionOutput,
)
from cognitiveplane.shared.dto_decision.recommendation import (
    RoutingRecommendation,
)
from cognitiveplane.shared.enums import (
    AggregatedValidationResult,
    BrainStateType,
    DecisionPointType,
    EventType,
    PersonaType,
    ReasoningMode,
    RoutingDecision,
)
from cognitiveplane.shared.types import CaseId, DecisionId


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _routing_recommendation() -> RoutingRecommendation:
    return RoutingRecommendation(
        route=RoutingDecision.ACCEPT,
        destination="next-stage",
        confidence=0.9,
        rationale="all checks passed",
    )


def _build_decision(
    *,
    output_content: object | None,
) -> BrainDecision:
    outputs: list[DecisionOutput]
    if output_content is None:
        outputs = []
    else:
        outputs = [DecisionOutput(content=output_content, confidence=0.85)]
    return BrainDecision(
        decision_id=DecisionId(value=UUID(int=1)),
        case_id=CaseId(value="case-0002"),
        trigger_event_type=EventType.WORKFLOW_ENTERED,
        decision_point=DecisionPointType.DP0,
        persona=PersonaType.PLANNER,
        reasoning_mode=ReasoningMode.ROUTINE,
        state=BrainStateType.PUBLICATION,
        outputs=outputs,
        validation_result=AggregatedValidationResult.APPROVED,
        confidence=0.9,
        created_at=datetime(2026, 6, 15, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# DecisionTranslator
# ---------------------------------------------------------------------------


def test_translator_projects_primary_output_into_template() -> None:
    decision = _build_decision(output_content=_routing_recommendation())

    template = DecisionTranslator().translate(decision)

    assert isinstance(template, WorkflowTemplate)
    assert template.template_id == f"tmpl-{decision.decision_id.value}"
    assert template.case_id == "case-0002"
    assert template.decision_id == str(decision.decision_id.value)
    assert template.workflow_kind == "routing_recommendation"
    assert template.parameters["route"] == RoutingDecision.ACCEPT.value
    assert template.parameters["destination"] == "next-stage"
    assert template.metadata == {
        "persona": PersonaType.PLANNER.value,
        "reasoning_mode": ReasoningMode.ROUTINE.value,
        "confidence": 0.9,
    }


def test_translator_falls_back_to_generic_kind_when_outputs_empty() -> None:
    decision = _build_decision(output_content=None)

    template = DecisionTranslator().translate(decision)

    assert template.workflow_kind == GENERIC_KIND
    assert template.parameters == {}


# ---------------------------------------------------------------------------
# EventConnector
# ---------------------------------------------------------------------------


class _RecordingLauncher(WorkflowLauncher):
    def __init__(self) -> None:
        super().__init__()
        self.launched: list[WorkflowTemplate] = []

    async def launch(self, template: WorkflowTemplate) -> WorkflowLaunchResult:
        self.launched.append(template)
        return await super().launch(template)


@pytest.mark.asyncio
async def test_connector_skips_decisions_without_actionable_kind() -> None:
    connector = EventConnector(DecisionTranslator(), _RecordingLauncher())
    decision = _build_decision(output_content=None)

    result = await connector.on_decision_published(decision)

    assert result is None


@pytest.mark.asyncio
async def test_connector_launches_actionable_decisions() -> None:
    launcher = _RecordingLauncher()
    connector = EventConnector(DecisionTranslator(), launcher)
    decision = _build_decision(output_content=_routing_recommendation())

    result = await connector.on_decision_published(decision)

    assert result is not None
    assert result.accepted is True
    assert result.template_id == f"tmpl-{decision.decision_id.value}"
    assert result.metadata == {"adapter": "in-memory"}
    assert len(launcher.launched) == 1
    assert launcher.launched[0].workflow_kind == "routing_recommendation"


def test_should_launch_filter_rejects_generic_kind() -> None:
    class _GenericContent:
        type = GENERIC_KIND

    decision = _build_decision(output_content=None)
    decision = decision.model_copy(
        update={"outputs": [DecisionOutput.model_construct(content=_GenericContent(), confidence=0.5)]}
    )

    assert EventConnector._should_launch(decision) is False


def test_should_launch_filter_accepts_typed_content() -> None:
    decision = _build_decision(output_content=_routing_recommendation())

    assert EventConnector._should_launch(decision) is True


# ---------------------------------------------------------------------------
# WorkflowLauncher + custom port
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_launcher_emits_in_memory_metadata() -> None:
    launcher = WorkflowLauncher()
    template = WorkflowTemplate(
        template_id="tmpl-x",
        case_id="case-x",
        decision_id="dec-x",
        workflow_kind="routing_recommendation",
    )

    result = await launcher.launch(template)

    assert result.template_id == "tmpl-x"
    assert result.workflow_id.startswith("wf-")
    assert result.run_id.startswith("run-")
    assert result.accepted is True
    assert result.metadata == {"adapter": "in-memory"}


@pytest.mark.asyncio
async def test_custom_workflow_launch_port_is_honoured() -> None:
    class _FakePort(WorkflowLaunchPort):
        def __init__(self) -> None:
            self.received: WorkflowTemplate | None = None

        async def submit(self, template: WorkflowTemplate) -> WorkflowLaunchResult:
            self.received = template
            return WorkflowLaunchResult(
                workflow_id="wf-custom",
                run_id="run-custom",
                template_id=template.template_id,
                accepted=True,
                metadata={"adapter": "fake"},
            )

    port = _FakePort()
    launcher = WorkflowLauncher(port=port)
    template = WorkflowTemplate(
        template_id="tmpl-y",
        case_id="case-y",
        decision_id="dec-y",
        workflow_kind="routing_recommendation",
    )

    result = await launcher.launch(template)

    assert port.received is template
    assert result.workflow_id == "wf-custom"
    assert result.metadata == {"adapter": "fake"}
