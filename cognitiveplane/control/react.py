"""ReActEngine — backward-compatibility shim.

The monolithic ``control/react.py`` (2728 lines) has been decomposed into the
``control/engine/`` package with focused sibling modules:

  - ``engine/react``           : core ReAct loop (ReActEngine, InteractionTier, ...)
  - ``engine/approval``        : human-in-the-loop approval gate
  - ``engine/session_notes``   : per-iteration context notes derivation
  - ``engine/system_prompt``   : worldview injection + system prompt assembly
  - ``engine/tool_execution``  : tool validation / execution / hooks / ratchet

This file re-exports the full public surface so that existing
``from cognitiveplane.control.react import ...`` calls keep working with zero
changes. New code should import from ``cognitiveplane.control.engine`` directly.

Note: ``TOOL_TIMEOUT_SECONDS`` is re-exported here for backward compatibility,
but it is *defined* in ``engine.session_notes``. Code that needs to monkeypatch
this constant at runtime (e.g. tests) should target
``cognitiveplane.control.engine.session_notes`` directly, because the lazy
imports inside ``engine.tool_execution`` read from that module.
"""

from cognitiveplane.control.engine import (  # noqa: F401
    # Core loop
    ReActEngine,
    InteractionTier,
    InteractionResponse,
    EventCallback,
    TIER_A_TOOLS,
    TIER_B_TOOLS,
    MAX_TIER_A_RETRIES,
    # Approval gate
    ApprovalRequest,
    ApprovalStore,
    APPROVAL_REQUIRED_TOOLS,
    APPROVAL_TIMEOUT_SECONDS,
    summarize_for_approval,
    # Session notes
    TOOL_TIMEOUT_SECONDS,
    derive_note,
    derive_failure_reflection,
    build_progress_note,
    # System prompt
    SystemPromptBuilder,
    WELDEVENT_MD_PATH,
    OPERATOR_MD_PATH,
    # Tool execution
    HookRunner,
    RatchetRecorder,
    ToolCallValidator,
    ToolExecutor,
)

__all__ = [
    "ReActEngine",
    "InteractionTier",
    "InteractionResponse",
    "EventCallback",
    "TIER_A_TOOLS",
    "TIER_B_TOOLS",
    "MAX_TIER_A_RETRIES",
    "ApprovalRequest",
    "ApprovalStore",
    "APPROVAL_REQUIRED_TOOLS",
    "APPROVAL_TIMEOUT_SECONDS",
    "summarize_for_approval",
    "TOOL_TIMEOUT_SECONDS",
    "derive_note",
    "derive_failure_reflection",
    "build_progress_note",
    "SystemPromptBuilder",
    "WELDEVENT_MD_PATH",
    "OPERATOR_MD_PATH",
    "HookRunner",
    "RatchetRecorder",
    "ToolCallValidator",
    "ToolExecutor",
]
