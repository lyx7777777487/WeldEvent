"""ToolFailureReflector — 智能 schema 校验失败反射层。

拦截 jsonschema ValidationError，在 rejection 之前尝试：
  - auto_fix: 自动修复简单问题（缺失字段从上下文推断、node_id 自动生成）
  - actionable: 无法自动修复但生成精确 hint 供 LLM 自我修正
  - fatal: 无法修复也无法 hint，走现有 fatal 路径

Source: Plan §3.5 原则 5 + ToolReliability 章 — LLM 不应收到原始 jsonschema
错误消息然后盲目重试。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReflectionResult:
    """Reflection outcome for a schema validation failure."""

    severity: str  # "auto_fixed" | "actionable" | "fatal"
    fixed_arguments: dict[str, Any] | None = None
    hint: str = ""
    # When auto_fixed, what was changed (for EventLog)
    fix_description: str = ""


# ── Per-tool repair strategies ──────────────────────────────────────────

# Patterns for common jsonschema error messages
_RE_MISSING_REQUIRED = re.compile(r"'(\w+)' is a required property")
_RE_WRONG_TYPE = re.compile(r"('[\w.]+' )?is not of type '(\w+)'")
_RE_ENUM = re.compile(r"is not one of enum values: (.*)")
_RE_ARRAY_ITEM = re.compile(r"(\w+)\.(\w+)")
_RE_ARRAY_ITEM_VALIDATE = re.compile(r"'(\w+)' is a required")


def reflect(
    tool_name: str,
    error_message: str,
    arguments: dict[str, Any],
    user_input: str = "",
) -> ReflectionResult:
    """Analyze a schema validation failure and produce a reflection.

    Args:
        tool_name: name of the tool that failed validation
        error_message: raw jsonschema ValidationError.message
        arguments: the arguments that failed validation (may be partial)
        user_input: the user's original message, for context inference

    Returns:
        ReflectionResult with severity and optional fixed_arguments/hint.
    """
    # Dispatch to per-tool strategy
    if tool_name == "design_workflow":
        return _reflect_design_workflow(error_message, arguments, user_input)

    # Generic strategies for any tool
    return _reflect_generic(tool_name, error_message, arguments, user_input)


# ── design_workflow specific ────────────────────────────────────────────


def _reflect_design_workflow(
    error_message: str,
    arguments: dict[str, Any],
    user_input: str,
) -> ReflectionResult:
    """design_workflow 专用反射 — 最高频失败工具。"""
    msg = error_message

    # 1. Missing "objective" required field
    if _match_missing_required(msg, "objective"):
        # Try to infer from user_input
        if user_input and len(user_input) > 3:
            fixed = dict(arguments)
            fixed["objective"] = user_input[:500]
            return ReflectionResult(
                severity="auto_fixed",
                fixed_arguments=fixed,
                hint=f"Inferred objective from user message.",
                fix_description="inferred objective from user_input",
            )
        return ReflectionResult(
            severity="actionable",
            hint=(
                "design_workflow 缺少必填字段 'objective'。"
                "请提供 objective 参数，用一句话描述质检目标（如 '检测这张焊缝图的质量缺陷'）。"
            ),
        )

    # 2. Missing node_id in nodes array items
    if "'node_id'" in msg and isinstance(arguments.get("nodes"), list):
        nodes = arguments.get("nodes", [])
        if isinstance(nodes, list):
            fixed_nodes = []
            for raw in nodes:
                if isinstance(raw, dict):
                    node = dict(raw)
                    if not node.get("node_id") and node.get("capability"):
                        node["node_id"] = f"node_{node['capability']}"
                    fixed_nodes.append(node)
                else:
                    fixed_nodes.append(raw)
            fixed = dict(arguments)
            fixed["nodes"] = fixed_nodes
            return ReflectionResult(
                severity="auto_fixed",
                fixed_arguments=fixed,
                hint="Auto-generated node_id from capability names.",
                fix_description="auto-generated node_id for nodes missing it",
            )
        return ReflectionResult(
            severity="actionable",
            hint=(
                "nodes 数组中的每个元素必须有 'node_id' 字段。"
                "请为每个 node 添加唯一的 node_id（如 'node_1', 'defect_check' 等）。"
            ),
        )

    # 3. Unknown capability in nodes
    if "Unknown capability" in msg:
        return ReflectionResult(
            severity="actionable",
            hint=(
                f"{msg}\n请从 Activity Catalog 中选择已支持的 capability，"
                f"或去掉不支持的节点重试。"
            ),
        )

    # 4. Duplicate node_id
    if "Duplicate node_id" in msg:
        return ReflectionResult(
            severity="actionable",
            hint=(
                f"{msg}\n请确保每个 node 的 node_id 唯一，"
                f"不要重复使用同一个 node_id。"
            ),
        )

    # 5. depends_on references unknown node — try fuzzy auto-fix
    if "depends_on unknown node_id" in msg:
        fixed = _try_fix_depends_on(arguments, msg)
        if fixed:
            return ReflectionResult(
                severity="auto_fixed",
                fixed_arguments=fixed,
                hint="Auto-corrected depends_on references to match declared node_ids.",
                fix_description="auto-corrected depends_on references",
            )
        return ReflectionResult(
            severity="actionable",
            hint=(
                f"{msg}\n请检查 depends_on 引用的 node_id 是否已在 nodes "
                f"数组中定义，注意依赖的节点必须出现在被依赖节点之前。"
            ),
        )

    # 6. Cycle detected
    if "Cycle detected" in msg:
        return ReflectionResult(
            severity="actionable",
            hint=(
                f"{msg}\n请检查 depends_on 是否形成了环形依赖，"
                f"确保工作流是单向无环图（DAG）。"
            ),
        )

    # Fall through to generic
    return _reflect_generic("design_workflow", error_message, arguments, user_input)


# ── Generic strategies ──────────────────────────────────────────────────


def _reflect_generic(
    tool_name: str,
    error_message: str,
    arguments: dict[str, Any],
    user_input: str,
) -> ReflectionResult:
    """Generic reflection for any tool's schema failure."""
    msg = error_message

    # Missing required field — check if inferrable
    m = _RE_MISSING_REQUIRED.search(msg)
    if m:
        field = m.group(1)
        # Try user_input inference for common fields
        if field == "query" and user_input:
            fixed = dict(arguments)
            fixed["query"] = user_input[:500]
            return ReflectionResult(
                severity="auto_fixed",
                fixed_arguments=fixed,
                hint=f"Inferred '{field}' from user message.",
                fix_description=f"inferred {field} from user_input",
            )
        # Auto-infer 'reason' from objective, query, or user_input
        if field == "reason":
            inferred = (
                arguments.get("objective")
                or arguments.get("query")
                or arguments.get("workflow_id")
                or (user_input[:200] if user_input else "")
                or ""
            )
            if inferred:
                fixed = dict(arguments)
                fixed["reason"] = f"auto: {inferred[:200]}"
                return ReflectionResult(
                    severity="auto_fixed",
                    fixed_arguments=fixed,
                    hint=f"Inferred '{field}' from available context.",
                    fix_description=f"inferred {field} from objective/user_input",
                )
        if field == "image_ref" and user_input:
            # Can't infer image_ref — but can give good hint
            pass
        return ReflectionResult(
            severity="actionable",
            hint=(
                f"{tool_name} 缺少必填字段 '{field}'。"
                f"请在调用时提供 {field} 参数。"
                f"{_field_description(tool_name, field)}"
            ),
        )

    # Wrong type
    m = _RE_WRONG_TYPE.search(msg)
    if m:
        field_path = (m.group(1) or "").strip().rstrip(" ")
        expected = m.group(2)
        return ReflectionResult(
            severity="actionable",
            hint=(
                f"参数类型错误：{field_path}应为 {expected} 类型。"
                f"请检查传入值的数据类型是否正确（如字符串不应传数字，数组不应传对象）。"
            ),
        )

    # Enum validation
    m = _RE_ENUM.search(msg)
    if m:
        allowed = m.group(1)
        return ReflectionResult(
            severity="actionable",
            hint=(
                f"参数值不在允许范围内。允许的值：{allowed}。"
                f"请从以上选项中选择一个。"
            ),
        )

    # Unrecognized — give a generic but helpful message
    return ReflectionResult(
        severity="fatal",
        hint=(
            f"参数校验失败：{error_message}。"
            f"请检查 {tool_name} 的参数 schema 并重试。"
        ),
    )


