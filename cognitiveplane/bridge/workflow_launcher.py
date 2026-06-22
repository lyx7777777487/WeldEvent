"""Bridge — WorkflowLauncher (spec §6.1 lines 348-350).

Submits a translated `WorkflowTemplate` to the L2 Temporal control
plane. The Cognitive Plane never imports the Temporal SDK directly;
all Temporal-specific code lives behind `WorkflowLaunchPort`, which
Phase 2 implements via `executionplane`.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .decision_translator import WorkflowTemplate


@dataclass(frozen=True)
class WorkflowLaunchResult:
    workflow_id: str
    run_id: str
    template_id: str
    accepted: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkflowLaunchPort(ABC):
    """Outbound port — `executionplane` adapts this to Temporal's client."""

    @abstractmethod
    async def submit(self, template: WorkflowTemplate) -> WorkflowLaunchResult: ...


class WorkflowLauncher:
    """Phase 1: minimal launcher that delegates to a pluggable port.

    Defaults to an in-memory adapter so unit tests can wire the bridge
    without standing up Temporal. Production composition replaces the
    adapter inside `_build_dependencies`.
    """

    def __init__(self, port: WorkflowLaunchPort | None = None) -> None:
        self._port = port or _InMemoryWorkflowLaunchPort()

    async def launch(self, template: WorkflowTemplate) -> WorkflowLaunchResult:
        return await self._port.submit(template)


class _InMemoryWorkflowLaunchPort(WorkflowLaunchPort):
    """Default test/dev adapter — accepts every template, records nothing."""

    async def submit(self, template: WorkflowTemplate) -> WorkflowLaunchResult:
        return WorkflowLaunchResult(
            workflow_id=f"wf-{uuid.uuid4().hex[:8]}",
            run_id=f"run-{uuid.uuid4().hex[:8]}",
            template_id=template.template_id,
            accepted=True,
            metadata={"adapter": "in-memory"},
        )


__all__ = ["WorkflowLaunchPort", "WorkflowLaunchResult", "WorkflowLauncher"]
