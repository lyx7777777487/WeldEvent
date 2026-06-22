"""L1 Cognitive Plane — Brain-specific exceptions.

Source: L1_Port_and_Contract_Design.md (Phase 4, Brain state machine).
Section 13 Error Handling — per-plane error types.
"""

from cognitiveplane.shared.enums import BrainStateType, BrainTrigger


class InvalidStateTransition(Exception):
    """Raised when a trigger cannot be applied from the current Brain state."""

    def __init__(self, from_state: BrainStateType, trigger: BrainTrigger) -> None:
        self.from_state = from_state
        self.trigger = trigger
        super().__init__(
            f"Invalid transition: cannot apply '{trigger.value}' from state {from_state.value}"
        )


# ---------------------------------------------------------------------------
# Section 13: Per-plane error types
# ---------------------------------------------------------------------------


class InteractionError(Exception):
    """Interaction plane error — 400 + user-friendly message."""


class DecisionTimeoutError(Exception):
    """Control plane error — decision pipeline timed out.

    ROUTINE: retry→fallback; ADAPTIVE: downgrade; EXPLORATORY: notify human.
    """

    def __init__(self, case_id: str, reasoning_mode: str = "ROUTINE") -> None:
        self.case_id = case_id
        self.reasoning_mode = reasoning_mode
        super().__init__(f"Decision timeout for case {case_id} (mode: {reasoning_mode})")


class ReasoningLoopError(Exception):
    """Control plane error — reasoning loop detected.

    COGNITIVE_FALLBACK: memory match replaces LLM.
    """

    def __init__(self, case_id: str, iterations: int) -> None:
        self.case_id = case_id
        self.iterations = iterations
        super().__init__(f"Reasoning loop detected for case {case_id} after {iterations} iterations")


class ValidationBlockError(Exception):
    """Governance plane error — validation forced ESCALATED.

    Force ESCALATED → human intervention.
    """

    def __init__(self, case_id: str, reason: str = "") -> None:
        self.case_id = case_id
        self.reason = reason
        super().__init__(f"Validation block for case {case_id}: {reason}")


class EscalationOverflowError(Exception):
    """Governance plane error — escalation counter exceeded threshold.

    HUMAN_INTERVENTION required.
    """

    def __init__(self, case_id: str, consecutive_critical: int) -> None:
        self.case_id = case_id
        self.consecutive_critical = consecutive_critical
        super().__init__(
            f"Escalation overflow for case {case_id}: {consecutive_critical} consecutive criticals"
        )


class WeldMapUnavailableError(Exception):
    """Gateway plane error — WeldMap unavailable.

    Retry 3x → degrade to local cache → AuditEvent.
    """

    def __init__(self, operation: str, retries: int = 3) -> None:
        self.operation = operation
        self.retries = retries
        super().__init__(f"WeldMap unavailable after {retries} retries for operation: {operation}")


class LLMUnavailableError(Exception):
    """Capability plane error — LLM unavailable.

    Degrade to keyword mode + notify operator.
    """

    def __init__(self, provider: str = "unknown") -> None:
        self.provider = provider
        super().__init__(f"LLM provider unavailable: {provider}")


class VisionUnavailableError(LLMUnavailableError):
    """Vision model endpoint unavailable — 404/400/invalid endpoint.

    The configured vision endpoint does not exist, is not activated, or
    is bound to a non-vision model. LLM should degrade to text-only
    conversation. Not retriable without config change.
    """

    def __init__(self, detail: str = "", provider: str = "vision") -> None:
        self.detail = detail
        super().__init__(provider=provider)
        self.args = (f"Vision endpoint unavailable: {detail}" if detail else "Vision endpoint unavailable",)


class VisionTransientError(Exception):
    """Vision model transient failure — network/timeout/5xx.

    Retriable. Caller may retry with backoff or fall back to text mode.
    """

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(f"Vision transient error: {detail}" if detail else "Vision transient error")


class InvalidImageError(Exception):
    """Image format/size rejected by vision model — 400 image-related.

    Not retriable with same input. Caller should request a different image
    or inform the user.
    """

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(f"Invalid image: {detail}" if detail else "Invalid image")


class CheckpointNotFoundError(Exception):
    """Raised when a checkpoint_id does not exist."""

    def __init__(self, checkpoint_id: str) -> None:
        self.checkpoint_id = checkpoint_id
        super().__init__(f"Checkpoint not found: {checkpoint_id}")

