"""CognitiveDependencies — per-plane typed dependency groups.

Replaces dict[str, Any] / _has_port() pattern with typed, composable
dependency injection. Each plane assembles its own deps internally.

Source: 7-plane redesign spec §0.5 CognitiveDependencies split.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cognitiveplane.capability.provider import LLMProvider
    from cognitiveplane.capability.web_search import WebSearchProvider
    from cognitiveplane.shared.ports.knowledge import (
        RAGQueryPort,
        StandardsQueryPort,
        CaseLibraryQueryPort,
        ProcessKnowledgePort,
    )
    from cognitiveplane.shared.ports.memory import (
        MemorySearchPort,
        MemoryReadPort,
        MemoryWritePort,
        MemoryPromotionPort,
        MemoryArchivePort,
        MemoryConfidencePort,
    )
    from cognitiveplane.shared.ports.gateway import (
        GatewayReadPort,
        GatewayWritePort,
    )
    from cognitiveplane.shared.ports.validation import ValidationPipelinePort
    from cognitiveplane.shared.ports.collaboration import HumanReviewRequestRepository
    from cognitiveplane.shared.ports.learning import LearningEventRepository
    from cognitiveplane.control.ports import BrainDecisionRepository
    from cognitiveplane.control.registry.mcp_registry import MCPRegistry
    from cognitiveplane.governance.escalation import EscalationTracker
    from cognitiveplane.bridge.event_connector import EventConnector


@dataclass
class CapabilityDeps:
    """L7 Capability plane dependencies — pure capability Providers (no side effects).

    Plan §7 line 2234 contract: CapabilityDeps only holds llm_provider + web_search.
    ImageStore is session-scoped state, not a Provider — passed explicitly through
    ReActEngine → ToolRegistry → AnalyzeImageTool (not stored in any Deps group).
    """
    llm_provider: LLMProvider | None = None
    web_search: WebSearchProvider | None = None
    # Phase 2b: instructor: InstructorClient | None = None
    # Phase 2h: vision: VisionAdapter | None = None


@dataclass
class KnowledgeDeps:
    """L4 Knowledge plane dependencies."""
    rag_query: RAGQueryPort | None = None
    standards_query: StandardsQueryPort | None = None
    case_library: CaseLibraryQueryPort | None = None
    process_knowledge: ProcessKnowledgePort | None = None
    # Phase 2g: reranker: RerankPort | None = None
    # Phase 2g: hybrid_search: HybridSearchPort | None = None


@dataclass
class MemoryDeps:
    """L5 Memory plane dependencies."""
    search: MemorySearchPort | None = None
    read: MemoryReadPort | None = None
    write: MemoryWritePort | None = None
    promotion: MemoryPromotionPort | None = None
    archive: MemoryArchivePort | None = None
    confidence: MemoryConfidencePort | None = None
    # Phase 1d: block_manager: BlockManager | None = None
    # Phase 1d: compactor: ContextCompactor | None = None
    # Phase 2e: dual_write: DualWriteMemoryService | None = None
    # Phase 2e: milvus: MilvusKnowledgeAdapter | None = None


@dataclass
class GatewayDeps:
    """L3 Gateway plane dependencies.

    Plan §7 target design splits this into ``GatewayReadDeps`` /
    ``GatewayWriteDeps`` sub-dataclasses so the composition root can hand
    Control only the read half — a compile-time guard preventing Control from
    bypassing the validation pipeline via a bare WritePort. Phase 3 has not
    done that split: validation is wired through ``GovernanceDeps`` and
    enforced by ``ValidationPipeline`` (gateway/pipeline.py). The type-level
    split is deferred to Phase 4 — that is when the second write path
    (``notify_workflow_trigger``, light schema-only validation) appears and
    the guard becomes load-bearing.
    """
    read: GatewayReadPort | None = None
    write: GatewayWritePort | None = None
    # Phase 2d: nats_publisher: NATSPublisher | None = None
    # Phase 3: split into GatewayReadDeps/GatewayWriteDeps (plan §7 line 2381)


@dataclass
class GovernanceDeps:
    """L2 Governance plane dependencies."""
    validation: ValidationPipelinePort | None = None
    review_repo: HumanReviewRequestRepository | None = None
    learning_repo: LearningEventRepository | None = None
    escalation: EscalationTracker | None = None
    # Phase 3: validation moves to GatewayWriteDeps (plan §7 line 2376)


@dataclass
class ControlDeps:
    """L1 Control plane dependencies — decision repository and MCP registry.

    BrainOrchestrator removed 2026-06-26 (legacy 542-line DEAD CODE, replaced
    by ReActEngine as the single L1 cognitive executor per boundary-pinning §0).
    """
    decision_repo: BrainDecisionRepository | None = None
    mcp_registry: "MCPRegistry | None" = None  # Phase 3 解锁 (plan §7 line 2338)
    # Phase 1c: tool_policy: ToolPolicy | None = None
    # Phase 1c: hooks: list[BeforeToolHook] = field(default_factory=list)
    # Phase 2a: react_graph: CompiledStateGraph | None = None


@dataclass
class BridgeDeps:
    """L1→L2 Bridge dependencies — Cognitive Plane to Temporal Control Plane.

    boundary-pinning §6.2: bridge 层把 WorkflowSpec 翻译成
    temporal_client.start_workflow(RunWorkflowSpec, workflow_spec)。
    默认 _NullWorkflowLaunchPort（降级），生产环境装配 TemporalWorkflowLaunchPort。
    """
    event_connector: "EventConnector | None" = None


@dataclass
class CognitiveDependencies:
    """Top-level container. Each plane assembles its own deps internally."""
    capability: CapabilityDeps = field(default_factory=CapabilityDeps)
    control: ControlDeps = field(default_factory=ControlDeps)
    knowledge: KnowledgeDeps = field(default_factory=KnowledgeDeps)
    memory: MemoryDeps = field(default_factory=MemoryDeps)
    gateway: GatewayDeps = field(default_factory=GatewayDeps)
    governance: GovernanceDeps = field(default_factory=GovernanceDeps)
    bridge: BridgeDeps = field(default_factory=BridgeDeps)
