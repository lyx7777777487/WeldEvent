"""Knowledge DTOs — backward-compat shim.

Canonical home is now ``cognitiveplane.shared.dto.knowledge``.
This module re-exports every public name so existing callers
(``from cognitiveplane.shared.dto_knowledge import ...``) keep working.
"""

from cognitiveplane.shared.dto.knowledge import (  # noqa: F401
    CaseLibraryQuery,
    CaseLibraryResult,
    EquipmentKnowledgeQuery,
    EquipmentKnowledgeResult,
    KnowledgeResult,
    ProcessKnowledgeQuery,
    ProcessKnowledgeResult,
    RAGQuery,
    RuleQuery,
    RuleResult,
    StandardsQuery,
    StandardsResult,
)
from cognitiveplane.shared.enums import KnowledgeType  # noqa: F401

__all__ = [
    "CaseLibraryQuery",
    "CaseLibraryResult",
    "EquipmentKnowledgeQuery",
    "EquipmentKnowledgeResult",
    "KnowledgeResult",
    "KnowledgeType",
    "ProcessKnowledgeQuery",
    "ProcessKnowledgeResult",
    "RAGQuery",
    "RuleQuery",
    "RuleResult",
    "StandardsQuery",
    "StandardsResult",
]
