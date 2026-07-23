"""ChromaRAGAdapter — 基于 Chroma 向量库的 RAG 知识检索适配器。

一个适配器同时实现 VisionKnowledgePort 和 ReasoningKnowledgePort，
内部管理两个 Chroma collection（视觉理解 + 文本推理）。

设计：
  - 首次查询时自动 seed 种子数据（若 collection 为空）
  - Embedding 优先用本地 Ollama（qwen3-embedding），零成本、无需 API key
  - 回退到 LLMProvider.embed()，再回退到 Chroma 内置关键词检索
  - Chroma 做向量相似度检索 + 元数据过滤

配置：
  - OLLAMA_HOST: Ollama 服务地址（默认 http://localhost:11434）
  - OLLAMA_EMBEDDING_MODEL: embedding 模型名（默认 qwen3-embedding:4b）
  - VECTOR_DB_PATH: Chroma 数据库路径（默认 ./data/chroma_db）
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import httpx

from cognitiveplane.shared.dto.knowledge import (
    CaseLibraryQuery,
    CaseLibraryResult,
    ReasoningKnowledgeQuery,
    ReasoningKnowledgeResult,
    VisionKnowledgeQuery,
    VisionKnowledgeResult,
)
from cognitiveplane.shared.ports.knowledge import (
    CaseLibraryQueryInput,
    CaseLibraryQueryOutput,
    CaseLibraryQueryPort,
    ReasoningKnowledgeInput,
    ReasoningKnowledgeOutput,
    ReasoningKnowledgePort,
    VisionKnowledgeInput,
    VisionKnowledgeOutput,
    VisionKnowledgePort,
)

if TYPE_CHECKING:
    from cognitiveplane.capability.provider import LLMProvider

logger = logging.getLogger(__name__)

# Chroma 数据库默认路径
_DEFAULT_CHROMA_PATH = os.environ.get("VECTOR_DB_PATH", "./data/chroma_db")
# Ollama embedding 配置
_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_EMBEDDING_MODEL = os.environ.get("OLLAMA_EMBEDDING_MODEL", "qwen3-embedding:4b")


class ChromaRAGAdapter(VisionKnowledgePort, ReasoningKnowledgePort, CaseLibraryQueryPort):
    """Chroma 向量库 RAG 适配器 — 视觉理解 + 文本推理双 collection。

    Embedding 优先级：Ollama（本地零成本）> LLMProvider.embed() > Chroma 内置。

    用法：
        adapter = ChromaRAGAdapter(llm_provider)
        # 首次查询自动 seed 数据
        result = await adapter.query(VisionKnowledgeInput(query=...))
    """

    def __init__(
        self,
        llm_provider: "LLMProvider | None" = None,
        chroma_path: str = _DEFAULT_CHROMA_PATH,
        ollama_host: str = _OLLAMA_HOST,
        ollama_model: str = _OLLAMA_EMBEDDING_MODEL,
    ) -> None:
        self._llm = llm_provider
        self._chroma_path = chroma_path
        self._ollama_host = ollama_host.rstrip("/")
        self._ollama_model = ollama_model
        self._ollama_available: bool | None = None  # None=未检测
        self._client = None
        self._vision_collection = None
        self._reasoning_collection = None
        self._case_collection = None  # Op-新: CBR 案例库 collection (L2_CASE)
        self._vision_seeded = False
        self._reasoning_seeded = False
        self._case_seeded = False

    # ── 延迟初始化 ──

    def _ensure_collections(self) -> None:
        """延迟初始化 Chroma client 和两个 collection。"""
        if self._client is not None:
            return
        try:
            import chromadb
            self._client = chromadb.PersistentClient(path=self._chroma_path)
            self._vision_collection = self._client.get_or_create_collection(
                name="weld_vision",
                metadata={"description": "工业图像数据集元信息（视觉理解 RAG）"},
            )
            self._reasoning_collection = self._client.get_or_create_collection(
                name="weld_reasoning",
                metadata={"description": "工业流程/标准/推理模式语料（文本推理 RAG）"},
            )
            self._case_collection = self._client.get_or_create_collection(
                name="weld_cases",
                metadata={"description": "CBR 案例库（缺陷处置案例，L2_CASE 分层记忆）"},
            )
            logger.info("[chroma_rag] 初始化完成 path=%s", self._chroma_path)
        except Exception:
            logger.warning("[chroma_rag] Chroma 初始化失败，降级为关键词匹配", exc_info=True)
            self._client = None

    # ── 种子数据填充 ──

    def _seed_vision(self) -> None:
        """填充视觉理解 collection 的种子数据。"""
        if self._vision_seeded or self._vision_collection is None:
            return
        if self._vision_collection.count() > 0:
            self._vision_seeded = True
            return
        try:
            from cognitiveplane.bootstrap.seed_vision_knowledge import build_vision_knowledge_seed
            seeds = build_vision_knowledge_seed()
            texts = [s["search_text"] for s in seeds]
            # 用 Ollama 批量生成 embedding（同步调用，seed 在首次查询时触发）
            embeddings = self._ollama_embed_sync(texts)

            self._vision_collection.add(
                ids=[f"vision_{i}" for i in range(len(seeds))],
                documents=texts,
                metadatas=[{
                    "dataset_name": s["dataset_name"],
                    "description": s["description"],
                    "source_url": s["source_url"],
                    "image_count": s["image_count"],
                    "defect_types": "|".join(s["defect_types"]),
                    "modality": s["modality"],
                    "industry": s["industry"],
                    "applicable_scenarios": "|".join(s["applicable_scenarios"]),
                    "license": s["license"],
                } for s in seeds],
                embeddings=embeddings,
            )
            self._vision_seeded = True
            logger.info("[chroma_rag] vision collection seeded: %d 条 (embedding=%s)",
                        len(seeds), "ollama" if embeddings else "none")
        except Exception:
            logger.warning("[chroma_rag] vision seed 失败", exc_info=True)
            self._vision_seeded = True

    def _seed_reasoning(self) -> None:
        """填充文本推理 collection 的种子数据。"""
        if self._reasoning_seeded or self._reasoning_collection is None:
            return
        if self._reasoning_collection.count() > 0:
            self._reasoning_seeded = True
            return
        try:
            from cognitiveplane.bootstrap.seed_reasoning_knowledge import build_reasoning_knowledge_seed
            seeds = build_reasoning_knowledge_seed()
            texts = [s["search_text"] for s in seeds]
            embeddings = self._ollama_embed_sync(texts)

            self._reasoning_collection.add(
                ids=[f"reasoning_{i}" for i in range(len(seeds))],
                documents=texts,
                metadatas=[{
                    "title": s["title"],
                    "source": s["source"],
                    "knowledge_type": s["knowledge_type"],
                    "content": s["content"],
                    "applicable_context": s["applicable_context"],
                } for s in seeds],
                embeddings=embeddings,
            )
            self._reasoning_seeded = True
            logger.info("[chroma_rag] reasoning collection seeded: %d 条 (embedding=%s)",
                        len(seeds), "ollama" if embeddings else "none")
        except Exception:
            logger.warning("[chroma_rag] reasoning seed 失败", exc_info=True)
            self._reasoning_seeded = True

    def _seed_case(self) -> None:
        """填充 CBR 案例库 collection 的种子数据 (L2_CASE 分层记忆).

        案例结构: 缺陷类型 -> 处置方案 -> 结果. 供 case_library_correction 纠错、
        design_workflow 检索参考. 案例错误时由 case_library_correction 剔除/修正.
        """
        if self._case_seeded or self._case_collection is None:
            return
        if self._case_collection.count() > 0:
            self._case_seeded = True
            return
        # 内置典型案例种子 (焊检域 CBR)
        cases = [
            {"case_id": "case_001", "defect_type": "porosity",
             "defect_description": "气孔密集分布于焊缝中心，直径0.5-2mm",
             "resolution": "打磨清除后重新焊接，增加保护气体流量",
             "outcome": "PASS", "search_text": "气孔 porosity 焊缝中心 保护气体不足"},
            {"case_id": "case_002", "defect_type": "crack",
             "defect_description": "纵向裂纹沿焊缝走向延伸约15mm",
             "resolution": "碳弧气刨清除裂纹+预热后重焊，控制层间温度",
             "outcome": "REWORK", "search_text": "裂纹 crack 纵向 预热 层间温度"},
            {"case_id": "case_003", "defect_type": "undercut",
             "defect_description": "焊趾处咬边深度0.8mm，连续长度20mm",
             "resolution": "补焊焊趾，降低电流/提高速度重新走道",
             "outcome": "REWORK", "search_text": "咬边 undercut 焊趾 电流过大"},
            {"case_id": "case_004", "defect_type": "incomplete_penetration",
             "defect_description": "根部未熔透，X光显示黑影连续",
             "resolution": "背面清根后重焊，增大根部间隙",
             "outcome": "REWORK", "search_text": "未熔透 incomplete_penetration 根部 清根"},
            {"case_id": "case_005", "defect_type": "slag_inclusion",
             "defect_description": "夹渣呈条状分布于焊缝内部",
             "resolution": "清除夹渣区域，改进焊道间清理工艺",
             "outcome": "REWORK", "search_text": "夹渣 slag_inclusion 层间清理"},
        ]
        try:
            texts = [c["search_text"] for c in cases]
            embeddings = self._ollama_embed_sync(texts)
            self._case_collection.add(
                ids=[c["case_id"] for c in cases],
                documents=texts,
                metadatas=[{
                    "case_id": c["case_id"], "defect_type": c["defect_type"],
                    "defect_description": c["defect_description"],
                    "resolution": c["resolution"], "outcome": c["outcome"],
                } for c in cases],
                embeddings=embeddings,
            )
            self._case_seeded = True
            logger.info("[chroma_rag] case collection seeded: %d 条 (embedding=%s)",
                        len(cases), "ollama" if embeddings else "none")
        except Exception:
            logger.warning("[chroma_rag] case seed 失败", exc_info=True)
            self._case_seeded = True

    # ── Port 实现 ──

    async def query(self, input_data):
        """统一 query 入口 - 按 input 类型分派到对应 collection."""
        if isinstance(input_data, VisionKnowledgeInput):
            return await self._query_vision(input_data)
        elif isinstance(input_data, ReasoningKnowledgeInput):
            return await self._query_reasoning(input_data)
        elif isinstance(input_data, CaseLibraryQueryInput):
            return await self._query_case(input_data)
        else:
            raise ValueError(f"Unsupported input type: {type(input_data)}")
        """统一 query 入口 — 按 input 类型分派到对应 collection。"""
        if isinstance(input_data, VisionKnowledgeInput):
            return await self._query_vision(input_data)
        elif isinstance(input_data, ReasoningKnowledgeInput):
            return await self._query_reasoning(input_data)
        else:
            raise ValueError(f"Unsupported input type: {type(input_data)}")

    async def _query_vision(self, input_data: VisionKnowledgeInput) -> VisionKnowledgeOutput:
        """视觉理解 collection 检索。"""
        query = input_data.query
        self._ensure_collections()
        if self._vision_collection is None:
            # Chroma 不可用 — 降级
            return VisionKnowledgeOutput(results=[])

        if not self._vision_seeded:
            self._seed_vision()

        # 构建 where 过滤条件
        where = self._build_vision_where(query)
        # 获取 query embedding
        query_embedding = await self._embed_query(query.query_text)
        n_results = query.max_results

        try:
            if query_embedding is not None:
                results = self._vision_collection.query(
                    query_embeddings=[query_embedding],
                    n_results=n_results,
                    where=where if where else None,
                )
            else:
                # 无 embedding — 用 Chroma 内置文档检索（关键词 fallback）
                results = self._vision_collection.query(
                    query_texts=[query.query_text],
                    n_results=n_results,
                    where=where if where else None,
                )
        except Exception:
            logger.warning("[chroma_rag] vision query 失败", exc_info=True)
            return VisionKnowledgeOutput(results=[])

        return self._parse_vision_results(results)

    async def _query_reasoning(self, input_data: ReasoningKnowledgeInput) -> ReasoningKnowledgeOutput:
        """文本推理 collection 检索。"""
        query = input_data.query
        self._ensure_collections()
        if self._reasoning_collection is None:
            return ReasoningKnowledgeOutput(results=[])

        if not self._reasoning_seeded:
            self._seed_reasoning()

        where = self._build_reasoning_where(query)
        query_embedding = await self._embed_query(query.query_text)
        n_results = query.max_results

        try:
            if query_embedding is not None:
                results = self._reasoning_collection.query(
                    query_embeddings=[query_embedding],
                    n_results=n_results,
                    where=where if where else None,
                )
            else:
                results = self._reasoning_collection.query(
                    query_texts=[query.query_text],
                    n_results=n_results,
                    where=where if where else None,
                )
        except Exception:
            logger.warning("[chroma_rag] reasoning query 失败", exc_info=True)
            return ReasoningKnowledgeOutput(results=[])

        return self._parse_reasoning_results(results)

    async def _query_case(self, input_data: CaseLibraryQueryInput) -> CaseLibraryQueryOutput:
        """CBR 案例库检索 (L2_CASE). defect_type + similarity_context 拼 search text."""
        query = input_data.query
        self._ensure_collections()
        if self._case_collection is None:
            return CaseLibraryQueryOutput(results=[])
        if not self._case_seeded:
            self._seed_case()

        # 拼 search text: defect_type + similarity_context
        parts = [query.defect_type or ""]
        ctx = query.similarity_context or {}
        for k, v in ctx.items():
            parts.append(f"{k}: {v}")
        search_text = " ".join(p for p in parts if p)
        if not search_text:
            search_text = "weld defect case"

        query_embedding = await self._embed_query(search_text)
        n_results = query.max_results
        # 按 defect_type 过滤 (若有)
        where = {"defect_type": query.defect_type} if query.defect_type else None
        try:
            if query_embedding is not None:
                results = self._case_collection.query(
                    query_embeddings=[query_embedding],
                    n_results=n_results, where=where,
                )
            else:
                results = self._case_collection.query(
                    query_texts=[search_text], n_results=n_results, where=where,
                )
        except Exception:
            logger.warning("[chroma_rag] case query 失败", exc_info=True)
            return CaseLibraryQueryOutput(results=[])
        return self._parse_case_results(results)

    def _parse_case_results(self, results) -> CaseLibraryQueryOutput:
        """解析 Chroma 案例检索结果 -> CaseLibraryResult."""
        if not results or not results.get("ids") or not results["ids"][0]:
            return CaseLibraryQueryOutput(results=[])
        out = []
        ids = results["ids"][0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]
        for i, mid in enumerate(ids):
            m = metas[i] if i < len(metas) else {}
            dist = dists[i] if i < len(dists) else 1.0
            sim = max(0.0, 1.0 - dist)  # distance -> similarity
            out.append(CaseLibraryResult(
                case_id=m.get("case_id", mid),
                defect_description=m.get("defect_description", ""),
                resolution=m.get("resolution", ""),
                outcome=m.get("outcome", ""),
                similarity_score=round(sim, 3),
            ))
        return CaseLibraryQueryOutput(results=out)

    # ── 辅助方法 ──

    async def _embed_query(self, text: str) -> list[float] | None:
        """生成 query 的 embedding — 优先 Ollama，回退 LLMProvider。"""
        # 1. 优先 Ollama
        if self._ollama_available is not False:
            emb = await self._ollama_embed_async(text)
            if emb is not None:
                return emb
        # 2. 回退 LLMProvider
        if self._llm is not None:
            try:
                embeddings = await self._llm.embed([text])
                return embeddings[0] if embeddings else None
            except Exception:
                logger.debug("[chroma_rag] LLMProvider embed 失败", exc_info=True)
        return None

    # ── Ollama embedding ──

    async def _ollama_embed_async(self, text: str) -> list[float] | None:
        """异步调用 Ollama 生成单条文本 embedding。"""
        if self._ollama_available is False:
            return None
        try:
            # 绕过代理 — Ollama 是本地服务，走代理会 502
            async with httpx.AsyncClient(timeout=10.0, proxy=None, trust_env=False) as client:
                resp = await client.post(
                    f"{self._ollama_host}/api/embeddings",
                    json={"model": self._ollama_model, "prompt": text},
                )
                resp.raise_for_status()
                data = resp.json()
                emb = data.get("embedding")
                if emb:
                    self._ollama_available = True
                    return emb
                return None
        except Exception:
            if self._ollama_available is None:
                logger.info("[chroma_rag] Ollama 不可用，回退到 LLMProvider/关键词检索")
            self._ollama_available = False
            return None

    def _ollama_embed_sync(self, texts: list[str]) -> list[list[float]] | None:
        """同步调用 Ollama 批量生成 embedding（用于 seed 阶段）。"""
        if self._ollama_available is False:
            return None
        embeddings: list[list[float]] = []
        try:
            # 绕过代理 — Ollama 是本地服务
            with httpx.Client(timeout=30.0, proxy=None, trust_env=False) as client:
                for text in texts:
                    resp = client.post(
                        f"{self._ollama_host}/api/embeddings",
                        json={"model": self._ollama_model, "prompt": text},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    emb = data.get("embedding")
                    if emb:
                        embeddings.append(emb)
                    else:
                        return None
            if len(embeddings) == len(texts):
                self._ollama_available = True
                logger.info("[chroma_rag] Ollama embedding 成功: %d 条", len(embeddings))
                return embeddings
            return None
        except Exception:
            logger.info("[chroma_rag] Ollama 批量 embedding 失败，seed 数据将用 Chroma 内置检索")
            self._ollama_available = False
            return None

    @staticmethod
    def _build_vision_where(query: VisionKnowledgeQuery) -> dict | None:
        """构建 Chroma where 过滤条件。"""
        conditions = []
        if query.defect_type:
            conditions.append({"defect_types": {"$contains": query.defect_type}})
        if query.industry:
            conditions.append({"industry": {"$eq": query.industry}})
        if query.modality:
            conditions.append({"modality": {"$eq": query.modality}})
        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    @staticmethod
    def _build_reasoning_where(query: ReasoningKnowledgeQuery) -> dict | None:
        """构建 Chroma where 过滤条件。"""
        if query.knowledge_type:
            return {"knowledge_type": {"$eq": query.knowledge_type}}
        return None

    @staticmethod
    def _parse_vision_results(results: dict) -> VisionKnowledgeOutput:
        """解析 Chroma 返回结果为 VisionKnowledgeResult 列表。"""
        out = []
        ids = results.get("ids", [[]])
        metadatas = results.get("metadatas", [[]])
        distances = results.get("distances", [[]])
        for i, meta in enumerate(metadatas[0] if metadatas else []):
            dist = distances[0][i] if distances and i < len(distances[0]) else 1.0
            # Chroma 返回 distance（越小越相似），转成 relevance（0-1）
            relevance = max(0.0, min(1.0, 1.0 - dist))
            out.append(VisionKnowledgeResult(
                dataset_name=meta.get("dataset_name", ""),
                description=meta.get("description", ""),
                source_url=meta.get("source_url", ""),
                image_count=meta.get("image_count", ""),
                defect_types=meta.get("defect_types", "").split("|") if meta.get("defect_types") else [],
                modality=meta.get("modality", ""),
                industry=meta.get("industry", ""),
                applicable_scenarios=meta.get("applicable_scenarios", "").split("|") if meta.get("applicable_scenarios") else [],
                license=meta.get("license", ""),
                relevance=relevance,
            ))
        return VisionKnowledgeOutput(results=out)

    @staticmethod
    def _parse_reasoning_results(results: dict) -> ReasoningKnowledgeOutput:
        """解析 Chroma 返回结果为 ReasoningKnowledgeResult 列表。"""
        out = []
        metadatas = results.get("metadatas", [[]])
        distances = results.get("distances", [[]])
        for i, meta in enumerate(metadatas[0] if metadatas else []):
            dist = distances[0][i] if distances and i < len(distances[0]) else 1.0
            relevance = max(0.0, min(1.0, 1.0 - dist))
            out.append(ReasoningKnowledgeResult(
                title=meta.get("title", ""),
                source=meta.get("source", ""),
                knowledge_type=meta.get("knowledge_type", ""),
                content=meta.get("content", ""),
                applicable_context=meta.get("applicable_context", ""),
                relevance=relevance,
            ))
        return ReasoningKnowledgeOutput(results=out)
