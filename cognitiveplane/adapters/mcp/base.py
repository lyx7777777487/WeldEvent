"""MCP 基础数据结构 + ABC 定义。

Spec: docs/superpowers/specs/2026-06-25-phase3-c-mcp-infrastructure-design.md §2.

L1 认知 MCP 基础设施 — 仅抽象层 + InProcess 传输。
真实认知 MCP（文档检索等）按需接入；工业执行 MCP（detect_defects 等）
在 executionplane 仓库，不在本包。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class MCPAnnotations:
    """MCP 协议 annotations 字段 — 工具自声明安全属性。

    Spec §4.2.1 第二层判定来源。None 表示工具未声明该 hint。
    """
    readOnlyHint: bool | None = None
    destructiveHint: bool | None = None
    openWorldHint: bool | None = None


@dataclass(frozen=True)
class ToolDescriptor:
    """MCP 工具描述 — MCPClient.list_tools() 返回。

    name: 工具名（如 "echo_tool" / 未来的 "search_docs"）
    description: 给 LLM 看的工具说明
    parameters_schema: JSON Schema，给 LLM function calling 用
    annotations: §4.2.1 第二层判定来源，None 表示工具未声明
    """
    name: str
    description: str
    parameters_schema: dict
    annotations: MCPAnnotations | None = None


@dataclass(frozen=True)
class MCPPolicyDecision:
    """三层 ToolPolicy 判定结果 — 缓存到 MCPAdapter.metadata。

    Spec §4 — 判定顺序: YAML override > annotations > 前缀启发式 > 保守 Tier-B。
    """
    auto_approve: bool
    tier: str  # "A" (retriable) / "B" (precise, require approval)
    require_reason: bool = False
    reason: str = ""


# MCPClient / MCPServer / MCPAdapter 定义在后续 task 加入此文件