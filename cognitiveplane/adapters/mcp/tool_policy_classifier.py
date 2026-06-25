"""MCPToolPolicyClassifier — 三层 ToolPolicy 判定.

Spec §4 — 判定顺序: YAML override > annotations > 前缀启发式 > 保守 Tier-B。

偏差: §4.2.1 字面顺序是 "annotations → 前缀 → YAML override"，但
§4.2.1 明文写 "人工配置始终优先于自动判定"。本设计遵循明文约束，
把 YAML override 提到最前。偏差已记录在 spec §8。
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from cognitiveplane.adapters.mcp.base import MCPPolicyDecision, ToolDescriptor

logger = logging.getLogger(__name__)

READ_PREFIXES = ("read_", "get_", "list_", "search_", "find_", "query_")
WRITE_PREFIXES = (
    "create_",
    "update_",
    "delete_",
    "import_",
    "send_",
    "publish_",
    "post_",
)


class MCPToolPolicyClassifier:
    """三层 ToolPolicy 判定器 — 无状态，可复用。"""

    def __init__(self, yaml_path: Path | None = None) -> None:
        self._yaml_overrides: dict[str, dict] = {}
        if yaml_path is not None:
            self._yaml_overrides = self._load_yaml(yaml_path)

    @staticmethod
    def _load_yaml(yaml_path: Path) -> dict[str, dict]:
        """加载 YAML override 配置。格式错时返回空 dict 并记日志。"""
        try:
            with open(yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                logger.warning("mcp_policy.yaml root is not a dict — ignoring overrides")
                return {}
            return data
        except yaml.YAMLError as e:
            logger.warning("mcp_policy.yaml parse failed: %s — ignoring overrides", e)
            return {}
        except FileNotFoundError:
            return {}

    def classify(self, descriptor: ToolDescriptor) -> MCPPolicyDecision:
        # 第一层: YAML override (最高优先级)
        override = self._yaml_overrides.get(descriptor.name)
        if override is not None:
            return MCPPolicyDecision(
                auto_approve=override.get("auto_approve", False),
                tier=override.get("tier", "B"),
                require_reason=override.get("require_reason", False),
                reason="yaml_override",
            )

        # 第二层: MCP annotations
        if descriptor.annotations is not None:
            if descriptor.annotations.readOnlyHint is True:
                return MCPPolicyDecision(
                    auto_approve=True, tier="A", reason="annotations.readOnlyHint"
                )
            if descriptor.annotations.destructiveHint is True:
                return MCPPolicyDecision(
                    auto_approve=False,
                    tier="B",
                    require_reason=True,
                    reason="annotations.destructiveHint",
                )
            if descriptor.annotations.openWorldHint is True:
                return MCPPolicyDecision(
                    auto_approve=False, tier="B", reason="annotations.openWorldHint"
                )

        # 第三层: 名字前缀启发式
        if descriptor.name.startswith(READ_PREFIXES):
            return MCPPolicyDecision(
                auto_approve=True, tier="A", reason="prefix:read"
            )
        if descriptor.name.startswith(WRITE_PREFIXES):
            return MCPPolicyDecision(
                auto_approve=False,
                tier="B",
                require_reason=True,
                reason="prefix:write",
            )

        # 未匹配 → 保守 Tier-B
        return MCPPolicyDecision(
            auto_approve=False,
            tier="B",
            reason="prefix:unknown:conservative",
        )