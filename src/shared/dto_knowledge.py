"""L1 Cognitive Plane -- Knowledge DTOs.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 2.9).
"""

from pydantic import BaseModel, Field

from src.shared.enums import KnowledgeType
from src.shared.types import KnowledgeId
from src.shared.dto_decision.outputs import Constraint, ParameterSet


class RAGQuery(BaseModel):
    query_text: str = Field(min_length=1)
    knowledge_types: list[KnowledgeType] | None = None
    max_results: int = Field(default=5, ge=1, le=50)
    min_relevance: float = Field(default=0.5, ge=0.0, le=1.0)


class KnowledgeResult(BaseModel):
    knowledge_id: KnowledgeId
    knowledge_type: KnowledgeType
    content: str = Field(min_length=1)
    relevance_score: float = Field(ge=0.0, le=1.0)
    source_reference: str


class RuleQuery(BaseModel):
    rule_category: str
    context: dict
    parameters: dict | None = None


class RuleResult(BaseModel):
    rule_id: str
    rule_text: str
    applicability: float = Field(ge=0.0, le=1.0)
    constraints: list[Constraint]


class StandardsQuery(BaseModel):
    standard_id: str | None = None
    keyword: str | None = None
    section: str | None = None


class StandardsResult(BaseModel):
    standard_id: str
    section: str
    clause: str
    text: str
    relevance: float = Field(ge=0.0, le=1.0)


class EquipmentKnowledgeQuery(BaseModel):
    equipment_id: str | None = None
    equipment_type: str | None = None
    parameter: str | None = None


class EquipmentKnowledgeResult(BaseModel):
    equipment_id: str
    specifications: dict
    operational_limits: dict
    maintenance_requirements: list[str]


class ProcessKnowledgeQuery(BaseModel):
    process_type: str | None = None
    material: str | None = None
    joint_type: str | None = None


class ProcessKnowledgeResult(BaseModel):
    process_id: str
    recommended_parameters: ParameterSet
    quality_criteria: dict
    common_defects: list[str]


class CaseLibraryQuery(BaseModel):
    defect_type: str | None = None
    similarity_context: dict | None = None
    max_results: int = Field(default=5, ge=1, le=50)


class CaseLibraryResult(BaseModel):
    case_id: str
    defect_description: str
    resolution: str
    outcome: str
    similarity_score: float = Field(ge=0.0, le=1.0)
