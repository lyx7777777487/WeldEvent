"""Session data subclasses for stateful interaction modes.

Source: L1-Interaction-Layer-Business-Requirements.md §3.3, §3.5.
"""

from src.interaction.base import BaseSessionData
from src.shared.enums import DesignPhase, InterventionGranularity
from src.shared.types import SessionId


class DesignSessionData(BaseSessionData):
    """Workflow design mode session data."""

    objective: str = ""
    requirements: list[str] = []
    material: str | None = None
    thickness: float | None = None
    standard: str | None = None
    joint_type: str | None = None
    design_phase: DesignPhase = DesignPhase.COLLECTING
    generated_plan: str | None = None


class InterventionSessionData(BaseSessionData):
    """Intervention mode session data."""

    target_image_id: str | None = None
    target_image_index: int | None = None
    target_cp: str | None = None
    intervention_granularity: InterventionGranularity = InterventionGranularity.IMAGE
    requested_change: str = ""
    resolution: str = "PENDING"
