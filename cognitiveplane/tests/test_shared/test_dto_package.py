"""shared/dto/ package — DTO import path tests.

2026-06-26: 11 root-level ``shared.dto_*`` shims deleted. Canonical path is
now ``shared.dto.<topic>`` exclusively. The shim-identity tests below were
removed; what remains verifies the package re-exports still resolve.
"""

from __future__ import annotations


def test_decision_dto_reexport_identity():
    """dto.decision re-exports from dto_decision subpackage — same object."""
    from cognitiveplane.shared import dto_decision as legacy
    from cognitiveplane.shared.dto import decision as new

    assert new.BrainDecision is legacy.BrainDecision
    assert new.DecisionOutput is legacy.DecisionOutput


def test_escalation_dto_reexport_identity():
    """dto.escalation re-exports Escalation from dto.gateway — same object."""
    from cognitiveplane.shared.dto import escalation as reexport
    from cognitiveplane.shared.dto import gateway as canonical

    assert reexport.Escalation is canonical.Escalation


def test_dto_package_exports_all_six_topics():
    """Smoke test: all six spec-mandated submodules import cleanly."""
    from cognitiveplane.shared import dto

    for topic in ("context", "decision", "memory", "knowledge", "validation", "escalation"):
        assert getattr(dto, topic) is not None
        assert getattr(dto, topic).__name__ == f"cognitiveplane.shared.dto.{topic}"


def test_no_root_dto_shims_remain():
    """2026-06-26: root-level shared.dto_* shims deleted — verify they're gone."""
    import importlib
    import pytest

    for name in (
        "collaboration", "context", "deepagents", "gateway", "knowledge",
        "learning", "memory", "persona", "reasoning_mode", "validation",
        "weldmap_events",
    ):
        with pytest.raises(ImportError):
            importlib.import_module(f"cognitiveplane.shared.dto_{name}")