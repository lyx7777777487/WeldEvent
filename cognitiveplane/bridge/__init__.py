"""L1↔L2 Bridge — Cognitive Plane to Temporal Control Plane.

boundary-pinning §6.2 重写（2026-06-26）：
  废弃 legacy `BrainDecision → WorkflowTemplate` 管线，
  改为 `WorkflowSpec → temporal_client.start_workflow(RunWorkflowSpec)`。

新管线:
    design_workflow 工具 ──WorkflowSpec──▶ EventConnector
                                          │
                                          ▼
                                    WorkflowLauncher
                                    WorkflowSpec → Temporal
                                          │
                                          ▼
                                   RunWorkflowSpec (L2)
                                   generic DAG runner

Phase 2 实现:
  - EventConnector 直接接收 WorkflowSpec（无 BrainDecision 翻译）
  - WorkflowLauncher 默认 _NullWorkflowLaunchPort（降级）
  - 生产环境装配 TemporalWorkflowLaunchPort（app.py 注入）
  - decision_translator.py 保留为 legacy 文件，不再导出

Source: boundary-pinning §6.2 + §5 WorkflowSpec DTO
"""

from cognitiveplane.bridge.event_connector import EventConnector
from cognitiveplane.bridge.temporal_client import TemporalWorkflowLaunchPort
from cognitiveplane.bridge.workflow_launcher import (
    WorkflowLauncher,
    WorkflowLaunchPort,
    WorkflowLaunchResult,
)

__all__ = [
    "EventConnector",
    "TemporalWorkflowLaunchPort",
    "WorkflowLauncher",
    "WorkflowLaunchPort",
    "WorkflowLaunchResult",
]
