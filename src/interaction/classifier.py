"""IntentClassifier — maps user natural language to mode + entities.

Source: L1-Interaction-Layer-Business-Requirements.md §4.

Dual mode:
- Keyword mode (default): matches IntentPattern rules against text
- LLM mode (when get_llm() initialized): uses LLM function calling
  with structured output, falling back to keyword on failure.
"""

from pydantic import BaseModel, Field

from src.interaction.base import IntentPattern, UserMessage
from src.interaction.context import ActiveContext
from src.interaction.registry import ModeRegistry
from src.shared.enums import MatchStrategy


class IntentClassification(BaseModel):
    """Structured output from intent classification."""

    primary_intent: str
    confidence: float = Field(ge=0.0, le=1.0)
    secondary_intent: str | None = None
    extracted_entities: dict = {}
    ambiguity_clarifications: list[str] | None = None


class IntentClassifier:
    """Classifies user input into a target mode.

    Strategy:
    1. If get_llm() is available → use LLM function calling
    2. On LLM failure or when not initialized → fall back to keyword matching
    """

    def __init__(self, registry: ModeRegistry) -> None:
        self._registry = registry

    def classify(
        self, message: UserMessage, context: ActiveContext
    ) -> IntentClassification:
        """Classify the user message into a mode intent."""
        # Try LLM mode first
        try:
            from src.interaction.llm import get_llm

            provider = get_llm()
            return self._classify_llm(message, context, provider)
        except RuntimeError:
            # LLM not initialized, use keyword mode
            return self._classify_keyword(message, context)
        except Exception:
            # LLM failed, fall back to keyword
            return self._classify_keyword(message, context)

    def _classify_llm(
        self,
        message: UserMessage,
        context: ActiveContext,
        provider,
    ) -> IntentClassification:
        """Classify using LLM function calling."""
        import asyncio

        from src.interaction.llm.provider import LLMRequest
        from src.interaction.llm.prompts.intent_classifier import (
            INTENT_CLASSIFIER_SYSTEM_PROMPT,
        )

        mode_descriptions = "\n".join(
            f"- {m.mode_id}: {m.description}"
            for m in self._registry.all_modes().values()
        )
        context_summary = (
            f"operator: {context.operator_id}, "
            f"case: {context.case_id}, "
            f"session: {context.active_session_type}"
        )

        system_prompt = INTENT_CLASSIFIER_SYSTEM_PROMPT.format(
            all_mode_descriptions=mode_descriptions,
            streaming_context_summary=context_summary,
        )

        request = LLMRequest(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message.raw_text},
            ],
            response_format=IntentClassification,
            temperature=0.1,
            caller="IntentClassifier",
            purpose="intent_classification",
            case_id=str(context.case_id.value) if context.case_id else None,
        )

        try:
            loop = asyncio.new_event_loop()
            try:
                resp = loop.run_until_complete(provider.complete(request))
            finally:
                loop.close()
            if resp.parsed_object is not None:
                return resp.parsed_object
            return self._classify_keyword(message, context)
        except Exception:
            return self._classify_keyword(message, context)

    def _classify_keyword(
        self, message: UserMessage, context: ActiveContext
    ) -> IntentClassification:
        """Classify using keyword + context pattern matching."""
        candidates: list[tuple[float, str]] = []

        text_lower = message.raw_text.lower()

        for mode in self._registry.all_modes().values():
            for pattern in mode.intent_patterns:
                score = self._score_pattern(pattern, text_lower, context)
                if score > 0:
                    candidates.append((score, mode.mode_id))
                    break

        if not candidates:
            return IntentClassification(
                primary_intent="unknown",
                confidence=0.0,
                ambiguity_clarifications=["无法识别意图，请更详细地描述您的需求。"],
            )

        candidates.sort(key=lambda t: t[0], reverse=True)
        best_score, best_mode = candidates[0]
        confidence = min(best_score, 1.0)
        secondary = candidates[1][1] if len(candidates) > 1 else None

        return IntentClassification(
            primary_intent=best_mode,
            confidence=confidence,
            secondary_intent=secondary,
        )

    def _score_pattern(
        self,
        pattern: IntentPattern,
        text_lower: str,
        context: ActiveContext,
    ) -> float:
        """Score a single IntentPattern against the input text."""
        if pattern.match_strategy == MatchStrategy.KEYWORD:
            matched = sum(1 for kw in pattern.patterns if kw in text_lower)
            if matched == 0:
                return 0.0
            raw_score = matched / len(pattern.patterns) * (pattern.priority / 100.0)
            if pattern.required_context and pattern.required_context.needs_active_case:
                if context.case_id is not None:
                    raw_score += 0.1
            return raw_score

        if pattern.match_strategy == MatchStrategy.COMPOSITE:
            matched = sum(1 for kw in pattern.patterns if kw in text_lower)
            if matched == 0:
                return 0.0
            if pattern.required_context:
                if pattern.required_context.needs_active_case and context.case_id is None:
                    return 0.0
            raw_score = matched / len(pattern.patterns) * (pattern.priority / 100.0)
            if pattern.required_context and pattern.required_context.needs_active_case:
                if context.case_id is not None:
                    raw_score += 0.1
            return raw_score

        return 0.0
