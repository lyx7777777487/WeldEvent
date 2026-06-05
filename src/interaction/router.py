"""SessionRouter — table-lookup dispatch from classified intent to mode.

Source: L1-Interaction-Layer-Business-Requirements.md §5.
"""

from src.interaction.base import ModeProtocol, ModeResponse, ResponseType, UserMessage
from src.interaction.context import ContextResolver
from src.interaction.dependencies import PortProvider
from src.interaction.registry import ModeRegistry
from src.shared.types import SessionId
from uuid import uuid4


class SessionRouter:
    """Routes classified UserMessage to the appropriate Mode.

    Dispatch strategy:
    1. If message.intent_label matches a registered mode_id → direct dispatch
    2. If no match, try keyword matching via registry
    3. If still no match → return error response

    The router is NEVER modified when adding new modes.
    """

    def __init__(
        self,
        registry: ModeRegistry,
        port_provider: PortProvider,
        context_resolver: ContextResolver,
    ) -> None:
        self._registry = registry
        self._port_provider = port_provider
        self._context_resolver = context_resolver

    async def route(self, message: UserMessage) -> ModeResponse:
        """Route a classified message to the appropriate mode."""
        # 1. Direct mode_id match from intent_label
        mode = self._resolve_mode(message)
        if mode is None:
            return ModeResponse(
                mode_id="system",
                session_id=SessionId(value=uuid4()),
                response_type=ResponseType.ERROR,
                text_reply=f"无法识别意图: {message.intent_label or message.raw_text}",
            )

        # 2. Build dependencies for this mode
        deps = self._port_provider.build(mode.required_ports)

        # 3. Dispatch
        return await mode.handle(message, deps)

    def _resolve_mode(self, message: UserMessage) -> ModeProtocol | None:
        """Resolve the target mode from the message."""
        # Direct match
        if message.intent_label:
            mode = self._registry.get(message.intent_label)
            if mode is not None:
                return mode

        # Keyword fallback
        matches = self._registry.modes_matching_intent(message.intent_label or message.raw_text)
        if matches:
            return matches[0]

        return None
