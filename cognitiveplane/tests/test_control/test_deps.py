"""Tests for CognitiveDependencies typed DI."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from cognitiveplane.control.deps import (
    CapabilityDeps,
    CognitiveDependencies,
    ControlDeps,
    GatewayDeps,
    GovernanceDeps,
    KnowledgeDeps,
    MemoryDeps,
)
from cognitiveplane.gateway.adapters.in_memory import InMemoryGatewayAdapter
from cognitiveplane.knowledge.adapters.stub import StubKnowledgeAdapter
from cognitiveplane.memory.adapters.port_adapters import MemorySearchAdapter
from cognitiveplane.memory.repositories.in_memory import InMemoryMemoryRepository
from cognitiveplane.control.repositories.in_memory import InMemoryBrainDecisionRepository
from cognitiveplane.shared.dto.context import ContextSnapshot
from cognitiveplane.shared.enums import EventType, NoveltyLevel
from cognitiveplane.shared.types import CaseId
from cognitiveplane.governance.escalation import EscalationTracker
from cognitiveplane.gateway.pipeline import ValidationPipeline
from cognitiveplane.governance.validators.consistency import ConsistencyValidator
from cognitiveplane.governance.validators.rule import RuleValidator
from cognitiveplane.governance.validators.safety import SafetyValidator
from cognitiveplane.governance.validators.shadow import ShadowValidator


def _make_context(
    novelty: NoveltyLevel = NoveltyLevel.KNOWN,
) -> ContextSnapshot:
    return ContextSnapshot(
        case_id=CaseId(value="test-case-001"),
        event_type=EventType.IQA_COMPLETED,
        workflow_state={},
        case_data={"defect": "porosity"},
        measurements=[],
        memory_match_confidence=0.5,
        knowledge_coverage=0.8,
        event_novelty=novelty,
        validation_critical_count=0,
        timestamp=datetime.now(timezone.utc),
    )


def _make_deps() -> CognitiveDependencies:
    """Build CognitiveDependencies with all mock adapters."""
    memory_repo = InMemoryMemoryRepository()
    gateway = InMemoryGatewayAdapter()
    knowledge = StubKnowledgeAdapter()
    pipeline = ValidationPipeline(
        safety=SafetyValidator(),
        rule=RuleValidator(),
        shadow=ShadowValidator(),
        consistency=ConsistencyValidator(),
        escalation=EscalationTracker(),
    )

    return CognitiveDependencies(
        capability=CapabilityDeps(),
        control=ControlDeps(
            decision_repo=InMemoryBrainDecisionRepository(),
        ),
        knowledge=KnowledgeDeps(
            rag_query=knowledge,
            standards_query=knowledge,
            case_library=knowledge,
            process_knowledge=knowledge,
        ),
        memory=MemoryDeps(
            search=MemorySearchAdapter(memory_repo),
            read=None,
            write=None,
        ),
        gateway=GatewayDeps(read=gateway, write=gateway),
        governance=GovernanceDeps(
            validation=pipeline,
            escalation=EscalationTracker(),
        ),
    )


class TestCognitiveDependencies:
    def test_default_construction(self):
        deps = CognitiveDependencies()
        assert deps.capability is not None
        assert deps.control is not None
        assert deps.knowledge is not None
        assert deps.memory is not None
        assert deps.gateway is not None
        assert deps.governance is not None

    def test_no_deepagents_in_deps(self):
        """DeepAgents ports no longer exist in CognitiveDependencies."""
        deps = _make_deps()
        assert not hasattr(deps, "deepagents")


class TestOrchestratorTypedDeps:
    """Legacy BrainOrchestrator tests removed 2026-06-26 — orchestrator.py deleted
    as DEAD CODE (replaced by ReActEngine per boundary-pinning §0). ReActEngine
    integration covered by test_react_engine.py.
    """


def test_control_deps_has_mcp_registry_field():
    """Spec §7.2 — ControlDeps.mcp_registry 从注释占位变为真实字段。"""
    from cognitiveplane.control.deps import ControlDeps

    deps = ControlDeps()
    assert deps.mcp_registry is None  # default None


def test_control_deps_mcp_registry_assignable():
    """可注入 MCPRegistry 实例。"""
    from cognitiveplane.control.deps import ControlDeps
    from cognitiveplane.adapters.mcp.tool_policy_classifier import (
        MCPToolPolicyClassifier,
    )
    from cognitiveplane.control.mcp_registry import MCPRegistry

    classifier = MCPToolPolicyClassifier(yaml_path=None)
    registry = MCPRegistry(classifier=classifier, event_log=None)
    deps = ControlDeps(mcp_registry=registry)

    assert deps.mcp_registry is registry
