"""ToolPolicy — per-tool execution policy with wildcard + per-tool override.

Source: 7-plane redesign spec §9 ToolPolicy.
WeldEvent own design — Cline uses category-level booleans, not per-tool rules.
"""

from dataclasses import dataclass
from typing import Any

from cognitiveplane.shared.dto_context import ContextSnapshot


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
        "*": ToolRule(enabled=True, auto_approve=False),
        "search_standards": ToolRule(enabled=True, auto_approve=True),
        "search_cases": ToolRule(enabled=True, auto_approve=True),
        "search_process": ToolRule(enabled=True, auto_approve=True),
        "read_weldmap": ToolRule(enabled=True, auto_approve=True),
        "search_memory": ToolRule(enabled=True, auto_approve=True),
        "explain_decision": ToolRule(enabled=True, auto_approve=True),
        "design_workflow": ToolRule(enabled=True, auto_approve=False, require_reason=True),
        "adjust_parameter": ToolRule(enabled=True, auto_approve=False, require_reason=True, max_calls_per_session=5),
        "request_confirmation": ToolRule(enabled=True, auto_approve=True),
        "escalate": ToolRule(enabled=True, auto_approve=False, require_reason=True),
        "archive_memory": ToolRule(enabled=True, auto_approve=True),
    }

    def __init__(self, overrides: dict[str, ToolRule] | None = None) -> None:
        self._rules = {**self.DEFAULT_RULES, **(overrides or {})}
        self._call_counts: dict[str, dict[str, int]] = {}  # session_id -> tool_name -> count

    def get_rule(self, tool_name: str) -> ToolRule:
        """Get effective rule for a tool. Per-tool overrides wildcard."""
        return self._rules.get(tool_name, self._rules["*"])

    async def request_approval(
        self, tool_name: str, arguments: dict, context: ContextSnapshot
    ) -> ToolApprovalResult:
        """Request human approval for a tool call that requires it."""
        rule = self.get_rule(tool_name)
        if rule.auto_approve:
            return ToolApprovalResult(approved=True)

        # Check reason requirement
        if rule.require_reason and not arguments.get("reason"):
            return ToolApprovalResult(
                approved=False,
                reason=f"Tool {tool_name} requires a reason",
            )

        # Check max calls per session
        if rule.max_calls_per_session is not None:
            session_id = getattr(context, "session_id", "default")
            if session_id not in self._call_counts:
                self._call_counts[session_id] = {}
            count = self._call_counts[session_id].get(tool_name, 0)
            if count >= rule.max_calls_per_session:
                return ToolApprovalResult(
                    approved=False,
                    reason=f"Tool {tool_name} exceeded max calls ({rule.max_calls_per_session}) per session",
                )
            self._call_counts[session_id][tool_name] = count + 1

        # Default: auto-approve for now (ApprovalService integration in Phase 1f)
        return ToolApprovalResult(approved=True)
