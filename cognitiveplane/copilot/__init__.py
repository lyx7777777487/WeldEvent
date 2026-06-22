"""Industrial Copilot — B-level cognitive assistance (spec §11).

Five sub-modules support different B-level copilot capabilities:
- CopilotQA          : Standard / process / case Q&A
- CopilotExplain     : Decision explanation + reasoning trace
- CopilotInvestigate : Anomaly root cause analysis
- CopilotGovern      : Approval / compliance assistance
- CopilotOperate     : Production line operation guidance
"""

from cognitiveplane.copilot.qa import CopilotQA, QAQuery, QAResponse
from cognitiveplane.copilot.explain import CopilotExplain, ExplanationResult
from cognitiveplane.copilot.investigate import (
    CopilotInvestigate,
    InvestigationResult,
)
from cognitiveplane.copilot.govern import CopilotGovern, GovernanceSummary
from cognitiveplane.copilot.operate import CopilotOperate, OperationGuidance

__all__ = [
    "CopilotQA",
    "QAQuery",
    "QAResponse",
    "CopilotExplain",
    "ExplanationResult",
    "CopilotInvestigate",
    "InvestigationResult",
    "CopilotGovern",
    "GovernanceSummary",
    "CopilotOperate",
    "OperationGuidance",
]
