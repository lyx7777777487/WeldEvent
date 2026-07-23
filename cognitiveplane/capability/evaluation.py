"""Op 8.3: Zero-cost evaluation - LLM self-eval + trend tracking.

Source: Anthropic "Building Effective Agents" evaluator-optimizer (2024).

No labeled dataset needed. Uses LLM self-evaluation after each workflow
execution + tracks quality trends across runs to detect regressions.

Depends on:
  - LLMCallTracker (existing, needs wiring into production path)
  - LLMProvider.complete() for self-evaluation calls
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from cognitiveplane.capability.provider import LLMProvider
    from cognitiveplane.capability.tracking import LLMCallTracker

logger = logging.getLogger("evaluation")


@dataclass
class QualityEvaluation:
    """LLM self-evaluation result for a workflow execution."""
    quality_score: float  # 0.0 - 1.0
    quality_note: str = ""
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=lambda: _time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "quality_score": self.quality_score,
            "quality_note": self.quality_note,
            "issues": self.issues,
            "suggestions": self.suggestions,
            "timestamp": self.timestamp,
        }


class ZeroCostEvaluator:
    """Op 8.3: Zero-cost workflow quality evaluation.

    Uses LLM self-evaluation (no labeled data needed) + trend tracking
    to detect quality regressions over time.
    """

    def __init__(
        self,
        llm_provider: "LLMProvider | None" = None,
        tracker: "LLMCallTracker | None" = None,
    ) -> None:
        self._llm = llm_provider
        self._tracker = tracker
        self._history: list[QualityEvaluation] = []

    async def evaluate(
        self,
        goal: str,
        node_outcomes: list[dict[str, Any]],
        user_modifications: list[str] | None = None,
    ) -> QualityEvaluation:
        """Evaluate workflow execution quality via LLM self-evaluation."""
        if self._llm is None:
            # Fallback: heuristic evaluation without LLM
            return self._heuristic_eval(goal, node_outcomes, user_modifications)

        # Build evaluation prompt
        nodes_text = "\n".join(
            f"  - {n.get('node_id', '?')}: {n.get('status', '?')}" for n in node_outcomes
        )
        mods_text = "\n".join(f"  - {m}" for m in (user_modifications or [])) or "  无"

        prompt = (
            "评估本次工业焊缝检测工作流的执行质量。\n\n"
            f"目标: {goal}\n"
            f"节点结果:\n{nodes_text}\n"
            f"用户修改:\n{mods_text}\n\n"
            "请评估:\n"
            "1. quality_score (0.0-1.0): 整体质量分\n"
            "2. quality_note: 一句话评价\n"
            "3. issues: 发现的问题列表\n"
            "4. suggestions: 改进建议列表\n\n"
            "JSON 格式回复: {\"quality_score\": float, \"quality_note\": str, \"issues\": [str], \"suggestions\": [str]}"
        )

        try:
            from cognitiveplane.capability.provider import LLMRequest
            resp = await self._llm.complete(LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                caller="zero_cost_eval",
                purpose="evaluation",
                max_tokens=1024,
                temperature=0.1,
            ))
            import json
            result = json.loads(resp.content)
            evaluation = QualityEvaluation(
                quality_score=float(result.get("quality_score", 0.5)),
                quality_note=result.get("quality_note", ""),
                issues=result.get("issues", []),
                suggestions=result.get("suggestions", []),
            )
        except Exception as e:
            logger.warning("LLM evaluation failed, using heuristic: %s", e)
            evaluation = self._heuristic_eval(goal, node_outcomes, user_modifications)

        self._history.append(evaluation)
        return evaluation

    def _heuristic_eval(
        self,
        goal: str,
        node_outcomes: list[dict[str, Any]],
        user_modifications: list[str] | None = None,
    ) -> QualityEvaluation:
        """Fallback heuristic evaluation without LLM."""
        if not node_outcomes:
            return QualityEvaluation(quality_score=0.3, quality_note="无节点结果")

        ok_count = sum(1 for n in node_outcomes if str(n.get("status", "")).upper() == "OK")
        fail_count = sum(1 for n in node_outcomes if str(n.get("status", "")).upper() in ("NG", "ERROR"))
        total = len(node_outcomes)
        mod_count = len(user_modifications or [])

        score = ok_count / total
        if mod_count > 0:
            score -= 0.1 * mod_count  # user modifications indicate quality issues

        issues = []
        if fail_count > 0:
            issues.append(f"{fail_count} nodes failed")
        if mod_count > 2:
            issues.append(f"excessive user modifications ({mod_count})")

        return QualityEvaluation(
            quality_score=max(0.0, min(1.0, score)),
            quality_note=f"{ok_count}/{total} nodes OK, {fail_count} failed, {mod_count} modifications",
            issues=issues,
        )

    def check_trend(self, window: int = 10) -> dict[str, Any]:
        """Check quality trend for regression detection."""
        if len(self._history) < 2:
            return {"trend": "insufficient_data", "count": len(self._history)}

        recent = self._history[-window:]
        scores = [e.quality_score for e in recent]

        avg = sum(scores) / len(scores)
        if len(scores) >= 4:
            half = len(scores) // 2
            first_half_avg = sum(scores[:half]) / half
            second_half_avg = sum(scores[half:]) / (len(scores) - half)
            if second_half_avg < first_half_avg - 0.1:
                trend = "declining"
            elif second_half_avg > first_half_avg + 0.05:
                trend = "improving"
            else:
                trend = "stable"
        else:
            trend = "stable"

        return {
            "trend": trend,
            "avg_quality": round(avg, 3),
            "count": len(self._history),
            "recent_scores": [round(s, 2) for s in scores],
        }

    @property
    def history(self) -> list[QualityEvaluation]:
        return list(self._history)


def wire_tracker_to_provider(
    provider: "LLMProvider",
    tracker: "LLMCallTracker",
) -> None:
    """Op 8.3: Wire LLMCallTracker into the provider's complete() method.

    This patches the provider to record every LLM call into the tracker.
    Call this once during app startup.
    """
    original_complete = provider.complete

    async def tracked_complete(request, *args, **kwargs):
        start = _time.time()
        response = await original_complete(request, *args, **kwargs)
        latency_ms = int((_time.time() - start) * 1000)

        # Record to tracker
        from cognitiveplane.capability.tracking import LLMCallRecord
        usage = getattr(response, "usage", {}) or {}
        tracker.record(LLMCallRecord(
            caller=getattr(request, "caller", "unknown"),
            purpose=getattr(request, "purpose", ""),
            model_used=getattr(response, "model", "unknown"),
            tokens_prompt=usage.get("prompt_tokens", 0),
            tokens_completion=usage.get("completion_tokens", 0),
            latency_ms=latency_ms,
            cost_usd=0.0,  # cost calculation requires pricing config
        ))
        return response

    provider.complete = tracked_complete
    logger.info("LLMCallTracker wired into %s", type(provider).__name__)


__all__ = [
    "QualityEvaluation",
    "ZeroCostEvaluator",
    "wire_tracker_to_provider",
]
