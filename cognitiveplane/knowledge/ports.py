"""Knowledge plane ports — re-export from shared for backward compatibility.

Source: 7-plane redesign spec §4 Port Ownership Map.
Knowledge ports should live here, but we re-export from shared/ports/knowledge.py
until full migration in Phase 1f.
"""

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
