"""shared/dto/ package — re-export identity tests (spec §骨架 1011-1018).

The Phase 1 shims publish the canonical DTO names under
`cognitiveplane.shared.dto.<topic>` while the legacy flat
`shared.dto_*.py` modules still hold the actual class definitions.
These tests pin the **object identity**: the new path must hand back
the exact same class object as the legacy path, otherwise downstream
isinstance checks would silently break across the migration.
"""

from __future__ import annotations


def test_context_dto_shim_identity():
    from cognitiveplane.shared import dto_context as legacy
    from cognitiveplane.shared.dto import context as new

    for name in (
        "ContextSnapshot",
        "DomainEvent",
        "WeldMapSnapshot",
        "BrainInternalEvent",
    ):
        assert getattr(new, name) is getattr(legacy, name), name


def test_decision_dto_shim_identity():
    from cognitiveplane.shared import dto_decision as legacy
    from cognitiveplane.shared.dto import decision as new

    assert new.BrainDecision is legacy.BrainDecision
    assert new.DecisionOutput is legacy.DecisionOutput


def test_memory_dto_shim_identity():
    from cognitiveplane.shared import dto_memory as legacy
    from cognitiveplane.shared.dto import memory as new

    for name in (
        "MemoryContent",
        "MemorySearchQuery",
        "MemorySearchResult",
        "MemoryRecord",
    ):
        assert getattr(new, name) is getattr(legacy, name), name


def test_knowledge_dto_shim_identity():
    from cognitiveplane.shared import dto_knowledge as legacy
    from cognitiveplane.shared.dto import knowledge as new

    for name in (
        "RAGQuery",
        "KnowledgeResult",
        "StandardsQuery",
        "StandardsResult",
        "CaseLibraryQuery",
        "CaseLibraryResult",
        "ProcessKnowledgeQuery",
        "ProcessKnowledgeResult",
        "EquipmentKnowledgeQuery",
        "EquipmentKnowledgeResult",
    ):
        assert getattr(new, name) is getattr(legacy, name), name


def test_validation_dto_shim_identity():
    from cognitiveplane.shared import dto_validation as legacy
    from cognitiveplane.shared.dto import validation as new

    for name in (
        "ValidationResult",
        "SafetyValidationResult",
        "RuleValidationResult",
        "ShadowValidationResult",
        "ConsistencyValidationResult",
    ):
        assert getattr(new, name) is getattr(legacy, name), name


def test_escalation_dto_shim_identity():
    from cognitiveplane.shared import dto_gateway as legacy
    from cognitiveplane.shared.dto import escalation as new

    assert new.Escalation is legacy.Escalation


def test_dto_package_exports_all_six_topics():
    """Smoke test: all six spec-mandated submodules import cleanly."""
    from cognitiveplane.shared import dto

    for topic in ("context", "decision", "memory", "knowledge", "validation", "escalation"):
        assert getattr(dto, topic) is not None
        assert getattr(dto, topic).__name__ == f"cognitiveplane.shared.dto.{topic}"
