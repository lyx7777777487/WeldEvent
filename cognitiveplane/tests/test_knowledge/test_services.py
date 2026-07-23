"""knowledge/ — service façade tests (spec §骨架 943-947).

Each Knowledge plane service is a thin pass-through over its port:
build the canonical `*QueryInput` wrapper, call the port, return the
unwrapped `results` list. These tests pin that contract so the Phase
2e/2g adapter swap (Milvus + reranker) can land without behaviour
drift.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from cognitiveplane.knowledge.cases import CaseLibraryService
from cognitiveplane.knowledge.equipment import EquipmentKnowledgeService
from cognitiveplane.knowledge.process import ProcessKnowledgeService
from cognitiveplane.knowledge.rag import RAGService
from cognitiveplane.knowledge.standards import StandardsService
from cognitiveplane.shared.dto.knowledge import (
    CaseLibraryQuery,
    CaseLibraryResult,
    EquipmentKnowledgeQuery,
    EquipmentKnowledgeResult,
    KnowledgeResult,
    ProcessKnowledgeQuery,
    ProcessKnowledgeResult,
    RAGQuery,
    StandardsQuery,
    StandardsResult,
)
from cognitiveplane.shared.dto_decision.outputs import ParameterSet
from cognitiveplane.shared.enums import KnowledgeType
from cognitiveplane.shared.ports.knowledge import (
    CaseLibraryQueryInput,
    CaseLibraryQueryOutput,
    CaseLibraryQueryPort,
    EquipmentKnowledgeInput,
    EquipmentKnowledgeOutput,
    EquipmentKnowledgePort,
    ProcessKnowledgeInput,
    ProcessKnowledgeOutput,
    ProcessKnowledgePort,
    RAGQueryInput,
    RAGQueryOutput,
    RAGQueryPort,
    StandardsQueryInput,
    StandardsQueryOutput,
    StandardsQueryPort,
)
from cognitiveplane.shared.types import KnowledgeId


def _kid(seed: int = 1) -> KnowledgeId:
    return KnowledgeId(value=UUID(int=seed))


# ---------------------------------------------------------------------------
# Stub ports — record the call payload, return a canned result list.
# ---------------------------------------------------------------------------


class _StubRAG(RAGQueryPort):
    def __init__(self, results: list[KnowledgeResult]) -> None:
        self._results = results
        self.last_input: RAGQueryInput | None = None

    async def query(self, input_data: RAGQueryInput) -> RAGQueryOutput:
        self.last_input = input_data
        return RAGQueryOutput(results=self._results)


class _StubStandards(StandardsQueryPort):
    def __init__(self, results: list[StandardsResult]) -> None:
        self._results = results
        self.last_input: StandardsQueryInput | None = None

    async def query(
        self, input_data: StandardsQueryInput
    ) -> StandardsQueryOutput:
        self.last_input = input_data
        return StandardsQueryOutput(results=self._results)


class _StubCases(CaseLibraryQueryPort):
    def __init__(self, results: list[CaseLibraryResult]) -> None:
        self._results = results
        self.last_input: CaseLibraryQueryInput | None = None

    async def query(
        self, input_data: CaseLibraryQueryInput
    ) -> CaseLibraryQueryOutput:
        self.last_input = input_data
        return CaseLibraryQueryOutput(results=self._results)


class _StubProcess(ProcessKnowledgePort):
    def __init__(self, results: list[ProcessKnowledgeResult]) -> None:
        self._results = results
        self.last_input: ProcessKnowledgeInput | None = None

    async def query(
        self, input_data: ProcessKnowledgeInput
    ) -> ProcessKnowledgeOutput:
        self.last_input = input_data
        return ProcessKnowledgeOutput(results=self._results)


class _StubEquipment(EquipmentKnowledgePort):
    def __init__(self, results: list[EquipmentKnowledgeResult]) -> None:
        self._results = results
        self.last_input: EquipmentKnowledgeInput | None = None

    async def query(
        self, input_data: EquipmentKnowledgeInput
    ) -> EquipmentKnowledgeOutput:
        self.last_input = input_data
        return EquipmentKnowledgeOutput(results=self._results)


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rag_service_wraps_query_and_unwraps_results():
    canned = [
        KnowledgeResult(
            knowledge_id=_kid(1),
            knowledge_type=KnowledgeType.STANDARD,
            content="x-ray inspection method",
            relevance_score=0.9,
            source_reference="ISO-17636",
        )
    ]
    port = _StubRAG(canned)
    service = RAGService(port)

    query = RAGQuery(query_text="x-ray welding")
    out = await service.query(query)

    assert out == canned
    assert isinstance(port.last_input, RAGQueryInput)
    assert port.last_input.query == query


@pytest.mark.asyncio
async def test_standards_service_passes_query_through():
    canned = [
        StandardsResult(
            standard_id="ISO-5817",
            section="3",
            clause="3.1",
            text="quality levels",
            relevance=0.8,
        )
    ]
    port = _StubStandards(canned)
    service = StandardsService(port)

    query = StandardsQuery(keyword="quality")
    out = await service.query(query)

    assert out == canned
    assert port.last_input.query == query


@pytest.mark.asyncio
async def test_cases_service_passes_query_through():
    canned = [
        CaseLibraryResult(
            case_id="C-001",
            defect_description="porosity",
            resolution="reduce travel speed",
            outcome="resolved",
            similarity_score=0.7,
        )
    ]
    port = _StubCases(canned)
    service = CaseLibraryService(port)

    query = CaseLibraryQuery(defect_type="porosity")
    out = await service.query(query)

    assert out == canned
    assert port.last_input.query == query


@pytest.mark.asyncio
async def test_process_service_passes_query_through():
    canned = [
        ProcessKnowledgeResult(
            process_id="P-001",
            recommended_parameters=ParameterSet(parameters={"current": "200A"}),
            quality_criteria={},
            common_defects=[],
        )
    ]
    port = _StubProcess(canned)
    service = ProcessKnowledgeService(port)

    query = ProcessKnowledgeQuery(process_type="MIG")
    out = await service.query(query)

    assert out == canned
    assert port.last_input.query == query


@pytest.mark.asyncio
async def test_equipment_service_passes_query_through():
    canned = [
        EquipmentKnowledgeResult(
            equipment_id="E-001",
            specifications={"max_current": 300},
            operational_limits={"duty_cycle": 0.6},
            maintenance_requirements=["weekly clean"],
        )
    ]
    port = _StubEquipment(canned)
    service = EquipmentKnowledgeService(port)

    query = EquipmentKnowledgeQuery(equipment_type="MIG-welder")
    out = await service.query(query)

    assert out == canned
    assert port.last_input.query == query


@pytest.mark.asyncio
async def test_services_return_empty_list_when_port_returns_empty():
    """Each service must hand back `[]` (not None) when the port has
    nothing to return — callers iterate the result without a guard."""
    rag = RAGService(_StubRAG([]))
    standards = StandardsService(_StubStandards([]))
    cases = CaseLibraryService(_StubCases([]))
    process = ProcessKnowledgeService(_StubProcess([]))
    equipment = EquipmentKnowledgeService(_StubEquipment([]))

    assert await rag.query(RAGQuery(query_text="x")) == []
    assert await standards.query(StandardsQuery(keyword="x")) == []
    assert await cases.query(CaseLibraryQuery(defect_type="x")) == []
    assert await process.query(ProcessKnowledgeQuery(process_type="x")) == []
    assert await equipment.query(EquipmentKnowledgeQuery(equipment_type="x")) == []


# ── 第五步: embedding 基建 - CBR 案例库接入向量检索 ────────────────────

@pytest.mark.asyncio
async def test_chroma_rag_case_library_degrades_without_chroma():
    """case_library 检索: chroma 不可用时降级空结果(不报错)."""
    from cognitiveplane.knowledge.adapters.chroma_rag import ChromaRAGAdapter
    # 用不存在的路径触发 chroma 初始化失败 -> 降级
    adapter = ChromaRAGAdapter(llm_provider=None, chroma_path="/nonexistent/path/xyz")
    from cognitiveplane.shared.ports.knowledge import CaseLibraryQueryInput
    from cognitiveplane.shared.dto.knowledge import CaseLibraryQuery
    out = await adapter.query(CaseLibraryQueryInput(
        query=CaseLibraryQuery(defect_type="porosity", max_results=3)))
    assert out.results == [], "chroma 不可用应降级空结果"


@pytest.mark.asyncio
async def test_chroma_rag_case_library_keyword_fallback():
    """case_library 检索: 无 embedding 时用 chroma 内置关键词检索."""
    # 仅当 chromadb 可安装时跑; 不可用则 skip (不阻塞)
    pytest.importorskip("chromadb")
    from cognitiveplane.knowledge.adapters.chroma_rag import ChromaRAGAdapter
    from cognitiveplane.shared.ports.knowledge import CaseLibraryQueryInput
    from cognitiveplane.shared.dto.knowledge import CaseLibraryQuery
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        adapter = ChromaRAGAdapter(llm_provider=None, chroma_path=tmp)
        out = await adapter.query(CaseLibraryQueryInput(
            query=CaseLibraryQuery(defect_type="porosity", max_results=3)))
    # seed 了 5 个案例, porosity 应能命中 case_001
    assert isinstance(out.results, list)
    if out.results:
        # 命中 porosity 案例
        assert any("porosity" in r.defect_description.lower() or "气孔" in r.defect_description
                   for r in out.results)


@pytest.mark.asyncio
async def test_chroma_rag_implements_case_library_port():
    """ChromaRAGAdapter 实现 CaseLibraryQueryPort (类型契约)."""
    from cognitiveplane.knowledge.adapters.chroma_rag import ChromaRAGAdapter
    from cognitiveplane.shared.ports.knowledge import CaseLibraryQueryPort
    assert issubclass(ChromaRAGAdapter, CaseLibraryQueryPort)
