"""Canonical port surface tests (spec §4 lines 546-553).

The spec requires ReasoningPort/PlanningPort/ReflectionPort/SubAgentDelegationPort
to live under `control/ports.py`, ValidationPipelinePort/ApprovalServicePort/
HumanReviewRequestRepository under `governance/ports.py`, and the Memory
ports under `memory/ports.py`. These tests pin the surface so future
`shared/ports/*` deletions cannot regress the canonical home.
"""

from __future__ import annotations

import inspect


def test_control_ports_exposes_spec_mandated_abcs():
    from cognitiveplane.control import ports as control_ports

    for name in (
        "BrainDecisionRepository",
        "BrainEventConsumerPort",
        "BrainHealthPort",
        "ReasoningPort",
        "PlanningPort",
        "ReflectionPort",
        "SubAgentDelegationPort",
    ):
        cls = getattr(control_ports, name, None)
        assert cls is not None, f"control/ports.py is missing {name}"
        assert inspect.isclass(cls)


def test_governance_ports_exposes_spec_mandated_abcs():
    from cognitiveplane.governance import ports as governance_ports

    for name in (
        "ValidationPipelinePort",
        "ApprovalServicePort",
        "HumanReviewRequestRepository",
        "ValidationResultRepository",
    ):
        cls = getattr(governance_ports, name, None)
        assert cls is not None, f"governance/ports.py is missing {name}"
        assert inspect.isclass(cls)


def test_memory_ports_exposes_spec_mandated_abcs():
    from cognitiveplane.memory import ports as memory_ports

    for name in (
        "MemoryReadPort",
        "MemoryWritePort",
        "MemorySearchPort",
        "LearningEventWriterPort",
    ):
        cls = getattr(memory_ports, name, None)
        assert cls is not None, f"memory/ports.py is missing {name}"
        assert inspect.isclass(cls)
