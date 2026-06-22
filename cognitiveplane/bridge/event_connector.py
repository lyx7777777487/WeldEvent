"""Bridge — EventConnector (spec §6.1 lines 336-337).

Subscribes to the WeldMap decision domain and pushes each published
`BrainDecision` through the translator + launcher chain. Phase 1 is
synchronous + in-process; Phase 2d switches the inbound side to a
NATS JetStream consumer without changing the public contract.
"""

from __future__ import annotations

import logging
from typing import Any

from cognitiveplane.shared.dto_decision import BrainDecision

from .decision_translator import DecisionTranslator, GENERIC_KIND
from .workflow_launcher import WorkflowLaunchResult, WorkflowLauncher

logger = logging.getLogger(__name__)


class EventConnector:
    """Glue layer: Decision → Template → Launcher.

    Filters which decisions actually launch a workflow. Phase 1
    launches every decision whose primary output declares a non-generic
    `type`; Phase 2 grows a more nuanced policy (e.g. only PLANNER
    persona output triggers launch).
    """

    def __init__(
        self,
        translator: DecisionTranslator,
        launcher: WorkflowLauncher,
    ) -> None:
        self._translator = translator
        self._launcher = launcher

    async def on_decision_published(
        self, decision: BrainDecision
    ) -> WorkflowLaunchResult | None:
        if not self._should_launch(decision):
            logger.debug(
                "skip workflow launch for decision %s — no actionable kind",
                decision.decision_id,
            )
            return None
        template = self._translator.translate(decision)
        return await self._launcher.launch(template)

    @staticmethod
    def _should_launch(decision: BrainDecision) -> bool:
        outputs: list[Any] = getattr(decision, "outputs", None) or []
        if not outputs:
            return False
        first = outputs[0]
        content = getattr(first, "content", first)
        kind = getattr(content, "type", None) or getattr(content, "kind", None)
        return bool(kind) and kind != GENERIC_KIND


__all__ = ["EventConnector"]
