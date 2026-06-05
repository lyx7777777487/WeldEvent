"""Mode Registry — runtime registration of interaction modes.

Source: L1-Interaction-Layer-Business-Requirements.md §5.
"""

from src.interaction.base import ModeProtocol


class ModeRegistry:
    """Central registry for all interaction modes.

    Modes register via `registry.register(mode_class)` or `@registry.register`.
    SessionRouter dispatches by looking up mode_id in the registry.
    """

    def __init__(self) -> None:
        self._modes: dict[str, ModeProtocol] = {}

    def register(self, mode_class: type) -> type:
        """Register a mode class. Can be used as a decorator.

        Usage:
            @mode_registry.register
            class MyMode(ModeProtocol): ...

        Or:
            mode_registry.register(MyMode)
        """
        instance = mode_class()
        self._modes[instance.mode_id] = instance
        return mode_class

    def get(self, mode_id: str) -> ModeProtocol | None:
        """Look up a mode by its mode_id."""
        return self._modes.get(mode_id)

    def all_modes(self) -> dict[str, ModeProtocol]:
        """Return all registered modes."""
        return dict(self._modes)

    def modes_matching_intent(self, intent_label: str) -> list[ModeProtocol]:
        """Return modes whose intent_patterns contain the given label."""
        results = []
        for mode in self._modes.values():
            for pattern in mode.intent_patterns:
                if intent_label in pattern.patterns:
                    results.append(mode)
                    break
        return results


# Global singleton
mode_registry = ModeRegistry()