# ── Helpers ─────────────────────────────────────────────────────────────


def _match_missing_required(error_message: str, field_name: str) -> bool:
    """Check if error is about a specific missing required field."""
    return f"'{field_name}' is a required property" in error_message


def _try_fix_depends_on(arguments: dict[str, Any], error_msg: str) -> dict[str, Any] | None:
    """Attempt to auto-correct depends_on references by fuzzy matching node_ids.

    Common LLM mistake: node_id="node_preprocess" depends_on=["node_defect_detection"]
    but actual node_id is "defect_detection" (without "node_" prefix) or vice versa.
    """
    nodes = arguments.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return None

    # Collect all declared node_ids
    declared_ids: list[str] = []
    for n in nodes:
        if isinstance(n, dict) and n.get("node_id"):
            declared_ids.append(n["node_id"])
    if not declared_ids:
        return None

    # Build lookup: strip common prefixes/suffixes for fuzzy matching
    def normalize(nid: str) -> str:
        return nid.replace("node_", "").replace("_node", "").lower()

    normalized_map: dict[str, str] = {}
    for nid in declared_ids:
        normalized_map[normalize(nid)] = nid

    fixed_nodes = []
    any_fixed = False
    for n in nodes:
        if not isinstance(n, dict):
            fixed_nodes.append(n)
            continue
        node = dict(n)
        deps = node.get("depends_on")
        if isinstance(deps, list):
            fixed_deps = []
            for dep in deps:
                if dep in declared_ids:
                    fixed_deps.append(dep)
                else:
                    # Try fuzzy match
                    norm = normalize(dep)
                    if norm in normalized_map:
                        fixed_deps.append(normalized_map[norm])
                        any_fixed = True
                    else:
                        fixed_deps.append(dep)
            node["depends_on"] = fixed_deps
        fixed_nodes.append(node)

    if not any_fixed:
        return None

    fixed = dict(arguments)
    fixed["nodes"] = fixed_nodes
    return fixed


def _field_description(tool_name: str, field: str) -> str:
    """Return a human-readable description of what a field does."""
    descriptions: dict[str, dict[str, str]] = {
        "design_workflow": {
            "objective": "（质检目标的业务描述，如 '检查焊缝是否有气孔缺陷'）",
            "requirements": "（质检要求列表，如 ['GB/T3323 II级']）",
            "nodes": "（工作流节点列表，每个节点含 node_id + capability + depends_on）",
        },
        "launch_workflow": {
            "workflow_id": "（从 design_workflow 返回的 workflow_id）",
        },
        "analyze_image": {
            "image_ref": "（从系统提示中复制，格式如 PENDING:sess_xxx:0）",
        },
        "web_search": {
            "query": "（搜索关键词或问题）",
        },
        "archive_memory": {
            "content": "（要保存的内容）",
        },
        "request_confirmation": {
            "message": "（确认信息，如方案摘要）",
        },
    }
    tool_descs = descriptions.get(tool_name, {})
    return tool_descs.get(field, "")
