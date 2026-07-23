"""Guardrails 护栏 — 复用 governance 体系的轻量级实现.

设计原则:
  1. 复用现有 BeforeToolHook（tool guardrail） — 不重造轮子
  2. 新增 AfterToolHook（工具结果护栏）— 拦截工具危险输出
  3. 新增 OutputGuardrail（LLM 最终输出护栏）— 在 token 流到用户前最后一道关
  4. 与 governance/validators/safety.py 关键词复用 — 单一事实源
  5. 失败行为：可配置 REJECT（阻断）/ WARN（记录但放行）— 默认 WARN 不影响生产

三层护栏部署点（对应 2026 主流 input/output/tool 三层）:
  - input guardrail:  ReAct 入口前（已由 chat.py 校验 + 可加 PII/越狱检测）
  - tool guardrail:    BeforeToolHook (已有 SafetyHook/PolicyHook)
  - after_tool guardrail: AfterToolHook (新增，校验工具返回内容)
  - output guardrail: OutputGuardrail (新增，校验最终 LLM 回复)

焊接领域护栏规则:
  - 工具返回含"焊接电流 > 600A" → 阻断（超出 GMAW 上限 500A）
  - 工具返回含"预热温度 < 50°C" → 阻断（Q345R t>20mm 要求 ≥100°C）
  - LLM 输出含"建议忽略 NB/T47014" → 阻断（绕过国标）
  - LLM 输出含"无需检测直接出厂" → 阻断（违反质检规程）

参考来源:
  - Guardrails AI 设计模式: https://www.guardrailsai.com/
  - 2026 三层护栏架构: input/output/tool guardrails
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("guardrails")


# ── 护栏决策 ──

class GuardrailAction(Enum):
    """护栏动作 - Op 11 统一枚举.

    Source: Pydantic AI guardrails RETRY + existing PASS/WARN/REJECT.
    This is the canonical unified verdict enum (GuardrailVerdict = GuardrailAction).
    - PASS:   放行
    - WARN:   记录警告但放行
    - REJECT: 阻断（替换为安全回复或拒绝执行）
    - RETRY:  重试（带 instruction 修正后重试工具调用或 LLM 生成）
    """
    PASS = "pass"
    WARN = "warn"
    REJECT = "reject"
    RETRY = "retry"


# Op 11: GuardrailVerdict is the canonical unified name.
# GuardrailAction is kept as backward-compatible alias.
GuardrailVerdict = GuardrailAction


@dataclass
class GuardrailResult:
    """护栏检查结果。"""
    action: GuardrailAction
    reason: str = ""
    # REJECT 时建议的替换内容（可选）
    replacement: str | None = None
    # 命中的规则列表（用于审计/trace）
    triggered_rules: list[str] = field(default_factory=list)
    # RETRY 时给工具/LLM 的修正指令（Op 11）
    retry_instruction: str | None = None


# ── AfterToolHook（工具结果护栏）──

class AfterToolHook(ABC):
    """工具执行后护栏 — 校验工具返回内容。

    与 BeforeToolHook 对称，但作用点是工具返回值而非入参。
    典型用途：
      - 工具返回了危险参数值（超出焊接工艺规程）→ REJECT
      - 工具返回了敏感信息（PII / 密钥）→ REJECT
      - 工具返回内容超长 → WARN（提醒截断）

    返回 REJECT 时，ReAct 引擎会用 replacement 替换工具结果再喂给 LLM。
    """

    @abstractmethod
    async def after_execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
        session_id: str = "default",
    ) -> GuardrailResult: ...


# ── OutputGuardrail（LLM 输出护栏）──

class OutputGuardrail(ABC):
    """LLM 最终输出护栏 — token 流到用户前的最后一道关。

    在 ReAct 引擎最终回复处调用（run/run_stream 末尾）。
    典型用途：
      - 输出绕过国标建议 → REJECT（替换为合规提示）
      - 输出含敏感信息 → REJECT（脱敏）
      - 输出含"无需检测"等违规建议 → REJECT
    """

    @abstractmethod
    async def check_output(
        self,
        reply: str,
        tools_used: list[str],
        session_id: str = "default",
    ) -> GuardrailResult: ...


# ── 焊接领域护栏实现 ──

# 工具返回的危险参数模式（regex 匹配）
# 来源: NB/T47014-2011 焊接工艺规程 + GB/T3323-2005 检测标准
_DANGEROUS_TOOL_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    # GMAW 上限 500A（NB/T47014 工艺规程）
    ("welding_current_overload",
     re.compile(r"焊接电流[^\d]{0,10}(\d{3,4})\s*A", re.IGNORECASE),
     "焊接电流超过 GMAW 上限 500A"),
    # Q345R t>20mm 预热 ≥100°C
    ("preheat_too_low",
     re.compile(r"预热温度[^\d]{0,10}(\d{1,2})\s*°?C", re.IGNORECASE),
     "Q345R 厚板预热温度 < 100°C 违反 NB/T47014"),
]

# LLM 输出的违规建议模式
_DANGEROUS_OUTPUT_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("bypass_standard",
     re.compile(r"(?:忽略|跳过|绕过|不用遵守)\s*(?:NB/T47014|GB/T3323|国标|标准)", re.IGNORECASE),
     "建议绕过国家标准 — 违反质检规程"),
    ("skip_inspection",
     re.compile(r"(?:无需|不需要|跳过|免)(?:检测|检验|探伤|射线|超声)", re.IGNORECASE),
     "建议跳过焊缝检测 — 违反质检规程"),
    ("fabricate_parameter",
     re.compile(r"(?:随便|任意|随意)\s*(?:填|写|给)\s*(?:参数|电流|电压)", re.IGNORECASE),
     "建议伪造焊接参数 — 违反工艺纪律"),
]


def _check_patterns(
    text: str,
    patterns: list[tuple[str, re.Pattern, str]],
    max_value_check: bool = True,
) -> list[tuple[str, str]]:
    """匹配危险模式，返回 [(rule_name, reason), ...]。

    max_value_check: 对焊接电流等数值型规则做上限校验
    """
    hits: list[tuple[str, str]] = []
    for rule_name, pattern, reason in patterns:
        m = pattern.search(text)
        if not m:
            continue
        # 数值上限校验
        if rule_name == "welding_current_overload" and max_value_check:
            try:
                value = int(m.group(1))
                if value <= 500:
                    continue  # 正常范围
            except (ValueError, IndexError):
                logger.error("guardrail check failed", exc_info=True)
        if rule_name == "preheat_too_low" and max_value_check:
            try:
                value = int(m.group(1))
                if value >= 100:
                    continue  # 正常范围
            except (ValueError, IndexError):
                logger.error("guardrail check failed", exc_info=True)
        hits.append((rule_name, reason))
    return hits


class WeldingAfterToolHook(AfterToolHook):
    """焊接领域工具结果护栏。"""

    async def after_execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
        session_id: str = "default",
    ) -> GuardrailResult:
        # result 可能是 dict（ToolResult.to_dict）/ str / 自定义对象
        text = _extract_text(result)
        if not text:
            return GuardrailResult(action=GuardrailAction.PASS)

        hits = _check_patterns(text, _DANGEROUS_TOOL_PATTERNS)
        if not hits:
            return GuardrailResult(action=GuardrailAction.PASS)

        rules = [h[0] for h in hits]
        reasons = "; ".join(h[1] for h in hits)
        logger.warning(
            "[guardrail] AfterTool REJECT tool=%s rules=%s reasons=%s",
            tool_name, rules, reasons,
        )
        return GuardrailResult(
            action=GuardrailAction.REJECT,
            reason=reasons,
            replacement=f"[已拦截] 工具 {tool_name} 返回了违反焊接规程的参数：{reasons}。请重新查询合规参数。",
            triggered_rules=rules,
        )


class WeldingOutputGuardrail(OutputGuardrail):
    """焊接领域 LLM 输出护栏。"""

    async def check_output(
        self,
        reply: str,
        tools_used: list[str],
        session_id: str = "default",
    ) -> GuardrailResult:
        if not reply:
            return GuardrailResult(action=GuardrailAction.PASS)

        hits = _check_patterns(reply, _DANGEROUS_OUTPUT_PATTERNS, max_value_check=False)
        if not hits:
            return GuardrailResult(action=GuardrailAction.PASS)

        rules = [h[0] for h in hits]
        reasons = "; ".join(h[1] for h in hits)
        logger.warning(
            "[guardrail] Output REJECT session=%s rules=%s reasons=%s",
            session_id, rules, reasons,
        )
        return GuardrailResult(
            action=GuardrailAction.REJECT,
            reason=reasons,
            replacement=(
                "⚠️ 抱歉，上述回复包含违反焊接质检规程的建议，已被护栏拦截。\n"
                f"触发规则：{reasons}\n"
                "请基于 NB/T47014 / GB/T3323 等国家标准重新提问。"
            ),
            triggered_rules=rules,
        )


# ── 辅助函数 ──

def _extract_text(result: Any) -> str:
    """从工具结果对象中提取文本（兼容 ToolResult / dict / str）。"""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        # ToolResult.to_dict() 序列化后的结构
        return str(result.get("content") or result.get("result") or result.get("data") or "")
    # 对象属性
    for attr in ("content", "result", "text", "output"):
        val = getattr(result, attr, None)
        if val and isinstance(val, str):
            return val
    return str(result)[:500]


__all__ = [
    "GuardrailAction",
    "GuardrailVerdict",
    "GuardrailResult",
    "AfterToolHook",
    "OutputGuardrail",
    "WeldingAfterToolHook",
    "WeldingOutputGuardrail",
]
