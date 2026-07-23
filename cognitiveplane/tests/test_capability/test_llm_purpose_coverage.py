"""Guard test: every production LLMRequest construction carries a non-empty purpose.

Source: Step 5.3 (cache边界) - Op 36 LLM cost/observability.
`purpose` drives (1) per-purpose model routing (LLMConfig.resolve_model) and
(2) call analytics (LLMCallTracker.query_by_purpose). An untagged LLM call is
invisible to cost accounting and may route to the wrong model, so this invariant
must not silently regress.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCAN_DIRS = ["cognitiveplane", "controlplane"]
# LLMRequest call sites that are not real constructions.
_SKIP_FILES = {
    # class definition lives here, not a construction
    "cognitiveplane/capability/provider.py",
}


def _extract_call_block(text: str, open_paren_idx: int) -> str:
    """Return substring from '(' at open_paren_idx to its matching ')'."""
    depth = 0
    i = open_paren_idx
    in_str: str | None = None
    while i < len(text):
        c = text[i]
        if in_str is not None:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c in ("'", '"'):
            in_str = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren_idx : i + 1]
        i += 1
    return text[open_paren_idx:]


def _line_prefix(text: str, idx: int) -> str:
    line_start = text.rfind("\n", 0, idx) + 1
    return text[line_start:idx]


def _discover_call_sites() -> list[tuple[Path, int, str]]:
    sites: list[tuple[Path, int, str]] = []
    pat = re.compile(r"\bLLMRequest\s*\(")
    for sub in _SCAN_DIRS:
        root = _REPO_ROOT / sub
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if "tests" in path.parts:
                continue
            rel = path.relative_to(_REPO_ROOT).as_posix()
            if rel in _SKIP_FILES:
                continue
            text = path.read_text(encoding="utf-8")
            for m in pat.finditer(text):
                prefix = _line_prefix(text, m.start())
                stripped = prefix.lstrip()
                if stripped.startswith("from ") or stripped.startswith("import "):
                    continue
                if "class " in prefix:
                    continue
                paren_idx = m.end() - 1
                block = _extract_call_block(text, paren_idx)
                # docstring placeholder examples use literal ellipsis
                if "..." in block:
                    continue
                line_no = text.count("\n", 0, m.start()) + 1
                sites.append((path, line_no, block))
    return sites


def test_every_llm_request_has_nonempty_purpose():
    sites = _discover_call_sites()
    assert sites, "no LLMRequest construction sites found - scanner is broken"
    failures: list[str] = []
    discovered: list[str] = []
    for path, line_no, block in sites:
        rel = path.relative_to(_REPO_ROOT).as_posix()
        loc = f"{rel}:{line_no}"
        if "purpose=" not in block:
            failures.append(f"{loc}: missing purpose= entirely")
            continue
        pm = re.search(r'purpose\s*=\s*["\']([^"\']*)["\']', block)
        if pm:
            if not pm.group(1):
                failures.append(f"{loc}: empty purpose string literal")
            else:
                discovered.append(f"{loc} -> purpose={pm.group(1)!r}")
        else:
            # dynamic purpose (e.g. getattr(...)); acceptable but recorded
            discovered.append(f"{loc} -> purpose=<dynamic>")
    if failures:
        pytest.fail("LLMRequest sites missing purpose:\n  " + "\n  ".join(failures))
    assert discovered, "no purpose values discovered"
    # Surface the catalogue for visibility when run with -v.
    print("\nLLMRequest purpose coverage:")
    for entry in sorted(discovered):
        print("  " + entry)
