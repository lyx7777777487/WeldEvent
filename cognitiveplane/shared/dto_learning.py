"""Re-export shim — canonical path is now cognitiveplane.shared.dto.learning."""

from cognitiveplane.shared.dto.learning import *  # noqa: F401,F403

__all__ = [
    "LearningContent",
    "LearningEvent",
]
