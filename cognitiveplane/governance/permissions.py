"""Governance plane — RBAC permissions (spec §骨架 line 892).

Phase 1 stub: in-memory role→permission mapping with a `PermissionPort`
ABC ready for Phase 2's OPA (Open Policy Agent) integration. The same
contract is consumed by `tool_policy.ToolPolicy` for fine-grained tool
access decisions and by `governance/api` routers for RBAC checks.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    """A user / agent / service requesting access."""

    id: str
    roles: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class PermissionDecision:
    allow: bool
    reason: str = ""


class PermissionPort(ABC):
    """Inbound port — Brain asks the Governance plane for an allow/deny."""

    @abstractmethod
    async def check(
        self,
        principal: Principal,
        action: str,
        resource: str,
    ) -> PermissionDecision: ...


class StaticRBAC(PermissionPort):
    """Phase 1 implementation — static role→action mapping.

    Phase 2 (`adapters/opa/`) will swap this for an OPA-backed adapter
    without touching the `PermissionPort` callers.
    """

    def __init__(self, role_grants: dict[str, frozenset[str]]) -> None:
        self._grants = role_grants

    async def check(
        self,
        principal: Principal,
        action: str,
        resource: str,
    ) -> PermissionDecision:
        for role in principal.roles:
            if action in self._grants.get(role, frozenset()):
                return PermissionDecision(
                    allow=True, reason=f"granted by role={role}"
                )
        return PermissionDecision(
            allow=False,
            reason=f"no role of {sorted(principal.roles)} grants {action!r}",
        )


# Phase 1 default grants — the bare-minimum tool surface needed for
# operators to use the Copilot. Operators with the `inspector` role can
# only read; `engineer` covers the design + adjust loop; `safety` is
# the only role allowed to escalate or override safety blocks.
DEFAULT_RBAC: dict[str, frozenset[str]] = {
    "inspector": frozenset({"read_weldmap", "search_standards", "search_cases"}),
    "engineer": frozenset(
        {
            "read_weldmap",
            "search_standards",
            "search_cases",
            "search_process",
            "design_workflow",
            "adjust_parameter",
            "request_confirmation",
            "explain_decision",
            "archive_memory",
        }
    ),
    "safety": frozenset({"escalate", "override_safety_block"}),
}


__all__ = [
    "DEFAULT_RBAC",
    "PermissionDecision",
    "PermissionPort",
    "Principal",
    "StaticRBAC",
]
