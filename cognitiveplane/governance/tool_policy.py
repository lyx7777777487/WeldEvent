"""ToolPolicy — per-tool execution policy with wildcard + per-tool override.

Source: 7-plane redesign spec §9 ToolPolicy.
WeldEvent own design — Cline uses category-level booleans, not per-tool rules.
"""

from dataclasses import dataclass
from typing import Any

from cognitiveplane.shared.dto.context import ContextSnapshot


@dataclass
class ToolRule:
    """Per-tool execution policy."""
    enabled: bool = True
    auto_approve: bool = False
    require_reason: bool = False
    max_calls_per_session: int | None = None


@dataclass
class ToolApprovalRequest:
    tool_name: str
    arguments: dict
    context: ContextSnapshot
    rule: ToolRule


@dataclass
class ToolApprovalResult:
    approved: bool
    reason: str | None = None


class ToolPolicy:
    """Centralized tool execution policy with wildcard + per-tool override."""

    DEFAULT_RULES: dict[str, ToolRule] = {
        # Wildcard fallback — plan §4.2: unlisted tools default to require approval
        "*": ToolRule(enabled=True, auto_approve=False),
        # ── Information-fetching (Tier-A, plan §4.2 line 749-755) ──
        "web_search": ToolRule(enabled=True, auto_approve=True),
        "search_standards": ToolRule(enabled=True, auto_approve=True),
        "search_cases": ToolRule(enabled=True, auto_approve=True),
        "search_process": ToolRule(enabled=True, auto_approve=True),
        "read_weldmap": ToolRule(enabled=True, auto_approve=True),
        "explain_decision": ToolRule(enabled=True, auto_approve=True),
        "archive_memory": ToolRule(enabled=True, auto_approve=True),
        # ── Self-management (plan §4.2 line 756-758) ──
        "manage_plan": ToolRule(enabled=True, auto_approve=True),
        # ── Human-interaction (plan §4.2 line 759) ──
        "request_confirmation": ToolRule(enabled=True, auto_approve=True),
        # ── Tier-B: require reason (plan §4.2 line 760-762) ──
        "design_workflow": ToolRule(enabled=True, auto_approve=False, require_reason=True),
        "adjust_parameter": ToolRule(enabled=True, auto_approve=False, require_reason=True, max_calls_per_session=5),
        "escalate": ToolRule(enabled=True, auto_approve=False, require_reason=True),
    }

    def __init__(self, overrides: dict[str, ToolRule] | None = None) -> None:
        self._rules = {**self.DEFAULT_RULES, **(overrides or {})}
        self._call_counts: dict[str, dict[str, int]] = {}  # session_id -> tool_name -> count

    def get_rule(self, tool_name: str) -> ToolRule:
        """Get effective rule for a tool. Per-tool overrides wildcard."""
        return self._rules.get(tool_name, self._rules["*"])

    async def request_approval(
        self, tool_name: str, arguments: dict, context: ContextSnapshot, session_id: str = "default"
    ) -> ToolApprovalResult:
        """Request human approval for a tool call that requires it.

        session_id: real per-session identifier (from ReActEngine.run session param).
            Required for max_calls_per_session to work — ContextSnapshot has no
            session_id field, so caller must pass it explicitly.
        """
        rule = self.get_rule(tool_name)
        if rule.auto_approve:
            return ToolApprovalResult(approved=True)

        # Check reason requirement
        if rule.require_reason and not arguments.get("reason"):
            return ToolApprovalResult(
                approved=False,
                reason=f"Tool {tool_name} requires a reason",
            )

        # Check max calls per session (plan §4.2 line 761: adjust_parameter 每 session 最多 5 次)
        if rule.max_calls_per_session is not None:
            if session_id not in self._call_counts:
                self._call_counts[session_id] = {}
            count = self._call_counts[session_id].get(tool_name, 0)
            if count >= rule.max_calls_per_session:
                return ToolApprovalResult(
                    approved=False,
                    reason=f"Tool {tool_name} exceeded max calls ({rule.max_calls_per_session}) per session",
                )
            self._call_counts[session_id][tool_name] = count + 1

        # Default: auto-approve for now (ApprovalService integration in Phase 4 per plan §A.11)
        return ToolApprovalResult(approved=True)
