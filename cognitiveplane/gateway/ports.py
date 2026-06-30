"""CognitiveGateway ports — Brain's only interface to WeldMap.

Source: 7-plane redesign spec §6 CognitiveGateway.
"""

from abc import ABC, abstractmethod

from cognitiveplane.shared.dto.context import WeldMapSnapshot
from cognitiveplane.shared.dto_decision import BrainDecision
from cognitiveplane.shared.dto.gateway import (
    CaseData,
    Escalation,
    PublishResult,
    WorkflowState,
)
from cognitiveplane.shared.types import CaseId
from cognitiveplane.interaction.signals.instruction import LiveInstruction


class CognitiveGatewayWritePort(ABC):
    """Brain's only write interface to WeldMap.

    Writes to WeldMap decision/negotiation domains only.
    Must NOT write to execution result domains (image/mask/annotation/rendering).
    """

    @abstractmethod
    async def publish_decision(self, decision: BrainDecision) -> PublishResult: ...

    @abstractmethod
    async def publish_escalation(self, escalation: Escalation) -> PublishResult: ...

    @abstractmethod
    async def publish_instruction(self, instruction: LiveInstruction) -> PublishResult: ...

    @abstractmethod
    async def notify_workflow_trigger(self, case_id: CaseId, workflow_config: dict) -> None:
        """Notify L2 Control Plane to start a Temporal workflow.

        Fix P1-5: writes to WeldMap workflow domain → Temporal listens for Signal.
        """
        ...


class CognitiveGatewayReadPort(ABC):
    """Brain's read interface to WeldMap.

    Read-only access to WeldMap domains:
    - workflow: pipeline status, step progress
    - image: image metadata (not pixel data)
    - annotation: measurement results
    - decision: previous decisions for this case
    - negotiation: escalation history
    """

    @abstractmethod
    async def read_weldmap_snapshot(self, case_id: CaseId) -> WeldMapSnapshot: ...

    @abstractmethod
    async def read_workflow_state(self, case_id: CaseId) -> WorkflowState | None: ...

    @abstractmethod
    async def read_case_data(self, case_id: CaseId) -> CaseData | None: ...
