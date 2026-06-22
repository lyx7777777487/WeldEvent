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
    from cognitiveplane.control.orchestrator import BrainOrchestrator
    from cognitiveplane.governance.escalation import EscalationTracker


@dataclass
class CapabilityDeps:
    """L7 Capability plane dependencies — LLM providers + web search."""
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
    """L3 Gateway plane dependencies."""
    read: GatewayReadPort | None = None
    write: GatewayWritePort | None = None
    # Phase 2d: nats_publisher: NATSPublisher | None = None


@dataclass
class GovernanceDeps:
    """L2 Governance plane dependencies."""
    validation: ValidationPipelinePort | None = None
    review_repo: HumanReviewRequestRepository | None = None
    learning_repo: LearningEventRepository | None = None
    escalation: EscalationTracker | None = None


@dataclass
class ControlDeps:
    """L1 Control plane dependencies — orchestrator and decision repository."""
    orchestrator: BrainOrchestrator | None = None
    decision_repo: BrainDecisionRepository | None = None
    # Phase 1c: tool_policy: ToolPolicy | None = None
    # Phase 1c: hooks: list[BeforeToolHook] = field(default_factory=list)
    # Phase 2a: react_graph: CompiledStateGraph | None = None


@dataclass
class CognitiveDependencies:
    """Top-level container. Each plane assembles its own deps internally."""
    capability: CapabilityDeps = field(default_factory=CapabilityDeps)
    control: ControlDeps = field(default_factory=ControlDeps)
    knowledge: KnowledgeDeps = field(default_factory=KnowledgeDeps)
    memory: MemoryDeps = field(default_factory=MemoryDeps)
    gateway: GatewayDeps = field(default_factory=GatewayDeps)
    governance: GovernanceDeps = field(default_factory=GovernanceDeps)
