"""ReAct engine package — ReActEngine and supporting components.

Decomposed from the original monolithic ``control/react.py`` (2728 lines) into
focused sibling modules:

  - ``react``           : core ReAct loop (ReActEngine, InteractionTier, ...)
  - ``approval``        : human-in-the-loop approval gate
  - ``session_notes``   : per-iteration context notes derivation
  - ``system_prompt``   : worldview injection + system prompt assembly
  - ``tool_execution``  : tool validation / execution / hooks / ratchet

This package re-exports the public surface so that callers can import either
``from cognitiveplane.control.engine import ReActEngine`` (new path) or
``from cognitiveplane.control.react import ReActEngine`` (legacy shim path).
"""

from cognitiveplane.control.engine.react import (
    ReActEngine,
    InteractionTier,
    InteractionResponse,
    TIER_A_TOOLS,
    TIER_B_TOOLS,
    MAX_TIER_A_RETRIES,
    EventCallback,
)
from cognitiveplane.control.engine.approval import (
    ApprovalRequest,
    ApprovalStore,
    APPROVAL_REQUIRED_TOOLS,
    APPROVAL_TIMEOUT_SECONDS,
    summarize_for_approval,
)
from cognitiveplane.control.engine.session_notes import (
    TOOL_TIMEOUT_SECONDS,
    derive_note,
    derive_failure_reflection,
    build_progress_note,
)
from cognitiveplane.control.engine.system_prompt import (
    SystemPromptBuilder,
    WELDEVENT_MD_PATH,
    OPERATOR_MD_PATH,
)
from cognitiveplane.control.engine.tool_execution import (
    HookRunner,
    RatchetRecorder,
    ToolCallValidator,
    ToolExecutor,
)

__all__ = [
    # Core loop
    "ReActEngine",
    "InteractionTier",
    "InteractionResponse",
    "EventCallback",
    "TIER_A_TOOLS",
    "TIER_B_TOOLS",
    "MAX_TIER_A_RETRIES",
    # Approval gate
    "ApprovalRequest",
    "ApprovalStore",
    "APPROVAL_REQUIRED_TOOLS",
    "APPROVAL_TIMEOUT_SECONDS",
    "summarize_for_approval",
    # Session notes
    "TOOL_TIMEOUT_SECONDS",
    "derive_note",
    "derive_failure_reflection",
    "build_progress_note",
    # System prompt
    "SystemPromptBuilder",
    "WELDEVENT_MD_PATH",
    "OPERATOR_MD_PATH",
    # Tool execution
    "HookRunner",
    "RatchetRecorder",
    "ToolCallValidator",
    "ToolExecutor",
]
