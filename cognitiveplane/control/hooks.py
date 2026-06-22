"""BeforeTool hooks — ALLOW/DENY only, no MODIFY.

Source: 7-plane redesign spec §5 hooks.
Verified against OpenHands PreToolUse (hooks/types.py) and Cline hooks.
Neither framework supports argument modification.
"""

from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass

from cognitiveplane.shared.dto_context import ContextSnapshot
from cognitiveplane.shared.enums import SafetyStatus


class HookDecision(Enum):
    """Hook verdict. Only ALLOW/DENY — neither OpenHands nor Cline supports argument modification."""
    ALLOW = "allow"
    DENY = "deny"


@dataclass
class HookResult:
    decision: HookDecision
    reason: str | None = None


class BeforeToolHook(ABC):
    @abstractmethod
    async def before_execute(
        self, tool_name: str, arguments: dict, context: ContextSnapshot
    ) -> HookResult: ...


class SafetyHook(BeforeToolHook):
    """Block dangerous operations based on safety status."""

    async def before_execute(
        self, tool_name: str, arguments: dict, context: ContextSnapshot
    ) -> HookResult:
        if tool_name == "adjust_parameter":
            safety_status = getattr(context, "safety_status", None)
            if safety_status == SafetyStatus.BLOCK:
                return HookResult(
                    decision=HookDecision.DENY,
                    reason="Safety BLOCK — parameter changes forbidden",
                )
        return HookResult(decision=HookDecision.ALLOW)


class PolicyHook(BeforeToolHook):
    """Enforce ToolPolicy. Denied tools produce RejectionObservation."""

    def __init__(self, policy: "ToolPolicy") -> None:
        self._policy = policy

    async def before_execute(
        self, tool_name: str, arguments: dict, context: ContextSnapshot
    ) -> HookResult:
        rule = self._policy.get_rule(tool_name)
        if not rule.enabled:
            return HookResult(
                decision=HookDecision.DENY,
                reason=f"Tool {tool_name} disabled by policy",
            )
        if rule.auto_approve:
            return HookResult(decision=HookDecision.ALLOW)
        # Needs approval — route to ApprovalService
        approval = await self._policy.request_approval(tool_name, arguments, context)
        if not approval.approved:
            return HookResult(
                decision=HookDecision.DENY,
                reason=approval.reason or "Approval denied",
            )
        return HookResult(decision=HookDecision.ALLOW)
