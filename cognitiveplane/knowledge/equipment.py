"""Knowledge plane — Equipment knowledge service (spec §骨架 line 947)."""

from __future__ import annotations

from cognitiveplane.shared.dto.knowledge import (
    EquipmentKnowledgeQuery,
    EquipmentKnowledgeResult,
)

from .ports import EquipmentKnowledgeInput, EquipmentKnowledgePort


class EquipmentKnowledgeService:
    def __init__(self, port: EquipmentKnowledgePort) -> None:
        self._port = port

    async def query(
        self, query: EquipmentKnowledgeQuery
    ) -> list[EquipmentKnowledgeResult]:
        out = await self._port.query(EquipmentKnowledgeInput(query=query))
        return list(out.results)


__all__ = ["EquipmentKnowledgeService"]
