"""Test MCPToolPolicyClassifier — 三层 ToolPolicy 判定.

Spec §4 — 判定顺序: YAML override > annotations > 前缀启发式 > 保守 Tier-B。
注意: 本设计把 §4.2.1 字面顺序"annotations → 前缀 → YAML"调整为
"YAML > annotations > 前缀"，遵循 §4.2.1 "人工配置始终优先"明文约束。
偏差已记录在 spec §8。
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from cognitiveplane.adapters.mcp.base import MCPAnnotations, ToolDescriptor
from cognitiveplane.adapters.mcp.tool_policy_classifier import (
    MCPToolPolicyClassifier,
)


def _desc(name: str, annotations: MCPAnnotations | None = None) -> ToolDescriptor:
    return ToolDescriptor(
        name=name, description="d", parameters_schema={}, annotations=annotations
    )


# ── 第一层: YAML override (最高优先级) ──

def test_yaml_override_takes_precedence_over_annotations(tmp_path: Path):
    """YAML override > annotations — 即使 readOnlyHint=True, YAML 说不要 auto_approve 就不要。"""
    yaml_path = tmp_path / "mcp_policy.yaml"
    yaml_path.write_text(dedent("""
        search_docs:
          auto_approve: false
          tier: "B"
          require_reason: true
    """))

    classifier = MCPToolPolicyClassifier(yaml_path=yaml_path)
    desc = _desc("search_docs", MCPAnnotations(readOnlyHint=True))

    decision = classifier.classify(desc)

    assert decision.auto_approve is False
    assert decision.tier == "B"
    assert decision.require_reason is True
    assert decision.reason == "yaml_override"


def test_yaml_override_takes_precedence_over_prefix(tmp_path: Path):
    """YAML override > 前缀 — 即使名字以 read_ 开头, YAML 说 Tier-B 就 Tier-B。"""
    yaml_path = tmp_path / "mcp_policy.yaml"
    yaml_path.write_text('read_special:\n  auto_approve: false\n  tier: "B"\n')

    classifier = MCPToolPolicyClassifier(yaml_path=yaml_path)
    desc = _desc("read_special")

    decision = classifier.classify(desc)

    assert decision.auto_approve is False
    assert decision.tier == "B"
    assert decision.reason == "yaml_override"


def test_yaml_missing_file_falls_through_to_other_layers():
    """No YAML path — skip override layer, continue to annotations/prefix."""
    classifier = MCPToolPolicyClassifier(yaml_path=None)
    desc = _desc("read_docs", MCPAnnotations(readOnlyHint=True))

    decision = classifier.classify(desc)

    # annotations layer fires
    assert decision.auto_approve is True
    assert decision.tier == "A"
    assert decision.reason == "annotations.readOnlyHint"


# ── 第二层: MCP annotations ──

def test_annotations_readonly_hint_auto_approve_tier_a():
    classifier = MCPToolPolicyClassifier(yaml_path=None)
    desc = _desc("anything", MCPAnnotations(readOnlyHint=True))

    decision = classifier.classify(desc)

    assert decision.auto_approve is True
    assert decision.tier == "A"
    assert decision.reason == "annotations.readOnlyHint"


def test_annotations_destructive_hint_tier_b_require_reason():
    classifier = MCPToolPolicyClassifier(yaml_path=None)
    desc = _desc("anything", MCPAnnotations(destructiveHint=True))

    decision = classifier.classify(desc)

    assert decision.auto_approve is False
    assert decision.tier == "B"
    assert decision.require_reason is True
    assert decision.reason == "annotations.destructiveHint"


def test_annotations_openworld_hint_tier_b():
    classifier = MCPToolPolicyClassifier(yaml_path=None)
    desc = _desc("anything", MCPAnnotations(openWorldHint=True))

    decision = classifier.classify(desc)

    assert decision.auto_approve is False
    assert decision.tier == "B"
    assert decision.reason == "annotations.openWorldHint"


def test_annotations_all_none_falls_through_to_prefix():
    """annotations present but all hints None — skip annotations layer."""
    classifier = MCPToolPolicyClassifier(yaml_path=None)
    desc = _desc("read_docs", MCPAnnotations())  # all None

    decision = classifier.classify(desc)

    # prefix layer fires
    assert decision.auto_approve is True
    assert decision.tier == "A"
    assert decision.reason == "prefix:read"


# ── 第三层: 名字前缀启发式 ──

@pytest.mark.parametrize("prefix", ["read_", "get_", "list_", "search_", "find_", "query_"])
def test_prefix_read_tier_a_auto_approve(prefix: str):
    classifier = MCPToolPolicyClassifier(yaml_path=None)
    desc = _desc(f"{prefix}docs")

    decision = classifier.classify(desc)

    assert decision.auto_approve is True
    assert decision.tier == "A"
    assert decision.reason == "prefix:read"


@pytest.mark.parametrize(
    "prefix", ["create_", "update_", "delete_", "import_", "send_", "publish_", "post_"]
)
def test_prefix_write_tier_b_require_reason(prefix: str):
    classifier = MCPToolPolicyClassifier(yaml_path=None)
    desc = _desc(f"{prefix}thing")

    decision = classifier.classify(desc)

    assert decision.auto_approve is False
    assert decision.tier == "B"
    assert decision.require_reason is True
    assert decision.reason == "prefix:write"


# ── 未匹配 → 保守 Tier-B ──

def test_unknown_prefix_conservative_tier_b():
    """echo_tool — 不匹配 read/write 前缀, 落入保守 Tier-B."""
    classifier = MCPToolPolicyClassifier(yaml_path=None)
    desc = _desc("echo_tool")

    decision = classifier.classify(desc)

    assert decision.auto_approve is False
    assert decision.tier == "B"
    assert decision.require_reason is False  # 保守但不强制 reason
    assert decision.reason == "prefix:unknown:conservative"


# ── YAML 格式错误 fallback ──

def test_yaml_malformed_falls_back_to_conservative(tmp_path: Path, capsys):
    """Spec §5 — YAML 格式错时记 EventLog + fallback 保守 Tier-B.

    本测试只验证 fallback 行为；EventLog 记录在 Task 7 MCPRegistry 集成时验证。
    """
    yaml_path = tmp_path / "mcp_policy.yaml"
    yaml_path.write_text("this: is: not: valid: yaml: [")

    classifier = MCPToolPolicyClassifier(yaml_path=yaml_path)
    desc = _desc("echo_tool")

    decision = classifier.classify(desc)

    # YAML 解析失败 → 跳过 override 层 → echo_tool 落入保守 Tier-B
    assert decision.auto_approve is False
    assert decision.tier == "B"
    assert decision.reason == "prefix:unknown:conservative"