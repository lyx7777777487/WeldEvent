"""L1 Cognitive Plane — Shared port ABCs.

Re-exports all port ABCs and repository interfaces for convenient importing.
Source: L1_Port_and_Contract_Design.md (Phase 4, Sections 6–10).
"""

# ---------------------------------------------------------------------------
# Memory ports (Section 8)
# ---------------------------------------------------------------------------
from cognitiveplane.shared.ports.memory import (  # noqa: F401
    MemoryArchiveInput,
    MemoryArchiveOutput,
    MemoryArchivePort,
    MemoryConfidenceInput,
    MemoryConfidenceOutput,
    MemoryConfidencePort,
    MemoryPromotionInput,
    MemoryPromotionOutput,
    MemoryPromotionPort,
    MemoryReadInput,
    MemoryReadOutput,
    MemoryReadPort,
    MemoryRepository,
    MemorySearchInput,
    MemorySearchOutput,
    MemorySearchPort,
    MemoryWriteInput,
    MemoryWriteOutput,
    MemoryWritePort,
)

# ---------------------------------------------------------------------------
# Validation ports (Section 9)
# ---------------------------------------------------------------------------
from cognitiveplane.shared.ports.validation import (  # noqa: F401
    ConsistencyValidatorInput,
    ConsistencyValidatorOutput,
    ConsistencyValidatorPort,
    EscalationState,
    EscalationTrackerInput,
    EscalationTrackerOutput,
    EscalationTrackerPort,
    RuleValidatorInput,
    RuleValidatorOutput,
    RuleValidatorPort,
    SafetyValidatorInput,
    SafetyValidatorOutput,
    SafetyValidatorPort,
    ShadowValidatorInput,
    ShadowValidatorOutput,
    ShadowValidatorPort,
    ValidationPipelineInput,
    ValidationPipelineOutput,
    ValidationPipelinePort,
    ValidationResultRepository,
)

# ---------------------------------------------------------------------------
# Gateway ports (Section 10)
# ---------------------------------------------------------------------------
from cognitiveplane.shared.ports.gateway import (  # noqa: F401
    EventSubscriptionConfig,
    GatewayEventSubscriptionPort,
    GatewayHealthPort,
    GatewayHealthStatus,
    GatewayReadPort,
    GatewayWritePort,
    SubscriptionStatus,
)

# ---------------------------------------------------------------------------
# Knowledge ports (Section advice.md.advice.md)
# ---------------------------------------------------------------------------
from cognitiveplane.shared.ports.knowledge import (  # noqa: F401
    CaseLibraryQueryInput,
    CaseLibraryQueryOutput,
    CaseLibraryQueryPort,
    EquipmentKnowledgeInput,
    EquipmentKnowledgeOutput,
    EquipmentKnowledgePort,
    KnowledgeRepository,
    ProcessKnowledgeInput,
    ProcessKnowledgeOutput,
    ProcessKnowledgePort,
    RAGQueryInput,
    RAGQueryOutput,
    RAGQueryPort,
    RuleQueryInput,
    RuleQueryOutput,
    RuleQueryPort,
    StandardsQueryInput,
    StandardsQueryOutput,
    StandardsQueryPort,
)

# ---------------------------------------------------------------------------
# Collaboration ports (Section 6.5)
# ---------------------------------------------------------------------------
from cognitiveplane.shared.ports.collaboration import (  # noqa: F401
    HumanReviewRequestRepository,
)

# ---------------------------------------------------------------------------
# Learning ports (Section 6.6)
# ---------------------------------------------------------------------------
from cognitiveplane.shared.ports.learning import (  # noqa: F401
    LearningEventRepository,
)
