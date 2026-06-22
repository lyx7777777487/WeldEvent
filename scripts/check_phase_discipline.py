#!/usr/bin/env python3
"""Phase discipline checker — scans diff for imports that jump ahead of the
current plan phase.

Rules:
- Reads current phase from --phase arg, then WELDEVENT_PHASE env, default 2.
- Scans .py files in the diff for `from cognitiveplane.X import Y` / `import cognitiveplane.X`.
- Looks up X's phase tag in PHASE_MAP (sourced from plan §7 file structure).
- If import target's phase > current phase AND target file is NOT grandfathered
  (already exists on disk) → exit 1 (new phase overreach).
- If import target's phase > current phase AND target IS grandfathered → warn
  only (acknowledged historical debt).
- Grandfathered set = all cognitiveplane/*.py files present at script startup.

Diff base:
- Default: origin/phase2-multimodal-vision (CI mode)
- --cached: staged changes only (pre-commit / local self-check)

Usage:
    python scripts/check_phase_discipline.py                          # CI mode
    python scripts/check_phase_discipline.py --phase 3                # explicit phase
    WELDEVENT_PHASE=3 python scripts/check_phase_discipline.py        # env
    python scripts/check_phase_discipline.py --cached                 # local pre-commit

CI integration (GitHub Actions):

    # .github/workflows/phase-discipline.yml
    name: phase-discipline
    on: [pull_request]
    jobs:
      check:
        runs-on: ubuntu-latest
        steps:
          - uses: actions/checkout@v4
            with: { fetch-depth: 0 }
          - uses: actions/setup-python@v5
            with: { python-version: '3.11' }
          - run: pip install -e .
          - run: python scripts/check_phase_discipline.py
            env:
              WELDEVENT_PHASE: 2

Pre-commit hook:

    # .pre-commit-config.yaml
    repos:
      - repo: local
        hooks:
          - id: phase-discipline
            name: phase discipline
            entry: python scripts/check_phase_discipline.py --cached
            language: system
            pass_filenames: false
            stages: [commit]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
COGNITIVEPLANE = REPO_ROOT / "cognitiveplane"

DEFAULT_BASE = "origin/phase2-multimodal-vision"
DEFAULT_PHASE = 2

IMPORT_RE = re.compile(
    r"^\s*(?:from\s+(cognitiveplane[\w.]*)\s+import\s+\S+"
    r"|import\s+(cognitiveplane[\w.]*))",
    re.MULTILINE,
)

PHASE_MAP: dict[str, int] = {
    # ─── Phase 2: LLM 看图说话 (skeleton + chat path) ───
    # Plan §11.4 验收 2.4 ban list: mcp_* / agent_loop / switch_role / request_image_detail
    "cognitiveplane.interaction.api": 2,
    "cognitiveplane.interaction.api.notifications": 3,  # escalate-dependent
    "cognitiveplane.interaction.multimodal": 2,
    "cognitiveplane.interaction.base": 2,
    "cognitiveplane.interaction.context": 2,
    "cognitiveplane.interaction.session": 2,
    "cognitiveplane.interaction.messages": 2,
    "cognitiveplane.interaction.entities": 2,
    "cognitiveplane.interaction.signals": 2,
    "cognitiveplane.interaction.ports": 2,
    "cognitiveplane.interaction.file_handler": 2,
    "cognitiveplane.capability": 2,
    "cognitiveplane.control.react": 2,
    "cognitiveplane.control.tools.analyze_image": 2,
    "cognitiveplane.control.tools.web_search": 2,
    "cognitiveplane.gateway": 2,
    "cognitiveplane.gateway.pipeline": 2,
    "cognitiveplane.gateway.read_gateway": 2,
    "cognitiveplane.gateway.write_gateway": 2,
    "cognitiveplane.gateway.weldmap_client": 2,
    "cognitiveplane.gateway.adapters": 2,
    "cognitiveplane.gateway.ports": 2,
    "cognitiveplane.gateway.services": 2,
    "cognitiveplane.gateway.events": 2,
    "cognitiveplane.governance.validators": 2,
    "cognitiveplane.governance.tool_policy": 2,  # PolicyHook dep, foundational
    "cognitiveplane.governance.exceptions": 2,
    "cognitiveplane.governance.repositories": 2,
    "cognitiveplane.adapters.cache": 2,
    "cognitiveplane.adapters.database": 2,
    "cognitiveplane.adapters.migrations": 2,
    "cognitiveplane.adapters.storage": 2,
    "cognitiveplane.adapters.weldmap": 2,
    "cognitiveplane.adapters.weldmap.weldmap_http": 2,
    "cognitiveplane.adapters.observability": 3,  # metrics basic ok phase 2, tracing phase 3
    "cognitiveplane.adapters.observability.metrics": 2,
    "cognitiveplane.adapters.observability.tracing": 5,  # §A.6 phase 5+
    "cognitiveplane.bridge": 2,
    "cognitiveplane.bridge.decision_translator": 2,
    "cognitiveplane.bridge.event_connector": 2,
    "cognitiveplane.bridge.workflow_launcher": 5,  # §1.7 phase 5+ L2 integration

    # ─── Phase 3: 人机反馈 + 检测/标注 MCP ───
    # Plan §7 lines 1086-1127, §A.1/A.4/A.6
    "cognitiveplane.control.agent_loop": 3,  # §7 line 1087 — proactive guard (file not on disk yet)
    "cognitiveplane.control.mcp_registry": 3,  # §7 line 1091
    "cognitiveplane.control.state_machine": 3,  # §A.1 — starts 3-state, 12-state in phase 5+
    "cognitiveplane.control.event_log": 3,  # §A.6
    "cognitiveplane.control.hooks": 3,  # §7 line 1120
    "cognitiveplane.control.fallback": 3,  # §7 line 1125
    "cognitiveplane.control.supervisor": 3,  # §7 line 1124
    "cognitiveplane.control.decision_factory": 3,  # §A.4 — 1-2 types at phase 3
    "cognitiveplane.control.tools.request_confirmation": 3,  # §7 line 1096
    "cognitiveplane.control.tools.escalate": 3,  # §7 line 1103
    "cognitiveplane.control.tools.read_weldmap": 3,  # §7 line 1111
    "cognitiveplane.control.tools.search_standards": 3,  # §7 line 1108
    "cognitiveplane.control.tools.search_cases": 3,  # §7 line 1109 (corrected from 4)
    "cognitiveplane.governance.escalation": 3,
    "cognitiveplane.governance.permissions": 3,
    "cognitiveplane.knowledge.rag": 3,
    "cognitiveplane.knowledge.standards": 3,
    "cognitiveplane.knowledge.cases": 3,
    "cognitiveplane.memory.compaction": 3,  # §7 line 2046 — basic LastN at phase 3
    "cognitiveplane.governance.mcp_policy": 3,  # §3.6 mcp policy yaml (phase 3 for mcp_*)

    # ─── Phase 4: 多角色 + Memory 成熟 + ReasoningMode ───
    # Plan §7 lines 1097-1104, §A.2/A.8/A.11
    "cognitiveplane.control.roles": 4,  # §7 line 1088 — proactive guard
    "cognitiveplane.control.persona": 4,  # §7 line 1121, §A.2
    "cognitiveplane.control.reasoning_mode": 4,  # §7 line 1122, §A.2
    "cognitiveplane.control.tools.search_process": 4,  # §7 line 1110
    "cognitiveplane.control.tools.request_clarification": 4,  # §7 line 1097
    "cognitiveplane.control.tools.explain_decision": 4,  # §7 line 1102
    "cognitiveplane.control.tools.archive_memory": 4,  # §7 line 1104
    "cognitiveplane.memory.hierarchy": 4,  # §A.2 — M0-M5 with ReasoningMode
    "cognitiveplane.memory.promotion": 4,
    "cognitiveplane.memory.confidence": 4,
    "cognitiveplane.memory.blocks": 4,
    "cognitiveplane.memory.search": 4,
    "cognitiveplane.memory.archive": 4,
    "cognitiveplane.memory.learning": 4,  # §A.2
    "cognitiveplane.memory.context_understanding": 4,  # §7 line 1968 — proactive guard
    "cognitiveplane.knowledge.process": 4,  # §7 line 1110
    "cognitiveplane.knowledge.equipment": 4,
    "cognitiveplane.governance.on_fail": 4,  # §A.8
    "cognitiveplane.governance.approval": 4,  # §A.11
    "cognitiveplane.governance.review": 4,  # §A.11
    "cognitiveplane.governance.collaboration": 4,
    "cognitiveplane.copilot.qa": 4,  # §7 line 1489
    "cognitiveplane.copilot.explain": 4,  # §7 line 1490
    "cognitiveplane.copilot.govern": 4,  # §7 line 1492 (compliance)

    # ─── Phase 5: 多角色自主切换 ───
    # Plan §7 lines 1089/1099-1100/1106, §A.7/A.9
    "cognitiveplane.control.checkpoint": 5,  # §A.7
    "cognitiveplane.control.orchestrator": 5,  # §7 line 1089
    "cognitiveplane.control.tools.switch_role": 5,  # §7 line 1099
    "cognitiveplane.control.tools.manage_plan": 5,  # §7 line 1100
    "cognitiveplane.control.tools.design_workflow": 5,  # §7 line 1106
    "cognitiveplane.control.tools.request_image_detail": 5,  # §7 line 1098
    "cognitiveplane.control.planner": 5,  # §A.5 (conservative; plan says 6+)
    "cognitiveplane.control.reflector": 5,  # §A.5
    "cognitiveplane.control.sub_agent": 5,  # §A.5
    "cognitiveplane.copilot.investigate": 5,  # §7 line 1491
    "cognitiveplane.copilot.operate": 5,  # §7 line 1493
    "cognitiveplane.memory.dual_write": 5,  # §A.9 — Event Sourcing + CAS
    "cognitiveplane.adapters.weldmap.event_sourcing": 5,  # §A.9

    # ─── Phase 6: 知识库主体域 + SubAgent ───
    # Plan §7 lines 1101/1113, §A.5
    "cognitiveplane.control.tools.spawn_investigator": 6,  # §7 line 1101 (corrected from 5)
    "cognitiveplane.control.skills": 6,  # §7 line 1113 — proactive guard
    "cognitiveplane.control.tools.adjust_parameter": 6,  # §7 line 1112 (conservative; plan says 5)
}


def resolve_phase(arg_phase: int | None) -> int:
    if arg_phase is not None:
        return arg_phase
    env = os.environ.get("WELDEVENT_PHASE")
    if env:
        try:
            return int(env)
        except ValueError:
            print(f"WARN: WELDEVENT_PHASE={env!r} not int, falling back to {DEFAULT_PHASE}", file=sys.stderr)
    return DEFAULT_PHASE


def get_diff(base: str | None, cached: bool) -> str:
    if cached:
        result = subprocess.run(
            ["git", "diff", "--cached", "--unified=0", "--", "*.py"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
    else:
        result = subprocess.run(
            ["git", "diff", "--unified=0", "--", "*.py", base] if base
            else ["git", "diff", "--unified=0", "--", "*.py"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
    return result.stdout


def parse_diff_files(diff_text: str) -> Iterable[tuple[str, list[str]]]:
    """Yield (file_path, added_lines) for each .py file in diff."""
    current_file: str | None = None
    added: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            if current_file is not None and current_file.endswith(".py"):
                yield current_file, added
            current_file = line[6:]
            added = []
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    if current_file is not None and current_file.endswith(".py"):
        yield current_file, added


def lookup_phase(module_path: str) -> int | None:
    """Return phase for a cognitiveplane.X.Y module path.

    Tries exact match first, then walks up the path segment by segment.
    Returns None if no prefix matches (e.g. cognitiveplane.shared — always allowed).
    """
    parts = module_path.split(".")
    for i in range(len(parts), 0, -1):
        prefix = ".".join(parts[:i])
        if prefix in PHASE_MAP:
            return PHASE_MAP[prefix]
    return None


def is_grandfathered(module_path: str) -> bool:
    """True if any .py file for this module exists on disk.

    `cognitiveplane.control.tools.switch_role` → check cognitiveplane/control/tools/switch_role.py
    `cognitiveplane.control` → check cognitiveplane/control/__init__.py
    """
    rel = module_path.replace(".", "/")
    candidates = [
        COGNITIVEPLANE.parent / f"{rel}.py",
        COGNITIVEPLANE.parent / rel / "__init__.py",
    ]
    return any(c.exists() for c in candidates)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phase", type=int, default=None, help=f"Current phase (default {DEFAULT_PHASE})")
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"Diff base ref (default {DEFAULT_BASE})")
    parser.add_argument("--cached", action="store_true", help="Check staged changes only (pre-commit mode)")
    args = parser.parse_args()

    current_phase = resolve_phase(args.phase)
    diff_text = get_diff(args.base if not args.cached else None, args.cached)

    errors: list[str] = []
    warns: list[str] = []

    for file_path, added_lines in parse_diff_files(diff_text):
        joined = "\n".join(added_lines)
        for match in IMPORT_RE.finditer(joined):
            module = match.group(1) or match.group(2)
            if not module:
                continue
            target_phase = lookup_phase(module)
            if target_phase is None:
                continue
            if target_phase <= current_phase:
                continue
            location = f"{file_path}"
            if is_grandfathered(module):
                warns.append(
                    f"WARN: {location}: imports {module} (tagged phase {target_phase}, current {current_phase})\n"
                    f"      file exists as acknowledged historical debt — not blocking"
                )
            else:
                errors.append(
                    f"ERROR: {location}: imports {module} (tagged phase {target_phase}, current {current_phase})\n"
                    f"       this is a NEW phase overreach — remove import or bump WELDEVENT_PHASE"
                )

    for w in warns:
        print(w, file=sys.stderr)
    for e in errors:
        print(e, file=sys.stderr)

    if errors:
        print(f"\n{len(errors)} error(s), {len(warns)} warning(s). Phase discipline check FAILED.", file=sys.stderr)
        return 1
    if warns:
        print(f"\n{len(warns)} warning(s), 0 errors. Phase discipline check PASSED with warnings.", file=sys.stderr)
    else:
        print(f"Phase discipline check PASSED (phase {current_phase}).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())