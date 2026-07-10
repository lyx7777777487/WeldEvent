"""L1 Cognitive Plane -- Knowledge DTOs.

Source: L1_Port_and_Contract_Design.md (Phase 4, Section 2.9).
"""

from pydantic import BaseModel, Field

from cognitiveplane.shared.enums import KnowledgeType
from cognitiveplane.shared.types import KnowledgeId
from cognitiveplane.shared.dto_decision.outputs import Constraint, ParameterSet

__all__ = [
    "CaseLibraryQuery",
    "CaseLibraryResult",
    "EquipmentKnowledgeQuery",
    "EquipmentKnowledgeResult",
    "KnowledgeResult",
    "ProcessKnowledgeQuery",
    "ProcessKnowledgeResult",
    "RAGQuery",
    "ReasoningKnowledgeQuery",
    "ReasoningKnowledgeResult",
    "RuleQuery",
    "RuleResult",
    "StandardsQuery",
    "StandardsResult",
    "VisionKnowledgeQuery",
    "VisionKnowledgeResult",
]


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


# ---------------------------------------------------------------------------
# 视觉理解知识库 — 工业图像数据集元信息（RAG collection 1）
# ---------------------------------------------------------------------------

class VisionKnowledgeQuery(BaseModel):
    """视觉理解 collection 的检索查询。

    语义检索：query_text 经 embedding 后做向量相似度匹配。
    过滤维度：defect_type / industry / modality 可选过滤。
    """
    query_text: str = Field(min_length=1, description="自然语言描述的场景或需求")
    defect_type: str | None = Field(default=None, description="缺陷类型过滤（如 气孔/未熔合/裂纹）")
    industry: str | None = Field(default=None, description="行业过滤（如 焊接/钢材/电子）")
    modality: str | None = Field(default=None, description="成像方式过滤（如 RGB/X-ray/红外）")
    max_results: int = Field(default=5, ge=1, le=20)


class VisionKnowledgeResult(BaseModel):
    """视觉理解 collection 的单条检索结果 — 一个工业图像数据集的元信息。"""
    dataset_name: str
    description: str
    source_url: str
    image_count: str
    defect_types: list[str]
    modality: str
    industry: str
    applicable_scenarios: list[str]
    license: str
    relevance: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# 文本推理知识库 — 工业流程/标准/推理模式语料（RAG collection 2）
# ---------------------------------------------------------------------------

class ReasoningKnowledgeQuery(BaseModel):
    """文本推理 collection 的检索查询。

    语义检索：query_text 经 embedding 后做向量相似度匹配。
    """
    query_text: str = Field(min_length=1, description="自然语言描述的问题或场景")
    knowledge_type: str | None = Field(
        default=None,
        description="知识类型过滤：standard（标准条款）/ reasoning_pattern（推理模式）/ vqa_template（视觉问答模板）/ process_doc（工艺流程文档）"
    )
    max_results: int = Field(default=5, ge=1, le=20)


class ReasoningKnowledgeResult(BaseModel):
    """文本推理 collection 的单条检索结果。"""
    title: str
    source: str
    knowledge_type: str
    content: str
    applicable_context: str
    relevance: float = Field(ge=0.0, le=1.0)
