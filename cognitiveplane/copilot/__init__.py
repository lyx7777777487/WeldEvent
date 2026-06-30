"""Industrial Copilot — B-level cognitive assistance (spec §11).

⚠️ HISTORICAL LEGACY — not wired into the running app (2026-06-23 audit).

The five classes below are a Phase 1 scaffold. They predate the plan §一
redesign and use the WRONG paradigm: each class calls Knowledge/Memory
ports directly and invokes `LLMProvider.complete()` itself, bypassing the
ReAct Loop. This violates the plan's core philosophy (§二 line 272:
"系统唯一的核心流程是 ReAct Loop") and the §一 line 167-170 contract
("Copilot = /chat + 预设 role + 工具白名单" — i.e. Copilot is a preset
ENTRY into ReAct, not a parallel pipeline).

The plan schedules Copilot for 系统阶段 4+ (多角色协作, §A.10 line 2570).
Current phase is 2 (multimodal vision), so this orphan state is correct
for now. When phase 4 lands, do NOT extend these classes — rewrite them
as `/chat` presets that configure ReActEngine with role-specific system
prompt + tool whitelist, per plan §一 line 167-170 and §3.4.

Five sub-modules (Phase 1 design, pending rewrite):
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
