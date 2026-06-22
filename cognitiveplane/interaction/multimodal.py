"""Interaction plane — multi-modal input parser (spec §骨架 line 883, §2.1).

Normalises text / image / annotation inputs into a single structured
payload that the ReAct engine can consume. Phase 1 ships text + URL
references; image bytes and annotation overlays are wired in Phase 2h
(MLLM Vision).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ModalityType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    ANNOTATION = "annotation"


@dataclass(frozen=True)
class ModalityPart:
    type: ModalityType
    content: str  # raw text, image URI, or annotation JSON
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MultiModalMessage:
    parts: tuple[ModalityPart, ...]

    @property
    def text(self) -> str:
        """Best-effort flat text for LLM Tier-2/Tier-3 fallback prompts."""
        return "\n".join(p.content for p in self.parts if p.type is ModalityType.TEXT)

    @property
    def has_image(self) -> bool:
        return any(p.type is ModalityType.IMAGE for p in self.parts)

    @property
    def has_annotation(self) -> bool:
        return any(p.type is ModalityType.ANNOTATION for p in self.parts)


class MultiModalParser:
    """Parses raw API payloads into a `MultiModalMessage`.

    Accepts either a flat string (treated as a single TEXT part) or a
    list of `{type, content, metadata?}` dicts. Unknown modality types
    are dropped silently — the caller can inspect `MultiModalMessage`
    to see what was actually accepted.
    """

    def parse(self, payload: Any) -> MultiModalMessage:
        if isinstance(payload, str):
            return MultiModalMessage(
                parts=(ModalityPart(type=ModalityType.TEXT, content=payload),)
            )
        if not isinstance(payload, list):
            return MultiModalMessage(parts=())

        parts: list[ModalityPart] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            type_str = str(item.get("type", "")).lower()
            try:
                modality = ModalityType(type_str)
            except ValueError:
                continue
            content = item.get("content", "")
            if not isinstance(content, str):
                continue
            meta = item.get("metadata") or {}
            if not isinstance(meta, dict):
                meta = {}
            parts.append(
                ModalityPart(type=modality, content=content, metadata=meta)
            )
        return MultiModalMessage(parts=tuple(parts))


__all__ = [
    "ModalityPart",
    "ModalityType",
    "MultiModalMessage",
    "MultiModalParser",
]
