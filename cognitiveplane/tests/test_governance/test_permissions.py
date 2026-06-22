"""governance/permissions.py — RBAC behaviour tests (spec §骨架 line 892).

The Phase 1 `StaticRBAC` is the contract Phase 2's OPA adapter must
match: same `PermissionPort.check()` signature, same allow/deny shape.
These tests pin the behaviour so the swap-out doesn't regress.
"""

from __future__ import annotations

import pytest

from cognitiveplane.governance.permissions import (
    DEFAULT_RBAC,
    PermissionDecision,
    Principal,
    StaticRBAC,
)


@pytest.mark.asyncio
async def test_grants_action_when_role_matches():
    rbac = StaticRBAC(DEFAULT_RBAC)
    p = Principal(id="u1", roles=frozenset({"engineer"}))

    decision = await rbac.check(p, "design_workflow", "case-1")

    assert isinstance(decision, PermissionDecision)
    assert decision.allow is True
    assert "engineer" in decision.reason


@pytest.mark.asyncio
async def test_denies_action_when_no_role_grants_it():
    rbac = StaticRBAC(DEFAULT_RBAC)
    p = Principal(id="u1", roles=frozenset({"engineer"}))

    decision = await rbac.check(p, "override_safety_block", "case-1")

    assert decision.allow is False
    assert "override_safety_block" in decision.reason


@pytest.mark.asyncio
async def test_principal_with_no_roles_denied_everything():
    rbac = StaticRBAC(DEFAULT_RBAC)
    p = Principal(id="anon")  # roles default to empty frozenset

    for action in (
        "read_weldmap",
        "design_workflow",
        "escalate",
        "override_safety_block",
    ):
        decision = await rbac.check(p, action, "case-1")
        assert decision.allow is False, action


@pytest.mark.asyncio
async def test_inspector_role_is_read_only():
    """Spec invariant: inspector can read, never design or adjust."""
    rbac = StaticRBAC(DEFAULT_RBAC)
    p = Principal(id="u-insp", roles=frozenset({"inspector"}))

    assert (await rbac.check(p, "read_weldmap", "c")).allow is True
    assert (await rbac.check(p, "search_standards", "c")).allow is True
    assert (await rbac.check(p, "design_workflow", "c")).allow is False
    assert (await rbac.check(p, "adjust_parameter", "c")).allow is False


@pytest.mark.asyncio
async def test_safety_role_owns_escalation_and_override():
    rbac = StaticRBAC(DEFAULT_RBAC)
    p = Principal(id="u-safety", roles=frozenset({"safety"}))

    assert (await rbac.check(p, "escalate", "c")).allow is True
    assert (await rbac.check(p, "override_safety_block", "c")).allow is True


@pytest.mark.asyncio
async def test_multiple_roles_take_first_grant():
    rbac = StaticRBAC(DEFAULT_RBAC)
    p = Principal(id="u-multi", roles=frozenset({"inspector", "engineer"}))

    decision = await rbac.check(p, "design_workflow", "c")

    assert decision.allow is True
    # Either role string is acceptable — only `engineer` actually grants
    # `design_workflow`, but `inspector` first iteration is also fine.
    assert "engineer" in decision.reason


@pytest.mark.asyncio
async def test_custom_grants_are_honoured():
    """Phase 2 will pass OPA decisions; Phase 1 supports custom dicts."""
    custom = {"qa": frozenset({"export_report"})}
    rbac = StaticRBAC(custom)
    p = Principal(id="u-qa", roles=frozenset({"qa"}))

    assert (await rbac.check(p, "export_report", "c")).allow is True
    assert (await rbac.check(p, "design_workflow", "c")).allow is False


def test_default_rbac_covers_three_spec_roles():
    """Spec defines three Phase-1 roles: inspector, engineer, safety."""
    assert set(DEFAULT_RBAC.keys()) == {"inspector", "engineer", "safety"}
