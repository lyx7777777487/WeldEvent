"""L1 Cognitive Plane -- Persona DTOs.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 2.12).
"""

from pydantic import BaseModel

from src.shared.enums import DecisionPointType, PersonaType


class PersonaFrame(BaseModel):
    persona_type: PersonaType
    decision_point: DecisionPointType
    deepagents_port_preference: list[str]
    reasoning_orientation: str
    output_templates: list[str]
    knowledge_port_priority: list[str]
    memory_query_bias: dict


class PersonaSelectionResult(BaseModel):
    primary_persona: PersonaType
    secondary_persona: PersonaType | None = None
    frame: PersonaFrame
