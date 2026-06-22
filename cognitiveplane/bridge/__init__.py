"""L1↔L2 Bridge — Cognitive Plane to Temporal Control Plane (spec §6.1).

Pipeline:
    CognitiveGateway ──Decision──▶ EventConnector
                                  │
                                  ▼
                        DecisionTranslator
                        BrainDecision → WorkflowTemplate
                                  │
                                  ▼
                          WorkflowLauncher
                          Template → Temporal
                                  │
                                  ▼
                           TemplateWorkflow

Phase 1 ships the contracts + a synchronous in-memory pipeline so the
Cognitive Plane can be exercised end-to-end without a live Temporal
cluster. Phase 2 wires `WorkflowLauncher` to the real Temporal client.
"""

from cognitiveplane.bridge.decision_translator import (
    DecisionTranslator,
    WorkflowTemplate,
)
from cognitiveplane.bridge.event_connector import EventConnector
from cognitiveplane.bridge.workflow_launcher import (
    WorkflowLauncher,
    WorkflowLaunchPort,
    WorkflowLaunchResult,
)

__all__ = [
    "DecisionTranslator",
    "EventConnector",
    "WorkflowLauncher",
    "WorkflowLaunchPort",
    "WorkflowLaunchResult",
    "WorkflowTemplate",
]
