"""Dependency assembly — build CognitiveDependencies & MCP ToolRegistry.

从 app.py 拆出的依赖装配逻辑：
  build_dependencies          — 装配 7 大平面（capability/control/knowledge/
                               memory/gateway/governance/bridge）的 CognitiveDependencies
  build_tool_registry_for_mcp — 为 MCP server 构建独立的 ToolRegistry
"""

from __future__ import annotations

import os

from cognitiveplane.control.repositories.in_memory import InMemoryBrainDecisionRepository
from cognitiveplane.capability.web_search import AutoWebSearchProvider
from cognitiveplane.gateway.adapters.in_memory import InMemoryGatewayAdapter
from cognitiveplane.knowledge.adapters.stub import StubKnowledgeAdapter
from cognitiveplane.memory.adapters.port_adapters import (
    MemoryReadAdapter,
    MemorySearchAdapter,
    MemoryWriteAdapter,
)
from cognitiveplane.memory.repositories.in_memory import InMemoryMemoryRepository
from cognitiveplane.memory.confidence import MemoryConfidenceService
from cognitiveplane.governance.escalation import EscalationTracker
from cognitiveplane.gateway.pipeline import ValidationPipeline
from cognitiveplane.governance.validators.consistency import ConsistencyValidator
from cognitiveplane.governance.validators.rule import RuleValidator
from cognitiveplane.governance.validators.safety import SafetyValidator
from cognitiveplane.governance.validators.shadow import ShadowValidator
from cognitiveplane.bootstrap.seed_knowledge import build_seed_knowledge


def build_dependencies(llm_available: bool) -> "CognitiveDependencies":
    from cognitiveplane.control.deps import (
        CapabilityDeps,
        CognitiveDependencies,
        ControlDeps,
        GatewayDeps,
        GovernanceDeps,
        KnowledgeDeps,
        MemoryDeps,
    )
    from cognitiveplane.memory.adapters.port_adapters import (
        MemoryPromotionAdapter,
        MemoryArchiveAdapter,
        MemoryConfidenceAdapter,
    )
    from cognitiveplane.capability import get_llm

    # Knowledge
    knowledge_adapter = StubKnowledgeAdapter(
        seed_responses=build_seed_knowledge()
    )
    knowledge_deps = KnowledgeDeps(
        rag_query=knowledge_adapter,
        standards_query=knowledge_adapter,
        case_library=knowledge_adapter,
        process_knowledge=knowledge_adapter,
    )

    # RAG 向量检索 — Chroma 向量库（视觉理解 + 文本推理双 collection）
    # 仅在 LLM 可用时装配（需要 embed() 生成向量）
    if llm_available:
        try:
            from cognitiveplane.knowledge.adapters.chroma_rag import ChromaRAGAdapter
            llm_provider = get_llm()
            chroma_adapter = ChromaRAGAdapter(llm_provider=llm_provider)
            knowledge_deps.vision_knowledge = chroma_adapter
            knowledge_deps.reasoning_knowledge = chroma_adapter
        except Exception:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "ChromaRAGAdapter 装配失败，RAG 向量检索不可用", exc_info=True
            )

    # Gateway
    gateway_adapter = InMemoryGatewayAdapter()
    gateway_deps = GatewayDeps(read=gateway_adapter, write=gateway_adapter)

    # Memory
    memory_repo = InMemoryMemoryRepository()
    memory_deps = MemoryDeps(
        search=MemorySearchAdapter(memory_repo),
        read=MemoryReadAdapter(memory_repo),
        write=MemoryWriteAdapter(memory_repo),
        promotion=MemoryPromotionAdapter(memory_repo),
        archive=MemoryArchiveAdapter(memory_repo),
        confidence=MemoryConfidenceAdapter(MemoryConfidenceService()),
    )

    # Governance
    escalation_tracker = EscalationTracker()
    validation_pipeline = ValidationPipeline(
        safety=SafetyValidator(),
        rule=RuleValidator(),
        shadow=ShadowValidator(),
        consistency=ConsistencyValidator(),
        escalation=escalation_tracker,
    )
    governance_deps = GovernanceDeps(
        validation=validation_pipeline,
        escalation=escalation_tracker,
    )

    # Control
    decision_repo = InMemoryBrainDecisionRepository()
    control_deps = ControlDeps(
        decision_repo=decision_repo,
    )

    # Capability — §7 line 2234 contract: only llm_provider + web_search.
    # ImageStore is session-scoped state, constructed in chat router (not in Deps).
    capability_deps = CapabilityDeps(
        llm_provider=get_llm() if llm_available else None,
        web_search=AutoWebSearchProvider(),
    )

    # Bridge — L1→L2 Temporal 桥接（boundary-pinning §6.2）
    # 默认装配 TemporalWorkflowLaunchPort；若 Temporal Server 未启动，
    # submit() 会返回 accepted=False + error，不影响 app 启动。
    from cognitiveplane.control.deps import BridgeDeps
    from cognitiveplane.bridge import (
        EventConnector,
        TemporalWorkflowLaunchPort,
        WorkflowLauncher,
    )
    temporal_host = os.environ.get("TEMPORAL_HOST", "localhost:7233")
    temporal_launch_port = TemporalWorkflowLaunchPort(
        temporal_host=temporal_host,
        namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"),
        task_queue=os.environ.get("TEMPORAL_TASK_QUEUE", "control-plane"),
    )
    workflow_launcher = WorkflowLauncher(port=temporal_launch_port)
    # P2-9 fix: 注入 gateway write port，让 EventConnector 在提交 Temporal 前
    # 调 notify_workflow_trigger 写 WeldMap workflow domain（§6.2 契约 2）
    event_connector = EventConnector(
        launcher=workflow_launcher,
        gateway_write=gateway_adapter,
    )
    bridge_deps = BridgeDeps(event_connector=event_connector)

    return CognitiveDependencies(
        capability=capability_deps,
        control=control_deps,
        knowledge=knowledge_deps,
        memory=memory_deps,
        gateway=gateway_deps,
        governance=governance_deps,
        bridge=bridge_deps,
    )


def build_tool_registry_for_mcp(deps: "CognitiveDependencies"):
    """为 MCP server 构建独立的 ToolRegistry。

    MCP server 需要一个 ToolRegistry 实例来发现工具。chat router 内部
    也有自己的 ToolRegistry（在 ReActEngine 里），两者独立但工具相同
    （都是从 deps 构建的无状态工具，除了 image_store）。

    image_store 单独创建（MCP 调用 analyze_image 时用），与 chat 的
    image_store 隔离 — MCP 客户端上传的图片需通过 MCP 路径管理。
    生产环境可改为共享 image_store。
    """
    from cognitiveplane.control.registry.tool_registry import ToolRegistry
    from cognitiveplane.interaction.image_store import ImageStore
    return ToolRegistry(deps, image_store=ImageStore())
