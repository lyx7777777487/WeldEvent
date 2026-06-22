"""Re-export shim — canonical path is now cognitiveplane.shared.dto.persona."""

from cognitiveplane.shared.dto.persona import *  # noqa: F401,F403

__all__ = [
    "PersonaFrame",
    "PersonaSelectionResult",
]
