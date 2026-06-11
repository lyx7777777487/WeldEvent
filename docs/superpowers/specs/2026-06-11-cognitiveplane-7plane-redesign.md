# CognitivePlane Architecture Redesign — Aligned with WeldEvent 6-Layer System

**Date:** 2026-06-11
**Status:** Draft v5 (Phased execution + LangGraph/DSPy/Instructor/NATS/Milvus/MLLM/Langfuse)
**Scope:** L1 Cognitive Plane internal restructuring per IAOS V4.1 (advice.md)
**System Context:** WeldEvent 6-Layer Architecture (L1–L6)

---

## 0. System-Level Architecture

WeldEvent is a 6-layer system. This spec covers **L1 Cognitive Plane only**.

```
L1 Cognitive Plane  ← 本文档范围 — Brain决策，通过CognitiveGateway写WeldMap
L2 Control Plane    ← Temporal Server — DAG工作流编排，CP0-CP6
L3 Execution Plane  ← Agent Pool — 7+1 Agent，只响应Temporal调度
L4 Capability Plane ← 工具库 — 分割模型/CV/风险引擎/渲染
L5 Data Plane       ← WeldMap Store — 黑板存储，8个数据域，单一真相来源
L6 Infrastructure   ← K8s/NATS/OTel — 运行支撑
```

**核心原则（来自架构方案）：**
- 确定性优先：规则引擎与Temporal负责确定性执行，Brain永远在执行循环之外
- 职责分离：Brain只做认知决策，Temporal只做执行保证，Agent只响应调度
- 单一真相来源：所有层的状态读写必须经过WeldMap，禁止Agent间直接通信
- 最小耦合：各层通过Port接口通信
- 人在回路：关键判定节点保留人工审查通道，Brain建议须经人工确认才能生效

---

## 0.5 Phased Execution — 这不是全量重写

本spec描述的是**理想终态**，不是一次性交付计划。执行分三阶段，严格有序：

### Phase 0: 紧急修复（不改结构，改几行代码）

P0问题**不需要重构就能修**，应立即独立完成：

| # | P0 Issue | Fix | 改动量 |
|---|---------|-----|--------|
| 1 | InterventionMode None crash | 加 `if port is None` 前置检查 | 1行 |
| 2 | UrgencyLevel enum mismatch | 修正为 ROUTINE/URGENT/CRITICAL | 2行 |
| 3 | Memory write chain broken | BrainOrchestrator.execute() 末尾加 `await self._memory_write.store(...)` | 5行 |
| 4 | Memory search status_filter | 写入时 `promotion_status=VALIDATED`; 默认filter加VALIDATED | 3行 |

### Phase 1: 结构重组（本spec核心）

**只做包结构重组 + DI改造 + 删死代码**，不引入新技术：

- 7-plane包结构重组（brain/→control/, 删deepagents/, modes/, 等）
- CognitiveDependencies替代dict[str, Any]
- 删除死代码（commands.py, queries.py, 未使用的DTO）
- ReAct Engine替换5个Mode类（手写版本，不用LangGraph）
- CognitiveGateway写端口（手写版本，不用NATS）
- P1-P2问题修复（EscalationTracker per-case, ValidationPipeline纯计算, etc.）

### Phase 2: 技术引入（按价值/风险排序）

逐个引入，每引入一个都确保全部测试通过后再引入下一个：

| 顺序 | 技术 | 替代什么 | 前置条件 |
|------|------|---------|---------|
| 2a | LangGraph | 手写ReActEngine + CheckpointManager | Phase 1 ReAct Engine可工作 |
| 2b | Instructor | 手写_try_parse() | Phase 1 LLMProvider可工作 |
| 2c | DSPy Signatures | 手写Prompt | Phase 1 IntentClassifier可工作 |
| 2d | NATS JetStream | InMemoryEventBus | Phase 1 CognitiveGateway可工作 |
| 2e | Milvus | pgvector占位 | Phase 1 DualWrite可工作 |
| 2f | Langfuse | LLMCallTracker | Phase 1 observability可工作 |
| 2g | Reranker + Hybrid Search | StubKnowledgeAdapter | Phase 1 knowledge ports可工作 |
| 2h | MLLM Vision | 无多模态 | Phase 1 multimodal.py可工作 |

**每个技术都可以独立回退。** 如果LangGraph在某场景下表现不佳，回退到手写ReAct即可。

### Orchestrator vs ReAct Engine：明确的职责边界

```
用户输入 "设计Q345R 22mm的检测方案"
  │
  ▼
[ReAct Engine] — 外层交互循环（LangGraph StateGraph，Phase 2引入）
  │  LLM思考: "用户要设计检测方案，需要查标准和历史案例"
  │  调用 search_standards("Q345R 22mm")
  │  观察: 标准要求...
  │  调用 design_workflow(objective, constraints, context)
  │     │
  │     ▼
  │  [BrainOrchestrator] — 内层决策管线（纯决策逻辑，不是StateGraph）
  │     persona→knowledge→memory→reasoning→validation→publish
  │     返回: BrainDecision
  │     │
  │  观察: 决策结果...
  │  LLM思考: "方案已生成，需要向工程师确认"
  │  调用 request_confirmation("是否采用此方案?")
  │  ...
  ▼
[Response] — 最终回复用户
```

**关键约束：**
- ReAct Engine是唯一的外层循环，BrainCore的LLM推理在此发生
- BrainOrchestrator是design_workflow Tool的内部实现，不是独立执行引擎
- Orchestrator不接受用户输入，不与LLM直接交互，只做确定性决策管线
- Orchestrator不需要是StateGraph——它是Tool内部的步骤序列，由ReAct循环驱动

### CognitiveDependencies 拆分：按Plane分组

30+字段的巨型dataclass是设计坏味道。拆分为按Plane分组的小容器，composition root只组装顶层：

```python
@dataclass
class CapabilityDeps:
    llm_provider: LLMProvider
    instructor: InstructorClient       # Phase 2

@dataclass
class ControlDeps:
    orchestrator: BrainOrchestrator
    supervisor: SupervisorCenter
    tool_policy: ToolPolicy
    hooks: list[BeforeToolHook]
    react_graph: CompiledStateGraph     # Phase 2: LangGraph

@dataclass
class KnowledgeDeps:
    rag_query: RAGQueryPort
    standards_query: StandardsQueryPort
    case_library: CaseLibraryQueryPort
    process_knowledge: ProcessKnowledgePort

@dataclass
class MemoryDeps:
    search: MemorySearchPort
    read: MemoryReadPort
    write: MemoryWritePort
    block_manager: BlockManager
    compactor: ContextCompactor

@dataclass
class GatewayDeps:
    read: CognitiveGatewayReadPort
    write: CognitiveGatewayWritePort

@dataclass
class GovernanceDeps:
    validation: ValidationPipelinePort
    review_repo: HumanReviewRequestRepository
    tool_policy: ToolPolicy             # shared with ControlDeps

@dataclass
class CognitiveDependencies:
    """Top-level container. Each plane assembles its own deps internally."""
    capability: CapabilityDeps
    control: ControlDeps
    knowledge: KnowledgeDeps
    memory: MemoryDeps
    gateway: GatewayDeps
    governance: GovernanceDeps
```

**好处：**
- 各Plane的deps可独立测试
- 新技术引入只影响对应Plane的deps（如Instructor只改CapabilityDeps）
- composition root的组装逻辑更清晰（6步而非30步）

---

## 1. Design Goals

Restructure `cognitiveplane/` (L1) from a monolithic package with architectural problems into a clean internal module structure aligned with advice.md's 7-Plane pattern, while respecting the 6-layer system architecture and WeldMap blackboard pattern.

### Decisions Made

| Decision | Conclusion | Rationale |
|----------|-----------|-----------|
| Scope | L1 Cognitive Plane only | Other layers are independent (L2-L6) |
| Temporal | L2, not in cognitiveplane | Bridge via CognitiveGateway → WeldMap → Temporal Signal |
| MCP | Deferred | Reduce Phase 1 risk |
| ChatUI | Remove, use FastAPI | Production API; WebSocket for streaming |
| Storage | PG + Redis + MinIO (L5/L6) | Cognitive plane has adapters to L5/L6, doesn't own infrastructure |
| DeepAgents | Don't depend, borrow ideas | advice.md: self-build BrainCore |
| Package structure | Single package, modules by internal Plane | Simpler than monorepo, clearer than current |
| Interaction model | ReAct unified model (Reasoning + Acting) | BrainCore自主推理+调用Tool，取消独立Mode类 |
| Intent understanding | 3-tier: ReAct+FunctionCalling → StructuredOutput → SemanticEmbedding+Rules | 替代关键词匹配 |
| WeldMap | Single source of truth | All cross-layer state through WeldMap, CognitiveGateway as write port |
| P0-P3 fixes | Must address all | Architecture doc identified 19 issues |

---

## 2. Package Structure

```
cognitiveplane/
├── __init__.py
├── app.py                        # FastAPI app + composition root
│
├── interaction/                  # Interaction Plane — 用户交互入口
│   ├── __init__.py
│   ├── api/                      # FastAPI routers
│   │   ├── __init__.py
│   │   ├── chat.py               # POST /chat, WebSocket /ws
│   │   └── notifications.py      # GET /notifications (system→human pushes)
│   ├── ports.py                  # Interaction-internal port ABCs
│   ├── context.py                # ActiveContext + ContextResolver
│   ├── session.py                # Session management (per-operator state)
│   ├── base.py                   # UserMessage, InteractionResponse, Notification
│   └── multimodal.py             # Multi-modal input parser (text/image/annotation)
│
├── governance/                   # Governance Plane — 审批/验证/升级
│   ├── __init__.py
│   ├── ports.py                  # ValidationPipelinePort, HumanReviewRepository, ApprovalServicePort
│   ├── approval.py               # ApprovalService (Plan/Approve/Execute from Cline)
│   ├── tool_policy.py            # 🆕 ToolPolicy (from Cline) — {enabled, autoApprove} per-tool + wildcard
│   ├── escalation.py             # EscalationTracker (per-case isolation, persisted)
│   ├── review.py                 # HumanReview workflow
│   ├── permissions.py            # RBAC (Phase2: OPA)
│   ├── pipeline.py               # 4-stage ValidationPipeline (Safety→Rule→Shadow→Consistency)
│   ├── validators/
│   │   ├── safety.py
│   │   ├── rule.py
│   │   ├── shadow.py
│   │   └── consistency.py
│   └── exceptions.py
│
├── control/                      # Control Plane — BrainCore决策引擎
│   ├── __init__.py
│   ├── ports.py                  # BrainDecisionRepository, ReasoningPort, PlanningPort, ReflectionPort, SubAgentPort
│   ├── react.py                  # 🆕 ReAct Engine — Reasoning+Acting循环（核心）
│   ├── tool_registry.py          # 🆕 Tool Registry — Brain可调用的全部Tool定义
│   ├── tools/                    # 🆕 Tool implementations
│   │   ├── __init__.py
│   │   ├── search_standards.py   # 查询标准参数
│   │   ├── search_cases.py       # 查询案例库
│   │   ├── read_weldmap.py       # 读取WeldMap状态
│   │   ├── design_workflow.py    # 设计检测方案
│   │   ├── adjust_parameter.py   # 调整检测参数
│   │   ├── request_confirmation.py # 向人工请求确认（Agent→人）
│   │   ├── escalate.py           # 升级到人工干预
│   │   ├── explain_decision.py   # 解释决策推理
│   │   └── archive_memory.py     # 🆕 Agent-controlled memory archival (from Letta)
│   ├── hooks.py                  # 🆕 beforeTool hooks (from OpenHands/Cline) — skip/stop/modify interception
│   ├── event_log.py              # 🆕 Append-only EventLog (from OpenHands) — state transitions as events
│   ├── checkpoint.py             # 🆕 Decision checkpoint/rollback (from Cline) — state snapshots for recovery
│   ├── orchestrator.py           # BrainOrchestrator (typed injection, drives ReAct, stateless per OpenHands pattern)
│   ├── state_machine.py          # BrainStateMachine (pure function, 7 states)
│   ├── persona.py                # PersonaSelector (Planner/Copilot/CAA)
│   ├── reasoning_mode.py         # ReasoningModeSelector (ROUTINE/ADAPTIVE/EXPLORATORY)
│   ├── planner.py                # Self-built Planner (borrow DeepAgents Task Decomposition)
│   ├── reflector.py              # Self-built Reflector (borrow DeepAgents Reflection)
│   ├── sub_agent.py              # Self-built Sub-Agent delegation (borrow DeepAgents Sub-Agent)
│   ├── supervisor.py             # SupervisorCenter (Health/Loop/Timeout/Fallback)
│   ├── decision_factory.py       # DecisionFactory (multi-output-type by Persona)
│   ├── fallback.py               # 🆕 3-tier fallback strategy (ReAct→Structured→Embedding+Rules)
│   └── exceptions.py
│
├── gateway/                      # CognitiveGateway — 认知关口（L1→WeldMap唯一写出通道）
│   ├── __init__.py
│   ├── ports.py                  # CognitiveGatewayWritePort, CognitiveGatewayReadPort
│   ├── write_gateway.py          # Decision/Escalation/Instruction → WeldMap
│   ├── read_gateway.py           # WeldMap查询（workflow/image/annotation/decision域只读）
│   ├── events.py                 # 🆕 ActionEvent/ObservationEvent pair (from OpenHands) — gateway I/O as events
│   └── weldmap_client.py         # WeldMap HTTP/gRPC client adapter
│
├── knowledge/                    # Knowledge Plane — RAG知识检索
│   ├── __init__.py
│   ├── ports.py                  # RAGQueryPort, StandardsQueryPort, CaseLibraryQueryPort, etc.
│   ├── rag.py
│   ├── standards.py
│   ├── cases.py
│   ├── process.py
│   └── equipment.py
│
├── memory/                       # Memory Plane — 工业记忆L0-L5
│   ├── __init__.py
│   ├── ports.py                  # MemorySearchPort, MemoryReadPort, MemoryWritePort, etc.
│   ├── hierarchy.py              # L0-L5 level definition + promotion rules
│   ├── blocks.py                 # 🆕 Block-based working memory (from Letta) — versioned, compiled to prompt
│   ├── search.py                 # Multi-level search (hybrid: vector + FTS + RRF from Letta)
│   ├── promotion.py              # Memory promotion (RAW→VALIDATED→PROMOTED)
│   ├── compaction.py             # 🆕 Context compaction with fallback chain (from Letta) — sliding_window→all→self_compact
│   ├── dual_write.py             # 🆕 Dual-write persistence (from Letta) — PG + Redis/Milvus sync
│   ├── archive.py
│   └── confidence.py             # Real confidence scoring (fix P1-14: no more constant 0.5)
│
├── capability/                   # LLM Capability — 大模型推理能力（L1内部，非完整L4）
│   ├── __init__.py
│   ├── ports.py                  # LLMProvider ABC
│   ├── provider.py               # LLMRequest/LLMResponse models
│   ├── deepseek.py               # DeepSeek adapter
│   ├── openai_compat.py          # OpenAI-compatible adapter
│   ├── mock.py                   # Mock LLM for testing
│   └── config.py                 # LLMConfig, OpenAIConfig
│
├── copilot/                      # Industrial Copilot (B-level)
│   ├── __init__.py
│   ├── qa.py                     # Standard/process/case Q&A
│   ├── explain.py                # Decision explanation + reasoning trace
│   ├── investigate.py            # Anomaly root cause analysis
│   ├── govern.py                 # Approval/compliance assistance
│   └── operate.py                # Production line operation guidance
│
├── adapters/                     # L5/L6适配器（认知层连接外部基础设施的唯一入口）
│   ├── __init__.py
│   ├── database/                 # PostgreSQL adapters
│   │   ├── __init__.py
│   │   ├── engine.py             # AsyncEngine + session management
│   │   ├── models.py             # SQLAlchemy ORM models (6 tables)
│   │   ├── decision_repo.py      # BrainDecision → PG
│   │   ├── memory_repo.py        # Memory → Redis(L0-L1) + PG(L2-L5)
│   │   ├── knowledge_repo.py     # Knowledge → PG (+ Milvus Phase2)
│   │   ├── workflow_repo.py      # WorkflowState → PG (via WeldMap read)
│   │   ├── review_repo.py        # HumanReview → PG
│   │   └── audit_repo.py         # AuditEntry → PG (L5 audit memory)
│   ├── cache/
│   │   ├── __init__.py
│   │   └── redis_client.py       # Redis L0/L1 cache + session state
│   ├── storage/
│   │   ├── __init__.py
│   │   └── minio_client.py       # MinIO image/report storage
│   ├── observability/
│   │   ├── __init__.py
│   │   ├── tracing.py            # OpenTelemetry trace setup
│   │   └── metrics.py            # OpenTelemetry metrics setup
│   ├── weldmap/
│   │   ├── __init__.py
│   │   └── weldmap_http.py       # WeldMap HTTP client (L5 Data Plane)
│   └── migrations/               # Alembic migrations
│       └── ...
│
└── shared/                       # 共享内核（最小化，仅L1内部共享）
    ├── __init__.py
    ├── types.py                  # ID types (DecisionId, CaseId, SessionId, etc.)
    ├── enums.py                  # Cross-Plane shared enums (BrainStateType, ReasoningMode, etc.)
    │                             # NOTE: per-Plane enums live in each Plane's __init__.py
    └── dto/                      # Cross-Plane shared DTOs only
        ├── __init__.py
        ├── context.py            # ContextSnapshot, DomainEvent, WeldMapSnapshot (unified!)
        ├── decision.py           # BrainDecision + DecisionOutput union (15 types)
        ├── memory.py             # MemorySearchQuery, MemoryRecord
        ├── knowledge.py          # RAGQuery, KnowledgeResult
        ├── validation.py         # ValidationResult
        └── escalation.py         # Escalation
```

### Key Changes from v1

| v1 Design | v2 Design | Reason |
|-----------|-----------|--------|
| `execution/` (Gateway + Publisher + TemporalBridge) | `gateway/` (CognitiveGateway only) | Execution is L2/L3, not L1. Brain writes to WeldMap via CognitiveGateway |
| `infrastructure/` (PG/Redis/MinIO as a Plane) | `adapters/` (thin layer connecting to L5/L6) | Infrastructure is L5/L6, cognitive plane only has adapters |
| `capability/` as full L4 | `capability/` as LLM only within L1 | Full L4 (CV/segmentation/risk engine) is a separate layer |
| Direct port calls between modules | All cross-layer state through WeldMap | Architecture principle: single source of truth |
| Two separate WeldMapSnapshot DTOs | Unified WeldMapSnapshot in shared/dto/context.py | Fix P1-6: unify the two conflicting DTOs |

### Key Changes from v3 → v4 (Borrowed Patterns)

| v3 Design | v4 Design | Source |
|-----------|-----------|--------|
| No beforeTool hooks | `control/hooks.py` with SafetyHook + PolicyHook | OpenHands/Cline |
| No event tracing | `control/event_log.py` append-only BrainEvent log | OpenHands EventLog |
| No decision rollback | `control/checkpoint.py` CheckpointManager with apply_patch | Cline checkpoint/rollback |
| L0/L1 plain dict memory | `memory/blocks.py` Block-based with version history | Letta Block memory |
| PG-only memory persistence | `memory/dual_write.py` PG + Redis/Milvus dual-write | Letta dual-write |
| No context compaction | `memory/compaction.py` sliding_window→summary→self_compact | Letta compaction |
| No tool execution policy | `governance/tool_policy.py` ToolPolicy with per-tool rules | Cline ToolPolicy |
| No agent archival control | `control/tools/archive_memory.py` agent-controlled promotion | Letta archival |
| No gateway event tracking | `gateway/events.py` ActionEvent↔ObservationEvent pairs | OpenHands events |

---

## 3. Dependency Injection

### Problem: Global LLM Singleton

Current code uses `get_llm()`/`init_llm()` global singleton, accessed by 4+ modules via deferred imports. This is a service locator anti-pattern.

### Solution: Per-Plane Dependency Groups + Constructor Injection

Instead of one 30-field dataclass (untestable composition root), dependencies are grouped by Plane. Each Plane receives only its own deps. See Section 0.5 for the full type definitions.

```python
# Full type definitions in Section 0.5
# CapabilityDeps, ControlDeps, KnowledgeDeps, MemoryDeps, GatewayDeps, GovernanceDeps
# → composed into CognitiveDependencies(capability, control, knowledge, memory, gateway, governance)
```

### Before vs After

```python
# BEFORE: dict[str, Any] + global singleton
class BrainOrchestrator:
    async def execute_workflow_design(self, ..., ports: dict[str, Any]):
        if self._has_port(ports, "RAGQueryPort"):
            rag_port: RAGQueryPort = ports["RAGQueryPort"]

# AFTER: typed constructor injection — Orchestrator receives only what it needs
class BrainOrchestrator:
    def __init__(
        self,
        knowledge: KnowledgeDeps,    # All knowledge ports
        memory: MemoryDeps,           # All memory ports
        governance: GovernanceDeps,   # Validation + policy
        gateway: GatewayDeps,         # Write port
        decision_repo: BrainDecisionRepository,
        supervisor: SupervisorCenter,
    ):
        self._knowledge = knowledge
        self._memory = memory
        self._governance = governance
        self._gateway = gateway
        # No more _has_port checks
```

### Composition Root

```python
def create_app() -> FastAPI:
    deps = _build_dependencies()
    app = FastAPI(title="WeldEvent Cognitive Plane", version="0.2.0")

    from cognitiveplane.interaction.api.chat import create_chat_router
    app.include_router(create_chat_router(deps))

    return app

def _build_dependencies() -> CognitiveDependencies:
    # 1. Adapters to L5/L6 (shared infrastructure)
    db_engine = create_db_engine(DatabaseConfig.from_env())
    redis = RedisCache(RedisConfig.from_env())
    minio = MinioStorage(MinioConfig.from_env())
    weldmap = WeldMapHTTPClient(WeldMapConfig.from_env())
    setup_tracing(TelemetryConfig.from_env())

    # 2. Per-Plane assembly
    capability = _build_capability()
    gateway = _build_gateway(weldmap)
    knowledge = _build_knowledge(db_engine)
    memory = _build_memory(redis, db_engine)
    governance = _build_governance(db_engine)
    control = _build_control(capability, knowledge, memory, governance, gateway)

    return CognitiveDependencies(
        capability=capability,
        control=control,
        knowledge=knowledge,
        memory=memory,
        gateway=gateway,
        governance=governance,
    )

def _build_capability() -> CapabilityDeps:
    return CapabilityDeps(llm_provider=_bootstrap_llm())

def _build_gateway(weldmap) -> GatewayDeps:
    return GatewayDeps(
        read=WeldMapReadGateway(weldmap),
        write=WeldMapWriteGateway(weldmap),
    )

def _build_knowledge(db) -> KnowledgeDeps:
    return KnowledgeDeps(
        rag_query=PostgreSQLRAGAdapter(db),
        standards_query=PostgreSQLStandardsAdapter(db),
        case_library=PostgreSQLCaseLibraryAdapter(db),
        process_knowledge=PostgreSQLProcessAdapter(db),
    )

def _build_memory(redis, db) -> MemoryDeps:
    memory_repo = HybridMemoryRepository(redis, db)
    return MemoryDeps(
        search=memory_repo,
        read=memory_repo,
        write=memory_repo,           # Fix P0-3: wire Memory write
        block_manager=BlockManager(),
        compactor=ContextCompactor(),
    )

def _build_governance(db) -> GovernanceDeps:
    tool_policy = ToolPolicy()
    return GovernanceDeps(
        validation=ValidationPipeline(...),
        review_repo=PostgreSQLReviewRepository(db),
        tool_policy=tool_policy,
    )

def _build_control(cap, know, mem, gov, gw) -> ControlDeps:
    tool_policy = gov.tool_policy
    hooks = [SafetyHook(), PolicyHook(tool_policy)]
    orchestrator = BrainOrchestrator(
        knowledge=know, memory=mem, governance=gov, gateway=gw,
        decision_repo=PostgreSQLDecisionRepository(db_engine),
        supervisor=SupervisorCenter(),
    )
    return ControlDeps(
        orchestrator=orchestrator,
        supervisor=SupervisorCenter(),
        tool_policy=tool_policy,
        hooks=hooks,
    )
```

---

## 4. Port System Redesign

### Rules

1. Port ABCs defined in their owning module's `ports.py`
2. Ports accept/return DTOs directly, no Input/Output wrappers
3. Each port gets its own adapter class (except HybridMemoryRepository for L0-L5 cross-store)
4. `shared/` contains only cross-module shared DTOs and types
5. **All cross-layer I/O goes through CognitiveGateway → WeldMap** (architecture principle)

### Port Ownership Map

| Module | Port | Old Location | New Location |
|--------|------|-------------|-------------|
| Interaction | InstructionPublishPort | shared/ports/event_bus.py | interaction/ports.py |
| Governance | ValidationPipelinePort | shared/ports/validation.py | governance/ports.py |
| Governance | HumanReviewRepository | shared/ports/collaboration.py | governance/ports.py |
| Governance | ApprovalServicePort | (new) | governance/ports.py |
| Control | BrainDecisionRepository | brain/ports.py | control/ports.py |
| Control | ReasoningPort | shared/ports/deepagents.py | control/ports.py |
| Control | PlanningPort | shared/ports/deepagents.py | control/ports.py |
| Control | ReflectionPort | shared/ports/deepagents.py | control/ports.py |
| Control | SubAgentDelegationPort | (new) | control/ports.py |
| Gateway | CognitiveGatewayWritePort | shared/ports/gateway.py | gateway/ports.py |
| Gateway | CognitiveGatewayReadPort | shared/ports/gateway.py | gateway/ports.py |
| Knowledge | RAGQueryPort + 5 others | shared/ports/knowledge.py | knowledge/ports.py |
| Memory | MemorySearchPort + 4 others | shared/ports/memory.py | memory/ports.py |
| Capability | LLMProvider | interaction/llm/provider.py | capability/ports.py |

### Deleted Code

| File/Class | Reason |
|-----------|--------|
| All `*Input`/`*Output` wrapper classes in `shared/ports/` | No value over direct DTOs |
| `shared/commands.py` | Never used (P3-15) |
| `shared/queries.py` | Never used (P3-15) |
| `shared/dto_deepagents.py` | deepagents eliminated |
| `shared/dto_persona.py` | Moved to control/ |
| `shared/dto_reasoning_mode.py` | Moved to control/ |
| `shared/dto_learning.py` | Moved to memory/ |
| `shared/dto_collaboration.py` | Moved to governance/ |
| `interaction/llm/tracking.py` | LLMCallTracker never wired |
| `interaction/llm/__init__.py` | Global singleton removed |
| `deepagents/` entire module | Replaced by control/ BrainCore |
| `interaction/modes/` all 5 Mode classes | Replaced by ReAct Tool system |
| `interaction/classifier.py` | IntentClassifier replaced by ReAct engine |
| `interaction/router.py` | SessionRouter replaced by ReAct engine |
| `interaction/registry.py` | ModeRegistry replaced by ToolRegistry |
| `interaction/session_data.py` | Session data integrated into interaction/session.py |
| `brain/` module | Renamed to control/ |
| `gateway/` old module | Replaced by new gateway/ (CognitiveGateway) |
| `validation/` module | Moved to governance/ |
| `human_collaboration/` module | Merged into governance/ |
| `app.py` ChatUI class | Replaced by FastAPI |
| Duplicate WeldMapSnapshot in dto_gateway.py | Unified in shared/dto/context.py (P1-6) |

---

## 5. Control Plane — BrainCore

### Conversation-as-Runtime (from OpenHands)

OpenHands核心设计：Agent是冻结的Pydantic model（无mutable state），所有可变状态由Conversation持有。LocalConversation与RemoteConversation共享同一接口，实现本地/远程透明执行。

**应用到WeldEvent：** BrainOrchestrator是无状态的——每次请求创建新的DecisionContext，所有中间状态存入EventLog。Session持有per-operator的可变状态。

```python
# Orchestrator is stateless — no mutable fields
class BrainOrchestrator:
    """Stateless decision executor. All mutable state in EventLog/Session."""

    def __init__(self, ...):  # typed ports only, no mutable state
        self._rag_query = rag_query
        # ...

    async def execute(self, objective, requirements, context) -> OrchestrationResult:
        # Each execution creates fresh event log — no shared mutable state
        event_log = EventLog(case_id=context.case_id)
        # ... pipeline drives state through event_log.append()
```

### Append-Only EventLog (from OpenHands)

OpenHands使用ActionEvent↔ObservationEvent配对，增量View投影。每个Action产生对应Observation，EventLog是append-only的。

**应用到WeldEvent：** Brain每次状态转换、Tool调用、Validation结果都追加为事件。支持完整审计追踪和时间旅行调试。

```python
# control/event_log.py
@dataclass
class BrainEvent:
    timestamp: datetime
    event_type: str          # "state_transition" | "tool_call" | "tool_result" | "validation" | "decision"
    source: str              # module that emitted
    data: dict               # event payload
    correlation_id: str      # links Action↔Observation pairs

class EventLog:
    """Append-only event log for a single decision pipeline run."""

    def __init__(self, case_id: CaseId):
        self.case_id = case_id
        self._events: list[BrainEvent] = []

    def append(self, event_type: str, source: str, data: dict) -> BrainEvent:
        event = BrainEvent(
            timestamp=datetime.now(timezone.utc),
            event_type=event_type,
            source=source,
            data=data,
            correlation_id=str(uuid4()),
        )
        self._events.append(event)
        return event

    def query(self, event_type: str | None = None, source: str | None = None) -> list[BrainEvent]:
        return [e for e in self._events
                if (event_type is None or e.event_type == event_type)
                and (source is None or e.source == source)]

    def project_view(self) -> DecisionPipelineView:
        """Incremental projection — current state from event stream."""
        view = DecisionPipelineView()
        for event in self._events:
            view.apply(event)
        return view
```

### beforeTool Hooks (from OpenHands/Cline)

OpenHands: SecurityAnalyzer评估风险等级，beforeTool hooks可skip/stop/modify tool输入。Cline: beforeTool hooks在Tool执行前拦截，返回HookResult(approved/skip/stop/modify)。

**应用到WeldEvent：** ValidationPipeline和ToolPolicy在Tool执行前拦截。高风险Tool（adjust_parameter, escalate）需要额外审批；修改类Tool可被hook修改参数。

```python
# control/hooks.py
from enum import Enum
from dataclasses import dataclass

class HookAction(Enum):
    CONTINUE = "continue"    # Proceed with original input
    SKIP = "skip"            # Skip this tool call, return empty result
    STOP = "stop"            # Stop entire ReAct loop
    MODIFY = "modify"        # Modify tool arguments before execution

@dataclass
class HookResult:
    action: HookAction
    modified_args: dict | None = None  # Only when action=MODIFY
    reason: str | None = None

class BeforeToolHook(ABC):
    @abstractmethod
    async def before_execute(self, tool_name: str, arguments: dict, context: ContextSnapshot) -> HookResult: ...

class SafetyHook(BeforeToolHook):
    """Block dangerous operations based on context."""
    async def before_execute(self, tool_name, arguments, context):
        if tool_name == "adjust_parameter" and context.safety_status == SafetyStatus.BLOCK:
            return HookResult(action=HookAction.STOP, reason="Safety BLOCK — parameter changes forbidden")
        return HookResult(action=HookAction.CONTINUE)

class PolicyHook(BeforeToolHook):
    """Enforce ToolPolicy from governance/tool_policy.py."""
    def __init__(self, policy: "ToolPolicy"):
        self._policy = policy

    async def before_execute(self, tool_name, arguments, context):
        rule = self._policy.get_rule(tool_name)
        if not rule.enabled:
            return HookResult(action=HookAction.SKIP, reason=f"Tool {tool_name} disabled by policy")
        if rule.auto_approve:
            return HookResult(action=HookAction.CONTINUE)
        # Needs approval — route to ApprovalService
        approval = await self._policy.request_approval(tool_name, arguments, context)
        if not approval.approved:
            return HookResult(action=HookAction.STOP, reason=approval.reason or "Approval denied")
        return HookResult(action=HookAction.CONTINUE)
```

### Checkpoint/Rollback (from Cline)

Cline的createCheckpoint配置允许在关键操作前保存状态快照，出错时回滚。WeldEvent的Decision pipeline需要在Validation失败或人工拒绝时恢复到安全状态。

```python
# control/checkpoint.py
@dataclass
class DecisionCheckpoint:
    checkpoint_id: str
    case_id: CaseId
    state: BrainStateType
    decision: BrainDecision | None
    event_log_snapshot: list[BrainEvent]
    created_at: datetime

class CheckpointManager:
    """Save/restore decision pipeline state for rollback."""

    def __init__(self, memory_write: MemoryWritePort):
        self._memory = memory_write
        self._checkpoints: dict[str, DecisionCheckpoint] = {}

    async def save(self, case_id: CaseId, state: BrainStateType,
                   decision: BrainDecision | None, event_log: EventLog) -> str:
        cp_id = str(uuid4())
        checkpoint = DecisionCheckpoint(
            checkpoint_id=cp_id,
            case_id=case_id,
            state=state,
            decision=decision,
            event_log_snapshot=list(event_log._events),
            created_at=datetime.now(timezone.utc),
        )
        self._checkpoints[cp_id] = checkpoint
        # Persist to L5 Audit memory
        await self._memory.store(MemoryRecord(
            memory_type=MemoryType.CHECKPOINT,
            content=checkpoint,
            case_id=case_id,
            promotion_status=PromotionStatus.RAW,
        ))
        return cp_id

    async def restore(self, checkpoint_id: str) -> DecisionCheckpoint:
        cp = self._checkpoints.get(checkpoint_id)
        if not cp:
            raise CheckpointNotFoundError(checkpoint_id)
        return cp

    async def apply_patch(self, checkpoint_id: str, patch: dict) -> BrainDecision:
        """Apply structured diff to a checkpointed decision (from Cline apply_patch).
        Enables fine-grained parameter adjustments without full re-decision."""
        cp = await self.restore(checkpoint_id)
        if not cp.decision:
            raise ValueError("No decision in checkpoint")
        # Diff-based modification — only changed fields updated
        patched = self._apply_diff(cp.decision, patch)
        return patched

    @staticmethod
    def _apply_diff(decision: BrainDecision, patch: dict) -> BrainDecision:
        """Structured diff with fuzz matching (from Cline apply_patch grammar).
        Only modifies fields present in patch, preserves everything else."""
        # Implementation: iterate patch keys, apply to decision.outputs
        # Supports: parameter add/remove/modify, confidence adjustment
        ...
```

### Orchestrator Rewrite (with EventLog + Hooks)

```python
class BrainOrchestrator:
    def __init__(
        self,
        rag_query: RAGQueryPort,
        memory_search: MemorySearchPort,
        memory_write: MemoryWritePort,      # P0-3: wire memory write chain
        reasoning: ReasoningPort,
        planning: PlanningPort,
        reflection: ReflectionPort,
        validation: ValidationPipelinePort,
        gateway_write: CognitiveGatewayWritePort,  # Write to WeldMap
        decision_repo: BrainDecisionRepository,
        supervisor: SupervisorCenter,
        hooks: list[BeforeToolHook],        # 🆕 from OpenHands/Cline
        checkpoint_mgr: CheckpointManager,  # 🆕 from Cline
    ): ...

    async def execute(self, objective, requirements, context) -> OrchestrationResult:
        event_log = EventLog(case_id=context.case_id)  # 🆕 per-run event log

        # 1. Persona + ReasoningMode selection
        persona = PersonaSelector.select(context)
        mode = ReasoningModeSelector.select(context)
        event_log.append("persona_selected", "orchestrator", {"persona": persona.value, "mode": mode.value})

        # 2. State machine driven pipeline
        state = self._transition(IDLE, EVENT_DEQUEUED, event_log)
        state = self._transition(state, mode.context_loaded_trigger, event_log)

        # 3. Knowledge retrieval (ADAPTIVE/EXPLORATORY only)
        knowledge = []
        if mode != ReasoningMode.ROUTINE:
            state = self._transition(state, CONTEXT_UNDERSTOOD, event_log)
            knowledge = await self._rag_query.query(RAGQuery(...))
            event_log.append("knowledge_retrieved", "knowledge", {"count": len(knowledge)})
            state = self._transition(state, KNOWLEDGE_RECEIVED, event_log)

        # 4. Memory search (all modes)
        memory = await self._memory_search.search(MemorySearchQuery(...))
        event_log.append("memory_searched", "memory", {"count": len(memory)})
        state = self._transition(state, mode.memory_received_trigger, event_log)

        # 5. Checkpoint before reasoning (from Cline)
        cp_id = await self._checkpoint_mgr.save(context.case_id, state, None, event_log)

        # 6. Reasoning / Planning / Reflection
        result = None
        if mode == ReasoningMode.ROUTINE:
            state = self._transition(state, MATCH_PRODUCED, event_log)
        else:
            if persona == PersonaType.PLANNER:
                plan = await self._planning.plan(objective, context, knowledge, memory)
            result = await self._reasoning.reason(objective, context, knowledge, memory, plan)
            reflected = await self._reflection.reflect(result, context)
            event_log.append("reasoning_completed", "reasoning", {"confidence": getattr(result, "confidence", None)})
            state = self._transition(state, REASONING_COMPLETED, event_log)
            state = self._transition(state, DECISION_GENERATED, event_log)

        # 7. Decision generation (multi-output-type by Persona)
        decision = DecisionFactory.create(persona, mode, result, memory, context)

        # 8. Validation (pure computation, no cross-layer I/O — fix P2-12)
        validation = await self._validation.validate(decision, context)
        event_log.append("validation_completed", "governance", {"result": validation.aggregated_result.value})

        # 9. Supervisor check (loop/timeout)
        self._supervisor.check(context.case_id, state, validation)

        # 10. Publish via CognitiveGateway → WeldMap (not direct WeldMap write)
        published = await self._publish_by_validation(decision, validation, state, event_log)

        # 11. Persist decision
        await self._decision_repo.save(decision)

        # 12. Write to Memory (fix P0-3: complete memory write chain)
        if published:
            await self._memory_write.store(MemoryRecord(
                memory_type=MemoryType.APPROVED_DECISION,
                content=decision,
                case_id=context.case_id,
                promotion_status=PromotionStatus.VALIDATED,  # Auto-promote to searchable
            ))

        # 13. Write Learning event (fix P3-16)
        if published:
            await self._write_learning_event(decision, validation)

        # 14. Persist event log to L5 Audit memory
        await self._persist_event_log(event_log)

        return OrchestrationResult(event_log=event_log, ...)
```

### BrainStateMachine (7 States, preserved)

```
IDLE → OBSERVING → UNDERSTANDING → KNOWLEDGE_RETRIEVAL → MEMORY_RETRIEVAL
  → REASONING → VALIDATION → PUBLISHED / WAITING_FEEDBACK / ABORT

Key fixes:
- P2-10: ABORT is true terminal, remove UNRECOVERABLE_ERROR recovery from ABORT
- P2-11: WAITING_FEEDBACK waits for real Temporal Signal, not self-timeout
```

### Self-Built Planner/Reflector/Sub-Agent/Supervisor

(Same as v1 design — see Section 5 of v1 spec. Preserved here for completeness.)

- **Planner** — Task Decomposition (borrow DeepAgents)
- **Reflector** — Reflection on reasoning results
- **SubAgentDelegator** — Delegation to specialized sub-agents
- **SupervisorCenter** — Health Check, Loop Detection, Timeout Detection, Fallback

### DecisionFactory (Multi-Output-Type)

```python
class DecisionFactory:
    @staticmethod
    def create(persona, mode, result, memory, context) -> BrainDecision:
        if persona == PersonaType.PLANNER:
            content = WorkflowRecommendation(...)
        elif persona == PersonaType.CAA:
            content = InvestigationDirective(...)
        else:  # COPILOT
            content = ParameterRecommendation(...)
```

---

## 6. Gateway — CognitiveGateway (L1→WeldMap)

### Action/Observation Event Pairs (from OpenHands)

OpenHands核心设计：ActionEvent↔ObservationEvent配对。每次Action（如tool call）产生对应Observation（tool result）。EventLog是append-only的，View从事件流增量投影。

**应用到WeldEvent：** CognitiveGateway的每次write操作是一个ActionEvent，WeldMap返回结果是ObservationEvent。配对关系通过correlation_id链接，支持审计追踪。

```python
# gateway/events.py
@dataclass
class GatewayActionEvent:
    """Action: CognitiveGateway initiates a write to WeldMap."""
    action_id: str
    action_type: str           # "publish_decision" | "publish_escalation" | "notify_workflow"
    domain: str                # WeldMap domain: "decision" | "negotiation"
    payload: dict
    timestamp: datetime

@dataclass
class GatewayObservationEvent:
    """Observation: WeldMap responds to a write action."""
    observation_id: str
    action_id: str             # Links back to ActionEvent
    success: bool
    domain: str
    result: dict | None
    error: str | None
    timestamp: datetime
```

### Architecture Principle

Brain永远在执行循环之外。所有跨层状态通过WeldMap共享。CognitiveGateway是Brain写WeldMap的**唯一出口**。

```python
# gateway/ports.py

class CognitiveGatewayWritePort(ABC):
    """Brain's only write interface to WeldMap.
    
    Writes to WeldMap decision/negotiation domains.
    Must NOT write to execution result domains (image/mask/annotation/rendering).
    """
    
    @abstractmethod
    async def publish_decision(self, decision: BrainDecision) -> PublishResult: ...
    
    @abstractmethod
    async def publish_escalation(self, escalation: Escalation) -> PublishResult: ...
    
    @abstractmethod
    async def publish_instruction(self, instruction: LiveInstruction) -> PublishResult: ...
    
    @abstractmethod
    async def notify_workflow_trigger(self, case_id: CaseId, workflow_config: dict) -> None:
        """Notify L2 Control Plane to start a Temporal workflow.
        
        Fix P1-5: This is the missing link between Brain decision and Temporal execution.
        Implementation: writes to WeldMap workflow domain → Temporal listens for Signal.
        """
        ...


class CognitiveGatewayReadPort(ABC):
    """Brain's read interface to WeldMap.
    
    Read-only access to WeldMap domains:
    - workflow: pipeline status, step progress
    - image: image metadata (not pixel data)
    - annotation: measurement results
    - decision: previous decisions for this case
    - negotiation: escalation history
    
    Brain CANNOT read execution-internal state (mask, rendering).
    """
    
    @abstractmethod
    async def read_weldmap_snapshot(self, case_id: CaseId) -> WeldMapSnapshot: ...
    
    @abstractmethod
    async def read_workflow_state(self, case_id: CaseId) -> WorkflowState | None: ...
    
    @abstractmethod
    async def read_case_data(self, case_id: CaseId) -> CaseData | None: ...
```

### WeldMap Client Adapter

```python
# adapters/weldmap/weldmap_http.py

class WeldMapHTTPClient:
    """HTTP client connecting to L5 Data Plane (WeldMap Store)."""
    
    def __init__(self, config: WeldMapConfig):
        self._base_url = config.url
        self._headers = {"Authorization": f"Bearer {config.api_key}"}
    
    async def write(self, domain: str, key: str, value: dict) -> dict: ...
    async def read(self, domain: str, key: str) -> dict | None: ...
    async def search(self, domain: str, query: dict) -> list[dict]: ...
```

### Data Flow: Decision → WeldMap → Temporal

```
BrainOrchestrator
  │ publish_decision()
  ▼
CognitiveGatewayWritePort
  │ write to WeldMap "decision" domain
  ▼
WeldMap Store (L5)
  │ Event Sourcing → event emitted
  ▼
Temporal Server (L2) ← listens for Signal
  │ HumanGateSignal or AutoTrigger
  ▼
CP0–CP6 DAG Execution
```

---

## 7. Interaction Plane — ReAct Unified Model

### Architecture: BrainCore as Agent, Tools as Capabilities

Previous design: 5 independent Mode classes + IntentClassifier + SessionRouter
New design: **BrainCore uses ReAct (Reasoning + Acting) loop** — LLM autonomously understands intent and selects tools.

```
User Input
  │
  ▼
[Interaction Layer] FastAPI — input parsing + multimodal handling
  │
  ▼
[BrainCore ReAct Engine]
  │
  │  3-Tier Fallback:
  │  ┌─────────────────────────────────────────────────────────┐
  │  │ Layer 1: ReAct + Function Calling (LLM available)       │
  │  │   LLM reasons about user intent, calls Tools directly   │
  │  │   Most powerful: understands nuance, combines tools      │
  │  ├─────────────────────────────────────────────────────────┤
  │  │ Layer 2: Structured Output + Rule Router (LLM limited)  │
  │  │   LLM returns structured intent, rules dispatch to path  │
  │  ├─────────────────────────────────────────────────────────┤
  │  │ Layer 3: Semantic Embedding + Rule Engine (no LLM)      │
  │  │   User input → embedding → cosine similarity → path     │
  │  │   Much stronger than keyword matching                    │
  │  └─────────────────────────────────────────────────────────┘
  │
  ▼
[Tool Registry] — Brain's available capabilities
  │
  ├─ search_standards(标准号, 关键词)         → Knowledge Plane
  ├─ search_cases(材料, 厚度, 缺陷类型)       → Knowledge Plane
  ├─ search_process(工艺类型)                  → Knowledge Plane
  ├─ read_weldmap(case_id, domain)             → Gateway Read → WeldMap
  ├─ design_workflow(目标, 约束, 标准)          → Control Plane (Planner)
  ├─ adjust_parameter(case_id, 参数, 新值)     → Gateway Write → WeldMap → Temporal
  ├─ request_confirmation(问题, 选项, 超时)     → Human notification channel
  ├─ escalate(原因, 紧急度)                    → Governance Plane → WeldMap
  ├─ explain_decision(decision_id)             → Copilot (explain)
  └─ search_memory(查询, 置信度阈值)            → Memory Plane
  │
  ▼
[BrainStateMachine] — State tracking for active decision pipeline
  │
  ▼
[Response] — Natural language + structured data back to user
```

### ReAct Engine Implementation (with Hooks + Compaction)

```python
class ReActEngine:
    """Core Reasoning + Acting loop for BrainCore.

    Borrowed from DeepAgents/Claude Code/OpenHands pattern:
    Thought → Action → Observation → Thought → ... → Final Answer

    Enhanced with:
    - beforeTool hooks (OpenHands/Cline) — skip/stop/modify tool calls
    - ToolPolicy enforcement (Cline) — auto_approve or require human approval
    - Context compaction (Letta) — fallback chain when context window fills
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        tool_registry: ToolRegistry,
        state_machine: BrainStateMachine,
        supervisor: SupervisorCenter,
        hooks: list[BeforeToolHook],
        compactor: ContextCompactor,
        max_iterations: int = 10,
    ):
        self._llm = llm_provider
        self._tools = tool_registry
        self._sm = state_machine
        self._supervisor = supervisor
        self._hooks = hooks
        self._compactor = compactor

    async def run(
        self,
        user_input: UserMessage,
        context: ContextSnapshot,
        session: SessionState,
    ) -> InteractionResponse:
        """Execute ReAct loop until final answer or max iterations."""

        # Select tier based on LLM availability
        tier = self._select_tier()

        if tier == InteractionTier.REACT_FUNCTION_CALLING:
            return await self._run_react(user_input, context, session)
        elif tier == InteractionTier.STRUCTURED_OUTPUT:
            return await self._run_structured(user_input, context, session)
        else:
            return await self._run_embedding_rules(user_input, context, session)

    async def _run_react(self, user_input, context, session) -> InteractionResponse:
        """Layer 1: Full ReAct with Function Calling."""
        messages = self._build_system_prompt(context, session)
        messages.append({"role": "user", "content": user_input.raw_text})

        for i in range(self._max_iterations):
            # Context compaction check (from Letta)
            messages = await self._compactor.compact(messages, session.event_log)

            # LLM reasons and decides action
            response = await self._llm.complete_with_tools(
                messages=messages,
                tools=self._tools.get_llm_tool_definitions(),
            )

            if not response.tool_calls:
                # No tool calls = final answer
                return InteractionResponse(
                    text_reply=response.content,
                    decisions=[],
                    tools_used=self._tools_used,
                )

            # Execute each tool call — with hook interception
            for tool_call in response.tool_calls:
                # beforeTool hooks (from OpenHands/Cline)
                hook_result = await self._run_hooks(tool_call.name, tool_call.arguments, context)
                if hook_result.action == HookAction.SKIP:
                    messages.append({"role": "tool", "tool_call_id": tool_call.id,
                                     "content": json.dumps({"skipped": True, "reason": hook_result.reason})})
                    continue
                if hook_result.action == HookAction.STOP:
                    return InteractionResponse(text_reply=f"操作已拦截: {hook_result.reason}")
                if hook_result.action == HookAction.MODIFY:
                    tool_call.arguments = hook_result.modified_args  # Apply modified args

                result = await self._tools.execute(tool_call.name, tool_call.arguments)
                messages.append(tool_call.to_message())
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result.to_json(),
                })

                # Supervisor checks for loops/timeouts
                self._supervisor.check_iteration(session.case_id, i, tool_call.name)

        # Max iterations reached — supervisor decides fallback
        return self._supervisor.handle_max_iterations(session)

    async def _run_hooks(self, tool_name: str, arguments: dict,
                         context: ContextSnapshot) -> HookResult:
        """Run all beforeTool hooks in order. First non-CONTINUE wins."""
        for hook in self._hooks:
            result = await hook.before_execute(tool_name, arguments, context)
            if result.action != HookAction.CONTINUE:
                return result
        return HookResult(action=HookAction.CONTINUE)

    async def _run_structured(self, user_input, context, session) -> InteractionResponse:
        """Layer 2: Structured output intent analysis + rule-based routing."""
        intent = await self._llm.complete_structured(
            prompt=user_input.raw_text,
            schema=IntentAnalysisSchema,  # path, entities, urgency, requires_human
        )
        return await self._dispatch_by_intent(intent, context, session)

    async def _run_embedding_rules(self, user_input, context, session) -> InteractionResponse:
        """Layer 3: Semantic embedding + rule engine (no LLM)."""
        embedding = await self._embedding_model.embed(user_input.raw_text)
        path = self._semantic_match(embedding, self._tool_descriptions)
        return await self._dispatch_by_path(path, user_input, context, session)
```

### Tool Registry

```python
class ToolRegistry:
    """Central registry of Brain's available tools."""

    def __init__(self, deps: CognitiveDependencies):
        self._tools: dict[str, BrainTool] = {}
        self._register_tools(deps)

    def _register_tools(self, deps):
        # Knowledge tools
        self.register(SearchStandardsTool(deps.standards_query))
        self.register(SearchCasesTool(deps.case_library))
        self.register(SearchProcessTool(deps.process_knowledge))

        # Gateway tools (WeldMap)
        self.register(ReadWeldMapTool(deps.gateway_read))
        self.register(DesignWorkflowTool(deps.orchestrator))  # Uses Planner
        self.register(AdjustParameterTool(deps.gateway_write))

        # Human interaction tools
        self.register(RequestConfirmationTool(deps.notification_channel))
        self.register(EscalateTool(deps.validation_pipeline, deps.gateway_write))

        # Copilot tools
        self.register(ExplainDecisionTool(deps.copilot))
        self.register(SearchMemoryTool(deps.memory_search))

        # Memory tools (Letta-inspired)
        self.register(ArchiveMemoryTool(deps.memory_write))  # 🆕 Agent-controlled archival

    def get_llm_tool_definitions(self) -> list[dict]:
        """Return tool definitions in LLM Function Calling format."""
        return [tool.to_function_definition() for tool in self._tools.values()]

    async def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        tool = self._tools.get(tool_name)
        if not tool:
            return ToolResult(error=f"Unknown tool: {tool_name}")
        return await tool.execute(**arguments)
```

### BrainTool ABC

```python
class BrainTool(ABC):
    """Base class for all Brain tools. Each tool wraps one or more ports."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters_schema(self) -> dict: ...

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult: ...

    def to_function_definition(self) -> dict:
        """Convert to LLM Function Calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            }
        }

    def to_embedding_description(self) -> str:
        """Text description for semantic matching (Layer 3 fallback)."""
        return f"{self.name}: {self.description}"
```

### Example Tool: RequestConfirmation (Agent→Human)

```python
class RequestConfirmationTool(BrainTool):
    """Agent requests human confirmation — key for human-in-the-loop."""

    name = "request_confirmation"
    description = (
        "当需要人工确认时使用。例如：检测结果异常需要人工判定、"
        "方案变更需要工程师确认、标准解读存在歧义。"
        "系统会向操作员发送确认请求并等待回复。"
    )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "需要确认的问题"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选的确认选项",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["routine", "urgent", "critical"],
                    "description": "紧急程度",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "等待超时（秒），超时后按默认处理",
                },
            },
            "required": ["question"],
        }

    async def execute(self, question, options=None, urgency="routine", timeout_seconds=300):
        # Write confirmation request to WeldMap negotiation domain
        # → Notification channel pushes to operator
        # → Wait for response (or timeout)
        confirmation = await self._notification_channel.request_confirmation(
            question=question, options=options, urgency=urgency, timeout=timeout_seconds,
        )
        return ToolResult(data=confirmation.to_dict())
```

### System→Human Push Notifications

```python
# interaction/api/notifications.py

@router.get("/notifications")
async def get_notifications(operator_id: str, deps = Depends(get_deps)):
    """Poll for pending confirmation requests and system alerts."""

@router.websocket("/notifications/ws")
async def notifications_ws(websocket: WebSocket, operator_id: str):
    """WebSocket for real-time push notifications to operator."""
    # Agent confirmation requests arrive via WeldMap → pushed here
```

### API Endpoints

```
POST /api/v1/chat/              — Main interaction entry (ReAct loop)
WS   /api/v1/chat/ws            — WebSocket streaming responses
GET  /api/v1/notifications      — Poll pending confirmations/alerts
WS   /api/v1/notifications/ws   — Real-time push notifications
GET  /api/v1/health             — System health (SupervisorCenter)
```

### What Changed from Previous Mode Architecture

| Before (5 Mode classes) | After (ReAct + Tools) |
|--------------------------|----------------------|
| IntentClassifier hard-routes to Mode | BrainCore LLM autonomously selects Tools |
| 5 independent Mode.handle() methods | ReAct loop with Tool calls |
| Keyword matching as fallback | Semantic embedding + rule engine as fallback |
| Mode switches require explicit user action | BrainCore can combine multiple tools in one turn |
| Agent→Human confirmation has no channel | `request_confirmation` Tool + notification API |
| Human interruption requires Mode switch | New user input = new ReAct iteration, naturally interrupts |
| Multimodal input not supported | `multimodal.py` parses images/annotations into Tool arguments |

### Preserved Designs

- BrainStateMachine (pure function, 7 states)
- Session management (per-operator, with cleanup)
- PersonaSelector / ReasoningModeSelector (used inside tools)
- ValidationPipeline (called by `design_workflow` tool)

---

## 8. Adapters — L5/L6 Connection

### PostgreSQL — Core Persistence (6 Tables)

```sql
brain_decisions     — Decision aggregate
memory_records      — Memory hierarchy (L0-L5, promotion_status)
knowledge_entries   — Knowledge base (+ pgvector for Phase2 Milvus migration)
workflow_states     — Workflow execution state (read from WeldMap, cached in PG)
human_reviews       — Review workflow
audit_entries       — L5 Audit memory (immutable)
```

ORM models use SQLAlchemy async with `postgresql+asyncpg://`.

### Redis — L0/L1 Cache + Session State

- **L0 Realtime Memory** — current decision context, TTL=5min
- **L1 Working Memory** — session working memory, TTL=1h
- **Session State** — interaction session state

### MinIO — Object Storage

- Images, videos, reports (via WeldMap → MinIO path resolution)

### WeldMap HTTP Client — L5 Data Plane

- CognitiveGateway delegates to WeldMap Store for all cross-layer I/O
- Writes to: decision, negotiation domains
- Reads from: workflow, image, annotation, decision, negotiation domains

### OpenTelemetry — L6 Observability

- Traces: per-request trace spanning L1 internal modules
- Metrics: decision latency, validation pass rate, LLM call count/cost
- Logging: structured JSON logs

---

## 9. Governance Plane

### ValidationPipeline Fix (P2-12)

```python
# BEFORE: Pipeline holds cross-layer ports and does its own I/O
class ValidationPipeline:
    def __init__(self, ..., memory_search: MemorySearchPort, gateway_read: GatewayReadPort):
        # Pipeline calls MemorySearchPort / GatewayReadPort internally
        # Callers don't know this is happening

# AFTER: Pipeline is pure computation, data pre-fetched by Orchestrator
class ValidationPipeline:
    def __init__(self, safety, rule, shadow, consistency, escalation):
        # No cross-layer I/O ports
    
    async def validate(self, decision, context, 
                       memory_context: list | None = None,    # Pre-fetched
                       gateway_context: dict | None = None) -> ValidationResult:
        # Consistency validator receives pre-fetched data, not ports
```

### EscalationTracker Fix (P1-7)

```python
# BEFORE: single _case_id, concurrent cases overwrite each other
class EscalationTracker:
    def __init__(self):
        self._case_id: str | None = None
        self._consecutive_critical: int = 0

# AFTER: per-case isolation
class EscalationTracker:
    def __init__(self):
        self._states: dict[CaseId, EscalationState] = {}
    
    def record(self, case_id: CaseId, result: ShadowStatus) -> EscalationAction:
        state = self._states.setdefault(case_id, EscalationState())
        if result == ShadowStatus.CRITICAL:
            state.consecutive_critical += 1
        else:
            state.consecutive_critical = 0
        
        if state.consecutive_critical >= 5:
            return EscalationAction.HUMAN_INTERVENTION
        if state.consecutive_critical >= 3:
            return EscalationAction.COGNITIVE_FALLBACK
        return EscalationAction.CONTINUE
```

### ToolPolicy (from Cline)

Cline核心设计：ToolPolicy {enabled, autoApprove}支持通配符 `*` + per-tool覆盖。beforeTool hooks在Tool执行前拦截。

**应用到WeldEvent：** 治理层定义哪些Tool需要人工审批，哪些可自动执行。高风险Tool（adjust_parameter, escalate）默认autoApprove=false，查询类Tool默认autoApprove=true。

```python
# governance/tool_policy.py
@dataclass
class ToolRule:
    """Per-tool execution policy (from Cline ToolPolicy)."""
    enabled: bool = True
    auto_approve: bool = False
    require_reason: bool = False
    max_calls_per_session: int | None = None

@dataclass
class ToolApprovalRequest:
    tool_name: str
    arguments: dict
    context: ContextSnapshot
    rule: ToolRule

@dataclass
class ToolApprovalResult:
    approved: bool
    reason: str | None = None

class ToolPolicy:
    """Centralized tool execution policy with wildcard + per-tool override."""

    DEFAULT_RULES: dict[str, ToolRule] = {
        "*": ToolRule(enabled=True, auto_approve=False),  # Default: enabled but needs approval
        "search_standards": ToolRule(enabled=True, auto_approve=True),
        "search_cases": ToolRule(enabled=True, auto_approve=True),
        "search_process": ToolRule(enabled=True, auto_approve=True),
        "read_weldmap": ToolRule(enabled=True, auto_approve=True),
        "search_memory": ToolRule(enabled=True, auto_approve=True),
        "explain_decision": ToolRule(enabled=True, auto_approve=True),
        "design_workflow": ToolRule(enabled=True, auto_approve=False, require_reason=True),
        "adjust_parameter": ToolRule(enabled=True, auto_approve=False, require_reason=True, max_calls_per_session=5),
        "request_confirmation": ToolRule(enabled=True, auto_approve=True),
        "escalate": ToolRule(enabled=True, auto_approve=False, require_reason=True),
        "archive_memory": ToolRule(enabled=True, auto_approve=True),
    }

    def __init__(self, overrides: dict[str, ToolRule] | None = None):
        self._rules = {**self.DEFAULT_RULES, **(overrides or {})}

    def get_rule(self, tool_name: str) -> ToolRule:
        """Get effective rule for a tool. Per-tool overrides wildcard."""
        return self._rules.get(tool_name, self._rules["*"])

    async def request_approval(self, tool_name: str, arguments: dict,
                                context: ContextSnapshot) -> ToolApprovalResult:
        """Request human approval for a tool call that requires it."""
        rule = self.get_rule(tool_name)
        if rule.auto_approve:
            return ToolApprovalResult(approved=True)
        # Route to ApprovalService for human review
        ...
```

### ApprovalService (Cline Plan/Approve/Execute)

```python
class ApprovalService:
    async def request_approval(self, decision, urgency) -> ApprovalResult:
        if urgency in (UrgencyLevel.CRITICAL, UrgencyLevel.URGENT):
            return await self._sync_approval(decision)
        else:
            return await self._async_approval(decision)
```

---

## 10. Memory Plane — Industrial Memory L0-L5

### Block-Based Working Memory (from Letta)

Letta核心设计：Working memory由多个Block组成，每个Block有label/value/limit/read_only/tags。Block编译为XML注入System Prompt。Block有version history，支持checkpoint/undo/redo和乐观锁。

**应用到WeldEvent：** L0/L1工作内存使用Block模型——每个决策上下文、案例状态、操作员偏好为独立Block。Block版本历史支持决策回滚。标签过滤支持精准检索。

```python
# memory/blocks.py
@dataclass
class MemoryBlock:
    """A versioned, tagged block of working memory (from Letta)."""
    label: str                     # e.g. "case_context", "operator_preference", "active_constraints"
    value: str                     # Block content (compiled into system prompt)
    limit: int                     # Max character length
    read_only: bool = False
    tags: list[str] = field(default_factory=list)  # e.g. ["safety", "parameter"]
    version: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

@dataclass
class BlockVersion:
    """Snapshot of a block at a point in time — enables undo/redo."""
    version: int
    value: str
    timestamp: datetime
    change_reason: str | None = None

class BlockManager:
    """Manages working memory blocks with version history."""

    def __init__(self):
        self._blocks: dict[str, MemoryBlock] = {}
        self._history: dict[str, list[BlockVersion]] = {}

    def compile_to_prompt(self) -> str:
        """Compile all blocks into XML system prompt (from Letta compilation pattern)."""
        parts = []
        for label, block in sorted(self._blocks.items()):
            escaped = block.value.replace("<", "&lt;").replace(">", "&gt;")
            parts.append(f"<{label}>\n{escaped}\n</{label}>")
        return "\n".join(parts)

    def update(self, label: str, new_value: str, reason: str | None = None) -> MemoryBlock:
        block = self._blocks.get(label)
        if not block:
            raise KeyError(f"No block: {label}")
        if block.read_only:
            raise PermissionError(f"Block {label} is read-only")
        if len(new_value) > block.limit:
            new_value = new_value[:block.limit]

        # Save version history
        self._history.setdefault(label, []).append(BlockVersion(
            version=block.version, value=block.value,
            timestamp=datetime.now(timezone.utc), change_reason=reason,
        ))
        block.version += 1
        block.value = new_value
        block.updated_at = datetime.now(timezone.utc)
        return block

    def undo(self, label: str) -> MemoryBlock:
        """Revert block to previous version."""
        history = self._history.get(label, [])
        if not history:
            raise ValueError(f"No history for block: {label}")
        last = history.pop()
        block = self._blocks[label]
        block.value = last.value
        block.version = last.version
        return block

    def query_by_tags(self, tags: list[str]) -> list[MemoryBlock]:
        return [b for b in self._blocks.values() if any(t in b.tags for t in tags)]
```

### Dual-Write Persistence (from Letta)

Letta：Archival memory使用dual-write——同时写入SQL和Turbopuffer向量DB。Shareable Archives通过junction table实现跨Agent共享。

**应用到WeldEvent：** L2-L5持久内存使用dual-write——同时写入PostgreSQL（结构化查询）和Redis/Milvus（向量检索）。写入链确保双存储一致性。

```python
# memory/dual_write.py
class DualWriteMemoryService:
    """Write to both PG (structured) and Redis/Milvus (vector) simultaneously."""

    def __init__(self, pg_repo: PostgreSQLMemoryRepository, vector_store: VectorStorePort):
        self._pg = pg_repo
        self._vector = vector_store

    async def store(self, record: MemoryRecord) -> None:
        """Dual-write with best-effort vector sync."""
        # 1. Write to PG first (source of truth)
        pg_id = await self._pg.save(record)

        # 2. Write to vector store (best-effort, non-blocking)
        try:
            if record.feature_vector:
                await self._vector.upsert(
                    id=pg_id,
                    vector=record.feature_vector,
                    metadata={"case_id": str(record.case_id), "memory_type": record.memory_type.value},
                )
        except VectorStoreUnavailableError:
            # Log but don't fail — PG is source of truth
            logger.warning("Vector store unavailable, PG write succeeded for %s", pg_id)

    async def search(self, query: MemorySearchQuery) -> list[MemoryRecord]:
        """Hybrid search: vector similarity + PG structured query, RRF fusion."""
        results = []

        # Vector search (if available and feature_vector provided)
        if query.feature_vector and self._vector.is_available():
            vector_hits = await self._vector.search(
                vector=query.feature_vector,
                limit=query.max_results,
                filter_tags=query.tags,
            )
            results.extend(await self._pg.find_by_ids(vector_hits))

        # PG structured search (always)
        pg_hits = await self._pg.search(query)
        results.extend(pg_hits)

        # RRF (Reciprocal Rank Fusion) — from Letta recall memory
        return self._rrf_fuse(results, vector_weight=0.6, pg_weight=0.4)

    @staticmethod
    def _rrf_fuse(results: list, vector_weight: float, pg_weight: float) -> list:
        """Reciprocal Rank Fusion for combining vector + structured results."""
        scores: dict[str, float] = {}
        for rank, r in enumerate(results):
            rid = str(r.record_id)
            scores[rid] = scores.get(rid, 0.0) + vector_weight / (rank + 1)
        # Deduplicate and sort by fused score
        seen: dict[str, MemoryRecord] = {}
        for r in results:
            rid = str(r.record_id)
            if rid not in seen:
                seen[rid] = r
        return sorted(seen.values(), key=lambda r: scores.get(str(r.record_id), 0.0), reverse=True)
```

### Context Compaction (from Letta)

Letta：Compaction fallback chain: sliding_window → all → self_compact。当context window接近上限时，按链式降级执行压缩。

**应用到WeldEvent：** ReAct Engine的迭代上下文需要压缩策略——长对话轮次时，老消息被压缩为摘要，保留关键推理链和决策点。

```python
# memory/compaction.py
class CompactionStrategy(Enum):
    SLIDING_WINDOW = "sliding_window"  # Keep last N messages + summary
    FULL_SUMMARY = "full_summary"      # Summarize all history
    SELF_COMPACT = "self_compact"      # LLM self-summarizes its context

class ContextCompactor:
    """Compacts ReAct conversation context when approaching LLM limits.
    Fallback chain: sliding_window → full_summary → self_compact (from Letta)."""

    def __init__(self, llm_provider: LLMProvider, max_tokens: int = 8000):
        self._llm = llm_provider
        self._max_tokens = max_tokens

    async def compact(self, messages: list[dict], event_log: EventLog) -> list[dict]:
        """Apply compaction fallback chain."""
        estimated = self._estimate_tokens(messages)
        if estimated <= self._max_tokens:
            return messages

        # Try sliding window first (cheapest)
        result = self._sliding_window(messages)
        if self._estimate_tokens(result) <= self._max_tokens:
            return result

        # Full summary (moderate cost)
        result = await self._full_summary(messages, event_log)
        if self._estimate_tokens(result) <= self._max_tokens:
            return result

        # Self-compact (most expensive — LLM compresses its own context)
        return await self._self_compact(messages, event_log)

    def _sliding_window(self, messages: list[dict], keep_recent: int = 6) -> list[dict]:
        """Keep system prompt + last N messages."""
        system = [m for m in messages if m["role"] == "system"]
        recent = [m for m in messages if m["role"] != "system"][-keep_recent:]
        return system + recent

    async def _full_summary(self, messages: list[dict], event_log: EventLog) -> list[dict]:
        """LLM summarizes all non-recent messages into a single summary block."""
        older = [m for m in messages if m["role"] != "system"][:-6]
        recent = [m for m in messages if m["role"] != "system"][-6:]
        system = [m for m in messages if m["role"] == "system"]

        summary = await self._llm.complete(
            prompt=f"Summarize the following conversation history, preserving key decisions and reasoning:\n{json.dumps(older)}",
        )
        return system + [{"role": "assistant", "content": f"[Previous context summary]: {summary}"}] + recent

    async def _self_compact(self, messages: list[dict], event_log: EventLog) -> list[dict]:
        """LLM produces a compact version of its own context (most expensive)."""
        # Extract key events from EventLog for grounding
        key_events = event_log.query(event_type="decision") + event_log.query(event_type="tool_call")
        context_hint = "\n".join(f"- {e.data}" for e in key_events[:10])

        compacted = await self._llm.complete(
            prompt=f"Produce a compact context preserving all critical decisions and tool results. Key events:\n{context_hint}\n\nFull messages:\n{json.dumps(messages)}",
        )
        system = [m for m in messages if m["role"] == "system"]
        return system + [{"role": "assistant", "content": f"[Compacted context]: {compacted}"}]
```

### Agent-Controlled Archival (from Letta)

Letta：Agent主动调用archival_memory_insert()将重要信息从working memory移到长期存档。不是自动的——agent决定什么值得保存。

**应用到WeldEvent：** BrainCore通过Tool调用控制记忆归档。search_memory Tool在搜索时也触发潜在的记忆提升。agent决定何时将L1 Working Memory提升到L2 Case Memory。

```python
# 在 control/tools/ 中增加 archive_memory Tool
class ArchiveMemoryTool(BrainTool):
    """Agent-controlled memory archival (from Letta)."""
    name = "archive_memory"
    description = "将当前工作记忆中的重要信息归档到案例长期记忆。用于保存关键决策、推理链或经验教训。"

    async def execute(self, content: str, memory_type: str = "case_experience", tags: list[str] | None = None):
        record = MemoryRecord(
            memory_type=MemoryType[memory_type.upper()],
            content=content,
            case_id=self._current_case_id,
            promotion_status=PromotionStatus.RAW,
            tags=tags or [],
        )
        await self._memory_write.store(record)
        return ToolResult(data={"archived": True, "memory_type": memory_type})
```

### Hierarchy

| Level | Name | Storage | TTL | Content |
|-------|------|---------|-----|---------|
| L0 | Realtime | Redis | 5min | Current decision context |
| L1 | Working | Redis | 1h | Session working memory |
| L2 | Case | PostgreSQL | Permanent | Case-level memory |
| L3 | Experience | PostgreSQL | Permanent | Cross-case experience |
| L4 | Knowledge | PG + Milvus (Phase2) | Permanent | Structured knowledge |
| L5 | Audit | PostgreSQL | Permanent (immutable) | Audit trail |

### Promotion Rules

| Path | Condition | Review |
|------|-----------|--------|
| L0 → L1 | Decision completed | Automatic |
| L1 → L2 | Session closed | Automatic |
| L2 → L3 | Confidence > 0.8 + 3+ validations | Human review |
| L3 → L4 | Confidence > 0.95 + committee approval | Formal review |

### Memory Write Chain Fix (P0-3, P0-4)

```python
# P0-3: Wire MemoryWritePort in Orchestrator
await self._memory_write.store(MemoryRecord(
    memory_type=MemoryType.APPROVED_DECISION,
    content=decision,
    case_id=context.case_id,
    promotion_status=PromotionStatus.VALIDATED,  # P0-4: auto-promote to searchable
))

# P0-4: Default search filter includes VALIDATED
class MemorySearchService:
    DEFAULT_STATUSES = [PromotionStatus.VALIDATED, PromotionStatus.PROMOTED]
```

### Memory Confidence Fix (P2-14)

```python
# BEFORE: constant 0.5
class MemoryConfidenceService:
    def calculate(self, *args) -> float:
        return 0.5

# AFTER: real confidence based on match quality
class MemoryConfidenceService:
    def calculate(self, memory_record: MemoryRecord, query: MemorySearchQuery) -> float:
        # Based on: feature similarity, source reliability, recency, validation count
        similarity = self._cosine_similarity(query.feature_vector, memory_record.feature_vector)
        source_weight = self._source_weights.get(memory_record.memory_type, 0.5)
        recency = self._recency_factor(memory_record.created_at)
        validation_count = min(memory_record.validation_count / 5.0, 1.0)
        return similarity * source_weight * recency * validation_count
```

---

## 11. Copilot — Industrial Copilot

| Sub-module | Responsibility | Dependencies |
|-----------|----------------|--------------|
| CopilotQA | Standard/process/case Q&A | Knowledge ports + LLM |
| CopilotExplain | Decision explanation + reasoning trace | Decision repository + LLM |
| CopilotInvestigate | Anomaly root cause analysis | Knowledge + Memory + LLM |
| CopilotGovern | Approval/compliance assistance | Review repo + Decision repo |
| CopilotOperate | Production line operation guidance | Gateway read + Instruction publish |

---

## 12. Data Flow (L1 Internal — ReAct Model with Hooks + EventLog)

```
User Input (text / image / annotation)
  │
  ▼
[Interaction] FastAPI POST /api/v1/chat
  │ multimodal.py: parse image/annotation → structured description
  │ session.py: load per-operator context (BlockManager compiles blocks to system prompt)
  ▼
[BrainCore ReAct Engine]
  │
  │  ┌─── Iteration Loop ───────────────────────────────────────────────┐
  │  │                                                                   │
  │  │  LLM reasons about intent, decides which Tool(s) to call         │
  │  │                                                                   │
  │  │  ┌── beforeTool Hooks (from OpenHands/Cline) ──────────────┐     │
  │  │  │  SafetyHook: block if SafetyStatus.BLOCK                │     │
  │  │  │  PolicyHook: enforce ToolPolicy (auto_approve or deny)   │     │
  │  │  │  Result: CONTINUE / SKIP / STOP / MODIFY                 │     │
  │  │  └──────────────────────────────────────────────────────────┘     │
  │  │                                                                   │
  │  │  ┌── Tool Execution ──────────────────────────────────────┐      │
  │  │  │                                                          │      │
  │  │  │  search_standards  → [Knowledge] RAG                    │      │
  │  │  │  search_cases      → [Knowledge] Case lib               │      │
  │  │  │  read_weldmap      → [Gateway] WeldMap read             │      │
  │  │  │  design_workflow   → [Control] Planner                  │      │
  │  │  │    └─ PersonaSelector + ReasoningModeSelector            │     │
  │  │  │    └─ Knowledge RAG + Memory L0-L5 (Block-based L0/L1)  │     │
  │  │  │    └─ DecisionFactory → ValidationPipeline               │     │
  │  │  │    └─ CheckpointManager.save() before reasoning          │     │
  │  │  │    └─ CognitiveGateway → WeldMap write (ActionEvent)     │     │
  │  │  │  adjust_parameter → [Gateway] WeldMap write              │      │
  │  │  │    └─ Signal → Temporal (L2) → Agent (L3)               │     │
  │  │  │  request_confirmation → Notification channel              │     │
  │  │  │    └─ WebSocket push to operator                         │     │
  │  │  │  escalate         → [Governance] + [Gateway]             │      │
  │  │  │  explain_decision → [Copilot] Explain                    │      │
  │  │  │  search_memory    → [Memory] L0-L5 dual-write search     │      │
  │  │  │  archive_memory   → [Memory] Agent-controlled archival   │      │
  │  │  │                                                          │      │
  │  │  └──────────────────────────────────────────────────────────┘     │
  │  │                                                                   │
  │  │  Observation from Tool → next LLM reasoning step                 │
  │  │  EventLog.append(tool_call / tool_result / validation)           │
  │  │  ContextCompactor.compact() if approaching token limit           │
  │  │  Supervisor checks: loop? timeout? fallback?                     │
  │  │                                                                   │
  │  └───────────────────────────────────────────────────────────────────┘
  │
  ▼
[Response] — Natural language + structured data + follow-up suggestions
```

### Agent→Human Confirmation Flow

```
VDA Agent (L3) detects anomaly
  │
  ▼
Writes to WeldMap negotiation domain
  │
  ▼
CognitiveGateway (L1) reads WeldMap → detects pending confirmation
  │
  ▼
ReAct Engine calls request_confirmation Tool
  │
  ▼
Notification channel → WebSocket push to operator
  │
  ▼
Operator responds → ReAct Engine continues decision
```

### Human Interruption Flow

```
ReAct Engine is mid-execution (designing workflow)
  │
  ▼
User sends new message: "等一下，这个标准用NB/T47013不是47014"
  │
  ▼
New ReAct iteration:
  LLM understands: user wants to change standard reference
  Calls: search_standards("NB/T47013")
  Calls: design_workflow(... updated standard ...)
  Natural continuation — no mode switch needed
```

---

## 13. Error Handling

### By Plane

| Plane | Error | Handling |
|-------|-------|----------|
| Interaction | InteractionError | 400 + user-friendly message |
| Control | DecisionTimeoutError | ROUTINE: retry→fallback; ADAPTIVE: downgrade; EXPLORATORY: notify human |
| Control | ReasoningLoopError | COGNITIVE_FALLBACK (memory match replaces LLM) |
| Governance | ValidationBlockError | Force ESCALATED → human intervention |
| Governance | EscalationOverflowError | HUMAN_INTERVENTION |
| Gateway | WeldMapUnavailableError | Retry 3x → degrade to local cache → AuditEvent |
| Capability | LLMUnavailableError | Degrade to keyword mode + notify operator |

### SupervisorCenter Integration

Same as v1 design (Section 12).

---

## 14. P0-P3 Issue Resolution

| # | Priority | Issue | Resolution in New Architecture |
|---|----------|-------|-------------------------------|
| 1 | P0 | InterventionMode None crash | `deps.has()` check before port access |
| 2 | P0 | UrgencyLevel enum mismatch | Fix to ROUTINE/URGENT/CRITICAL |
| 3 | P0 | Memory write chain broken | Wire MemoryWritePort in Orchestrator.execute() |
| 4 | P0 | Memory search status_filter mismatch | Auto-promote to VALIDATED on write; default filter includes VALIDATED |
| 5 | P1 | L1→L2 trigger chain missing | CognitiveGateway.notify_workflow_trigger() → WeldMap Signal |
| 6 | P1 | Two conflicting WeldMapSnapshot DTOs | Unified WeldMapSnapshot in shared/dto/context.py |
| 7 | P1 | EscalationTracker concurrent overwrite | Per-case `dict[CaseId, EscalationState]` isolation |
| 8 | P1 | _sessions no operator isolation | `dict[operator_id, dict[session_id, session]]` |
| 9 | P2 | _sessions never cleaned | Pop on SESSION_CLOSED / PUBLISHED |
| 10 | P2 | ABORT recovery path semantic contradiction | ABORT is true terminal, remove UNRECOVERABLE_ERROR→IDLE from ABORT |
| 11 | P2 | WAITING_FEEDBACK self-heals | Wait for real Temporal HumanGateSignal |
| 12 | P2 | ValidationPipeline holds cross-layer ports | Pipeline is pure computation; Orchestrator pre-fetches data |
| 13 | P2 | ActiveContext bypasses Pydantic | Remove `object.__setattr__`, use normal assignment |
| 14 | P2 | MemoryConfidenceService constant 0.5 | Real confidence calculation based on similarity/recency/validation |
| 15 | P3 | commands.py/queries.py dead code | Delete (CQRS layer has no handlers) |
| 16 | P3 | Learning write chain broken | Write LearningEvent in Orchestrator after PUBLISHED |
| 17 | P3 | Error paths untested | Add pytest cases for all P0/P1 fixes |
| 18 | P3 | app.py monolithic assembly | FastAPI composition root with typed CognitiveDependencies |
| 19 | P3 | BrainOrchestrator rebuilt per request | Singleton orchestrator with typed constructor injection |

---

## 15. Advice.md Alignment Checklist

### A-Level (Must Adopt)

- [x] BrainCore (self-built) — control/ with Planner/Reflector/Sub-Agent/Supervisor
- [x] Temporal — L2 Control Plane, bridge via CognitiveGateway → WeldMap Signal
- [x] MCP — deferred
- [x] Pydantic — continued for DTO/Schema
- [x] FastAPI — interaction/api/ + app.py
- [x] PostgreSQL — adapters/database/
- [x] Redis — adapters/cache/
- [x] MinIO — adapters/storage/
- [x] OpenTelemetry — adapters/observability/

### B-Level (Phase 2)

- [x] Milvus — Phase2 in knowledge/ (pgvector placeholder)
- [x] Industrial Memory — memory/ with L0-L5 hierarchy
- [x] SupervisorCenter — control/supervisor.py
- [x] Industrial Copilot — copilot/ with 5 sub-modules

### C-Level (Source Borrowing) — With Concrete Implementation References

- [x] DeepAgents — Planner/Reflection/Task Decomposition/Sub-Agent → control/; ReAct loop → control/react.py
- [x] OpenHands — Conversation-as-Runtime → control/orchestrator.py (stateless); EventLog → control/event_log.py; Action/Observation events → gateway/events.py; beforeTool hooks → control/hooks.py
- [x] Claude Code — Plan Mode/Approval Flow/Context Engineering → governance/ + control/; Tool-based interaction → control/tools/
- [x] Cline — ToolPolicy → governance/tool_policy.py; beforeTool hooks → control/hooks.py; checkpoint/rollback → control/checkpoint.py; apply_patch diff → control/checkpoint.py
- [x] Letta — Block-based memory → memory/blocks.py; dual-write → memory/dual_write.py; compaction fallback chain → memory/compaction.py; agent-controlled archival → control/tools/archive_memory.py; RRF hybrid search → memory/search.py

### Explicitly Not Recommended

- [x] No AutoGen / CrewAI / MetaGPT / LangGraph / Letta Runtime / PydanticAI

---

## 16. Borrowed Patterns — Source Code Research → WeldEvent Implementation

This section maps specific techniques discovered through source code research of 3 frameworks to their concrete implementation in the CognitivePlane redesign.

### OpenHands (Source: github.com/All-Hands-AI/OpenHands)

| Source Pattern | How OpenHands Does It | WeldEvent Adaptation | File |
|---------------|----------------------|---------------------|------|
| Conversation-as-Runtime | Agent is frozen Pydantic model; Conversation holds all mutable state. LocalConversation/RemoteConversation same interface | BrainOrchestrator is stateless; EventLog holds per-run state; Session holds per-operator state | control/orchestrator.py, interaction/session.py |
| Event Sourcing | Append-only EventLog; ActionEvent↔ObservationEvent matching; incremental View projection | BrainEvent append-only log per decision pipeline run; GatewayActionEvent↔GatewayObservationEvent pairs; DecisionPipelineView projection | control/event_log.py, gateway/events.py |
| beforeTool Hooks | SecurityAnalyzer evaluates risk; hooks return skip/stop/modify; ParallelToolExecutor with ResourceLockManager | BeforeToolHook ABC; SafetyHook blocks on SafetyStatus.BLOCK; PolicyHook enforces ToolPolicy; hooks chain with first-non-CONTINUE wins | control/hooks.py |
| Workspace Isolation | BaseWorkspace → Local/Remote/Docker; dedicated non-root user, port isolation, ulimit | Not directly applicable (WeldEvent agents run in L3 Agent Pool, not L1). Borrow concept for Tool isolation — each Tool execution is sandboxed in its own async context | control/tools/ |

### Letta (Source: github.com/letta-ai/letta)

| Source Pattern | How Letta Does It | WeldEvent Adaptation | File |
|---------------|-------------------|---------------------|------|
| Block-based Working Memory | Each Block: label, value, limit, read_only, tags; compiled into XML system prompt; version history with checkpoint/undo/redo | MemoryBlock with label/value/limit/read_only/tags; compile_to_prompt() for XML system prompt injection; BlockManager with version history for undo; tag-based filtering for precision retrieval | memory/blocks.py |
| Dual-Write Persistence | Archival memory: SQL + Turbopuffer vector DB; best-effort vector sync; shareable Archives via junction table | DualWriteMemoryService: PG (source of truth) + Redis/Milvus (vector); PG write succeeds even if vector fails; vector availability checked before search | memory/dual_write.py |
| Hybrid Search + RRF | Recall memory: vector + FTS + Reciprocal Rank Fusion; sequence_id ordering | Vector similarity + PG structured query → RRF fusion (vector_weight=0.6, pg_weight=0.4); deduplication by record_id | memory/search.py |
| Compaction Fallback Chain | sliding_window → all → self_compact; agent-controlled archival via archival_memory_insert() | ContextCompactor: sliding_window (keep 6 recent) → full_summary (LLM summarize) → self_compact (LLM compress); ArchiveMemoryTool for agent-controlled L1→L2 promotion | memory/compaction.py, control/tools/archive_memory.py |
| Agent-Controlled Archival | Agent explicitly calls archival_memory_insert(); not automatic | ArchiveMemoryTool: Brain decides when working memory content deserves promotion to case memory; tag-based classification for retrieval | control/tools/archive_memory.py |

### Cline (Source: github.com/cline/cline)

| Source Pattern | How Cline Does It | WeldEvent Adaptation | File |
|---------------|-------------------|---------------------|------|
| ToolPolicy {enabled, autoApprove} | Wildcard `*` + per-tool override; binary approval with reason | ToolPolicy with DEFAULT_RULES dict; per-tool ToolRule (enabled, auto_approve, require_reason, max_calls_per_session); wildcard fallback | governance/tool_policy.py |
| beforeTool Hooks | Can skip, stop, or modify input; binary approval: ToolApprovalResult {approved, reason?} | BeforeToolHook ABC with HookAction enum (CONTINUE/SKIP/STOP/MODIFY); PolicyHook enforces ToolPolicy; SafetyHook blocks dangerous operations | control/hooks.py |
| Checkpoint/Rollback | createCheckpoint config; state snapshots for recovery | CheckpointManager: saves BrainState + Decision + EventLog snapshot; restore() for rollback; persisted to L5 Audit memory | control/checkpoint.py |
| apply_patch Diff | Structured diff grammar with fuzz matching; fine-grained changes without full replacement | CheckpointManager.apply_patch(): structured diff on decision parameters; only modified fields updated; supports parameter add/remove/modify + confidence adjustment | control/checkpoint.py |
| Mode Switching | mode-switching system (Plan/Code); restricts available tools per mode | Not adopted directly. WeldEvent uses ReAct unified model instead of mode switching. ToolPolicy achieves similar effect — different policy profiles can restrict available tools per persona | governance/tool_policy.py |

### Borrowing Decisions — What We Did NOT Borrow

| Framework | Pattern | Why Not Borrowed |
|-----------|---------|-----------------|
| OpenHands | DockerWorkspace isolation | L1 doesn't run user code; agents run in L3 Agent Pool |
| OpenHands | RemoteConversation | WeldEvent doesn't need remote agent execution within L1 |
| OpenHands | ParallelToolExecutor + ResourceLockManager | WeldEvent ReAct is sequential tool calls; parallelism is Phase 2 |
| Letta | Letta Server/REST API | WeldEvent has its own FastAPI app; Letta's runtime is not needed |
| Letta | Shareable Archives + junction tables | WeldEvent memory is per-case, not per-agent sharing |
| Letta | Optimistic locking on blocks | Not needed at current scale; can add in Phase 2 |
| Cline | Mode switching (Plan/Code) | ReAct unified model replaces mode switching |
| Cline | No structured plan object | WeldEvent's DecisionFactory produces structured BrainDecision |
| Cline | Browser/terminal tools | Industrial domain has different tools (search_standards, read_weldmap, etc.) |

### Implementation Priority

| Priority | Pattern | Impact | Effort |
|----------|---------|--------|--------|
| P0 | beforeTool hooks | Critical for safety + governance enforcement | Low |
| P0 | ToolPolicy | Required for tool execution control | Low |
| P1 | EventLog (append-only) | Audit trail, time-travel debugging, Action↔Observation pairing | Medium |
| P1 | Block-based working memory | L0/L1 memory with version history and prompt compilation | Medium |
| P1 | Context compaction | Prevents ReAct loop from exceeding context window | Medium |
| P2 | Dual-write persistence | PG+Redis/Milvus sync for memory search quality | Medium |
| P2 | Checkpoint/rollback | Decision recovery on validation failure or human rejection | Medium |
| P2 | Agent-controlled archival | Memory quality improvement via agent judgment | Low |
| P2 | apply_patch diff | Fine-grained decision modification | Medium |
| P3 | RRF hybrid search | Search quality improvement for memory/knowledge | Medium |
| P3 | Conversation-as-Runtime (stateless orchestrator) | Architecture cleanliness, testability | Low |

---

## 17. Extended Technology Fusion — Architecture Doc Commitments + Advanced RAG/LLM/Messaging

This section integrates findings from 7 additional technology research tracks beyond the OpenHands/Letta/Cline patterns in Section 16. Each track was chosen because: (a) the architecture doc explicitly names it, (b) it closes a critical design gap, or (c) it significantly improves an existing subsystem.

### 17.1 Dependency Decision Summary

| Technology | Decision | Rationale |
|-----------|----------|-----------|
| **LangGraph** | **Depend** | StateGraph + ToolNode + PostgresSaver + interrupt/resume replaces hand-built ReActEngine + CheckpointManager. Already transitively installed via langchain. |
| **DSPy** | **Depend (partial)** | Signature + Predict + ChatAdapter for prompt engineering. BootstrapFewShot for safe optimization. Do NOT use MIPROv2 (instruction rewriting risky for safety-critical prompts). |
| **Instructor** | **Depend** | Pydantic model enforcement + retry on parse failure. Replaces manual _try_parse(). Supports DeepSeek via Mode.JSON. |
| **NATS JetStream** | **Depend** | Event backbone for Brain→Temporal, escalations, HITL feedback. Replaces InMemoryEventBus. Architecture doc specifies NATS at L6. |
| **Milvus** | **Depend** | Vector search for case library + knowledge base. Architecture doc specifies Milvus at L5. HNSW index, hybrid search, RRF. |
| **Langfuse** | **Depend** | LLM observability: traces, prompt management, cost tracking. Replaces LLMCallTracker. |
| **LlamaIndex** | **Borrow patterns** | Hybrid search, re-ranking, auto-merging, query decomposition — implement as port adapters. Do NOT depend on the library (heavy, abstraction mismatch). |
| **Guardrails AI** | **Borrow OnFailAction pattern** | OnFailAction enum (REASK/FIX/FILTER/REFRAIN) enriches ValidationPipeline. Do NOT depend on the library (too heavy, overlaps ValidationPipeline). |
| **MLLM Vision** | **Borrow Hybrid pattern** | Option C: thumbnail for context + CV tool for detail. ROUTINE=text-only, ADAPTIVE=hybrid, EXPLORATORY=full image. |
| **DeepAgents** | **Borrow middleware pattern** | DeepAgents = create_react_agent + middleware stack. Borrow composable middleware concept. Do NOT depend (domain-specific to coding agents). |
| **Event Sourcing + CAS** | **Borrow + implement** | WeldMap is already Event Sourced (architecture doc). Implement CAS via Redis Lua script. Materialized views for fast read. |

### 17.2 LangGraph — ReAct Engine + Checkpointing

**Key findings from source code research:**

| LangGraph Feature | What It Does | WeldEvent Replacement |
|------------------|-------------|----------------------|
| `StateGraph` | Directed graph with typed state, nodes, conditional edges | Replaces hand-built ReActEngine loop |
| `ToolNode` + `tools_condition()` | Auto-execute LLM tool calls, route between agent and tools | Replaces ToolRegistry.execute() in ReAct loop |
| `PostgresSaver` | PG-backed checkpointing with version history | Replaces hand-built CheckpointManager |
| `interrupt()` / `Command(resume=...)` | Pause execution for human approval, resume later | Replaces request_confirmation tool + WebSocket |
| `astream()` with stream modes | values/updates/messages/custom/tasks/checkpoints/debug | Replaces hand-built streaming |
| `Send` API | Parallel fan-out to sub-graphs | For future parallel tool execution |
| Middleware (from DeepAgents) | Composable pre/post hooks around model calls and tool execution | Complements BeforeToolHook system |

**Integration architecture:**

```python
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.postgres import PostgresSaver

class BrainReActState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    brain_state: BrainStateType           # BrainStateMachine state
    context: ContextSnapshot
    decision: BrainDecision | None
    event_log: list[dict]                 # Append-only event trail

def build_react_graph(tools, checkpointer, interrupts=None):
    builder = StateGraph(BrainReActState)

    # Agent node: LLM reasons + decides tool calls
    builder.add_node("agent", agent_node)
    # Tool node: executes tool calls with beforeTool hooks
    builder.add_node("tools", ToolNode(tools))

    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition, {"tools": "tools", "__end__": END})
    builder.add_edge("tools", "agent")

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupts or [],  # e.g. ["tools"] for approval
    )
```

**Phase plan:**
- Phase 1: Replace ReActEngine with LangGraph StateGraph + ToolNode. Replace CheckpointManager with PostgresSaver. Replace request_confirmation with interrupt/resume.
- Phase 2: Refactor BrainOrchestrator pipeline as a separate StateGraph (each step = node, transitions = edges).
- Phase 3: Compose orchestrator graph + react graph as sub-graphs.

### 17.3 DSPy — Declarative Prompt Engineering

**Key findings:**

| DSPy Feature | What It Does | WeldEvent Use |
|-------------|-------------|---------------|
| `dspy.Signature` | Typed I/O contract (Pydantic-based) with docstring instructions | Replace hand-written prompts for IntentClassifier, KnowledgeQuery, Explanation |
| `dspy.Predict` + `ChatAdapter` | Auto-generate prompt from Signature, auto-parse response | Replace manual _inject_json_hint() + _try_parse() |
| `dspy.ReAct` | Built-in ReAct loop with tool calling | Superseded by LangGraph StateGraph — do NOT use |
| `BootstrapFewShot` | Safe optimization: only adds few-shot examples, never modifies instructions | Auto-discover effective examples from successful runs |
| `dspy.Assert`/`Suggest` | Output validation with retry | REMOVED in current DSPy version — use Instructor instead |
| `dspy.Evaluate` | Metric-based evaluation | Evaluate prompt quality on curated test set |

**Signature example for WeldEvent:**

```python
class IntentClassify(dspy.Signature):
    """Classify user intent in an industrial welding inspection system.
    Rules:
    - If casual conversation, set primary_intent to "cognitive.free_chat"
    - Only classify to professional modes when explicitly relevant
    - When uncertain, prefer "cognitive.free_chat"
    """
    user_message: str = dspy.InputField(desc="Raw user text input")
    context_summary: str = dspy.InputField(desc="Active session context")
    primary_intent: str = dspy.OutputField(desc="Mode ID, e.g. cognitive.knowledge_query")
    confidence: float = dspy.OutputField(desc="0.0-1.0", ge=0.0, le=1.0)
    extracted_entities: dict = dspy.OutputField(desc="Structured entities from user text")
```

**What NOT to DSPy-ify:** BrainOrchestrator (deterministic), ValidationPipeline (rule-based), keyword fallback tier (no LLM).

### 17.4 Instructor — LLM Output Format Enforcement

**Key findings:**

| Instructor Feature | What It Does | WeldEvent Use |
|-------------------|-------------|---------------|
| `Mode.JSON` | Enforce Pydantic output via response_format=json_object | DeepSeek primary — replaces _inject_json_hint() + _try_parse() |
| `Mode.TOOLS` | Enforce output via function calling | Tier 1 fallback — auto-retry on validation error |
| `max_retries` | Auto re-prompt with validation error context | Currently _try_parse() silently returns None on failure |
| `Maybe[T]` | Graceful degradation: result or error+message | For non-critical extractions where failure is acceptable |
| Streaming partials | Incremental Pydantic model construction | For WebSocket streaming of structured responses |

**Integration: format validation → domain validation pipeline:**

```
LLM output → [Instructor: format validation + retry] → valid Pydantic model
                                                           │
                                                           ▼
                              [ValidationPipeline: domain validation] → ValidationResult
```

**From Guardrails, borrow the OnFailAction pattern:**

```python
class OnFailAction(Enum):
    REASK = "reask"      # Re-prompt LLM with error context (Instructor handles this)
    FIX = "fix"           # Auto-fix if possible (e.g., clamp values to range)
    FILTER = "filter"     # Remove invalid field, continue
    REFRAIN = "refrain"   # Return empty/null result
    ESCALATE = "escalate"  # Escalate to human
```

### 17.5 NATS JetStream — Event Backbone

**Architecture doc specifies NATS at L6.** Currently L1 uses InMemoryEventBus (asyncio.Queue dict) — no durability, no cross-process communication.

**Stream design for WeldEvent:**

| Stream | Subjects | Retention | Consumer Pattern | Purpose |
|--------|----------|-----------|-----------------|---------|
| `DECISIONS` | `weldevent.L1.cognitive.decision.*` | LIMITS (7 days) | Durable pull | Brain→Bridge→Temporal trigger |
| `ESCALATIONS` | `weldevent.L1.cognitive.validation.escalation` | LIMITS (1 day) | Durable push | Urgent safety alerts |
| `WORKFLOW_TRIGGERS` | `weldevent.L2.control.trigger.*` | WORK_QUEUE | Durable pull | Exactly-once workflow launch |
| `HITL_SIGNALS` | `weldevent.L1.cognitive.feedback.human` | LIMITS (7 days) | Durable push | Human feedback → Temporal Signal |

**Key integration points:**
- `gateway/weldmap_client.py`: publish decision → `js.publish("weldevent.L1.cognitive.decision.{point}")` with dedup
- Bridge: `js.pull_subscribe()` with `AckPolicy.EXPLICIT`, `backoff=[10,30,60,120,300]`
- Escalation handler: `js.subscribe()` push consumer with `flow_control=True`
- Config hot-reload: JetStream KV store with `kv.watch("brain.*")`

**Subject namespace:**
```
weldevent.L1.cognitive.decision.{decision_point}
weldevent.L1.cognitive.validation.escalation
weldevent.L1.cognitive.feedback.human
weldevent.L2.control.workflow.{workflow_id}.status
weldevent.L3.execution.agent.{agent_type}.report
weldevent.L5.weldmap.change.{entity_type}
```

### 17.6 Milvus — Vector Search

**Architecture doc specifies Milvus at L5.** Currently marked "Phase2 pgvector placeholder".

**Collection design:**

| Collection | Vector Fields | Scalar Fields | Partition Key | Index |
|-----------|--------------|---------------|---------------|-------|
| `case_library` | text_vector(768), image_vector(512) | material, thickness, defect_type, verdict, standard_ref | material | HNSW (M=16, efConstruction=256) |
| `knowledge_standards` | text_vector(768) | standard_id, clause_number, topic, keywords | topic | HNSW |
| `memory_cases` | text_vector(768) | case_id, memory_type, confidence, promotion_status | memory_type | HNSW |
| `memory_experience` | text_vector(768), measurement_vector(64) | domain, confidence, validation_count | domain | HNSW |

**Hybrid search (vector + scalar filter):**
```python
results = client.search(
    collection_name="case_library",
    data=[query_embedding],
    filter='material == "Q235" AND thickness == 12.0',
    limit=20,
    search_params={"metric_type": "COSINE", "params": {"ef": 128}},
)
```

**Multi-vector search with RRF:**
```python
from pymilvus import AnnSearchRequest, RRFRanker

req_text = AnnSearchRequest(data=[text_emb], anns_field="text_vector", param={...}, limit=50)
req_image = AnnSearchRequest(data=[image_emb], anns_field="image_vector", param={...}, limit=50)
results = client.hybrid_search("case_library", reqs=[req_text, req_image], ranker=RRFRanker(k=60), limit=20)
```

**Consistency:** Bounded (default) for case library; Strong for decision-critical queries.

**PG-Milvus sync:** Dual-write (PG first = source of truth, Milvus best-effort) + periodic CDC reconciliation. Already designed in `memory/dual_write.py`.

### 17.7 Event Sourcing + CAS for WeldMap

**Architecture doc states: "Event Sourcing + CAS 乐观锁".** Current `InMemoryWeldMapClient` implements basic CAS; production needs Redis-backed Event Sourcing.

**Domain events:**

```python
class WeldMapEventType(str, Enum):
    DECISION_MADE = "decision_made"                # Cognitive Plane writes
    DECISION_OVERRIDDEN = "decision_overridden"     # Human override
    ESCALATION_RAISED = "escalation_raised"
    WORKFLOW_TRIGGERED = "workflow_triggered"       # Brain → Temporal
    HUMAN_FEEDBACK_RECEIVED = "human_feedback_received"
    # ... plus workflow/image/mask/annotation/rendering/spc events
```

**CAS implementation: Redis Lua script (atomic, single round-trip):**
```python
CAS_WRITE_LUA = """
local key_data = KEYS[1]
local key_version = KEYS[2]
local key_events = KEYS[3]
local expected_version = tonumber(ARGV[1])
local new_data = ARGV[2]
local new_version = tonumber(ARGV[3])
local event_data = ARGV[4]

if expected_version >= 0 then
    local current = tonumber(redis.call('GET', key_version) or '0')
    if current ~= expected_version then
        return {0, current}
    end
end

redis.call('SET', key_data, new_data)
redis.call('SET', key_version, new_version)
redis.call('XADD', key_events, '*', 'data', event_data)
return {1, new_version}
"""
```

**Materialized views for fast read + snapshot on PUBLISHED for recovery.**

**CognitiveGateway → Event-Sourced WeldMap:**
- Write: CAS write → event appended → materialized view updated → NATS notify
- Read: query materialized view (fast) or replay events (audit/time-travel)

### 17.8 MLLM — Multimodal Reasoning

**Recommended: Option C (Hybrid — thumbnail + CV tool):**

| Reasoning Mode | Image Strategy | Cost | When |
|---------------|----------------|------|------|
| ROUTINE | CV tool only (no image to LLM) | Text tokens only | Known defect patterns |
| ADAPTIVE | Low-detail thumbnail (85 tokens) + CV tool | Low | Context awareness + detail from tool |
| EXPLORATORY | High-detail image + CV tool | High | Novel situations requiring visual reasoning |

**Implementation:** Extend `LLMRequest` to support `image_url` content blocks. Add `vision_analysis` tool. ROUTINE/ADAPTIVE/EXPLORATORY paths naturally map to different image strategies.

**Provider support:** DeepSeek-VL2 (OpenAI-compatible format, self-hosted), GPT-4o (API, high-detail), Claude (API). Use existing `OpenAIProvider` transport — zero changes needed for image_url messages.

### 17.9 Advanced RAG Patterns (from LlamaIndex research)

**Priority order:**

| Priority | Pattern | Implementation | Benefit |
|----------|---------|---------------|---------|
| P0 | Hybrid Search (BM25 + dense + RRF) | `rank_bm25` + embedding + RRF fusion in port adapter | Exact match for standards IDs + semantic for context |
| P0 | Re-ranking (BGE-reranker-v2-m3) | `RerankPort` between retrieval and synthesis | Critical for distinguishing "preheat temperature" vs "interpass temperature" |
| P1 | LLM-based Router | Intent classifier returns `target_knowledge_type` | Replace keyword `_detect_category()` with LLM routing |
| P1 | Auto-merging (hierarchical chunks) | `parent_chunk_id` metadata on `KnowledgeResult` | Multiple clause matches → return full section |
| P2 | Query decomposition | `asyncio.gather()` for parallel multi-port queries | Complex cross-domain questions |

**Do NOT depend on LlamaIndex.** WeldEvent's port architecture is cleaner. Implement patterns as port adapters.

### 17.10 Langfuse — LLM Observability

**Replace `LLMCallTracker` with Langfuse.**

| Langfuse Feature | WeldEvent Use |
|-----------------|---------------|
| `langfuse.openai` wrapper | 1-line change to OpenAIProvider — auto-traces all LLM calls |
| `@observe()` decorator | Full pipeline tracing (retrieval → reasoning → validation) |
| Prompt management | Externalize prompts from code → domain experts iterate without deployment |
| Cost tracking | Per-model token counting, cost dashboards, anomaly detection |
| Sessions | Group traces by `SessionId` — matches existing session model |
| Evaluation | Score-based quality tracking for LLM outputs |

**Integration:** `langfuse.openai.AsyncOpenAI` replaces raw `AsyncOpenAI` in `_bootstrap_llm()`. Keep `LLMCallTracker` as offline fallback for air-gapped environments.

### 17.11 DeepAgents — Middleware Pattern

**Key finding: DeepAgents is NOT a separate graph engine.** It is `create_react_agent()` + composable middleware stack:

| Middleware | DeepAgents | WeldEvent Adaptation |
|-----------|-----------|---------------------|
| TodoListMiddleware | `write_todos` tool for task tracking | Borrow for `design_workflow` tool decomposition |
| SubAgentMiddleware | `task` tool spawns sub-agent graph | Borrow for Sub-Agent delegation in BrainCore |
| SummarizationMiddleware | Auto-compact when token threshold exceeded | Already implemented as ContextCompactor (from Letta) |
| HumanInTheLoopMiddleware | Pause on specified tool calls | Already implemented as PolicyHook + interrupt (from LangGraph) |
| FilesystemMiddleware | File operations with permissions | Not applicable — industrial domain uses different tools |

**Do NOT depend on DeepAgents.** Borrow the middleware composition pattern — each middleware wraps model calls and tool execution with pre/post hooks.

### 17.12 Updated Package Structure (v5)

New files added by this section:

```
cognitiveplane/
├── interaction/
│   ├── api/
│   │   └── chat.py          # Updated: LangGraph StateGraph integration
│   └── multimodal.py        # Updated: image_url support for MLLM
├── governance/
│   ├── tool_policy.py       # (already in v4)
│   └── on_fail.py           # 🆕 OnFailAction enum (from Guardrails)
├── control/
│   ├── react.py             # Updated: LangGraph StateGraph + ToolNode
│   ├── hooks.py             # Updated: LangGraph interrupt integration
│   ├── checkpoint.py        # Updated: Delegated to LangGraph PostgresSaver
│   ├── dspy_signatures.py   # 🆕 DSPy Signatures for prompt engineering
│   └── dspy_optimize.py     # 🆕 BootstrapFewShot optimizer
├── gateway/
│   ├── events.py            # (already in v4)
│   └── nats_publisher.py    # 🆕 NATS JetStream publisher (replaces InMemoryEventBus)
├── knowledge/
│   ├── rerank.py            # 🆕 BGE-reranker-v2-m3 re-ranking port
│   ├── hybrid_search.py     # 🆕 BM25 + dense + RRF hybrid search
│   └── decomposition.py     # 🆕 Query decomposition for multi-port search
├── memory/
│   ├── blocks.py            # (already in v4)
│   ├── dual_write.py        # Updated: Milvus integration in dual-write
│   └── milvus_client.py     # 🆕 Milvus collection management + hybrid search
├── capability/
│   ├── provider.py          # Updated: Instructor Mode.JSON integration
│   ├── instructor_adapter.py # 🆕 Instructor wrapper for format validation + retry
│   └── vision.py            # 🆕 MLLM vision adapter (thumbnail/full/CV-tool)
├── adapters/
│   ├── nats/                # 🆕 NATS JetStream adapter
│   │   ├── __init__.py
│   │   ├── connection.py    # Connection management + lifecycle
│   │   ├── streams.py       # Stream creation (DECISIONS, ESCALATIONS, etc.)
│   │   └── kv_config.py     # JetStream KV for config hot-reload
│   ├── milvus/              # 🆕 Milvus adapter
│   │   ├── __init__.py
│   │   ├── collections.py   # Collection schema + index management
│   │   └── sync.py          # PG→Milvus CDC reconciliation
│   ├── langfuse/            # 🆕 Langfuse observability adapter
│   │   ├── __init__.py
│   │   └── tracing.py       # @observe decorator + langfuse.openai wrapper
│   └── weldmap/
│       ├── weldmap_http.py  # Updated: Event Sourcing + CAS via Lua script
│       └── event_sourcing.py # 🆕 CAS Lua script + materialized views + replay
└── shared/
    └── dto/
        ├── context.py       # Updated: WeldMapDomainEvent (Event Sourcing)
        └── events.py        # 🆕 WeldMapEventType enum + domain event models
```

### 17.13 Updated CognitiveDependencies (Per-Plane Groups)

See Section 0.5 for the base per-plane groups. Phase 2 technology additions extend specific plane groups only:

```python
# Phase 2 additions to existing plane groups (not new top-level deps)

@dataclass
class CapabilityDeps:   # Phase 2 additions
    llm_provider: LLMProvider
    instructor: InstructorClient          # 🆕 format validation + retry
    vision: VisionAdapter                 # 🆕 MLLM vision

@dataclass
class ControlDeps:      # Phase 2 additions
    orchestrator: BrainOrchestrator
    supervisor: SupervisorCenter
    tool_policy: ToolPolicy
    hooks: list[BeforeToolHook]
    react_graph: CompiledStateGraph       # 🆕 LangGraph
    dspy_signatures: dict[str, dspy.Signature]  # 🆕 DSPy

@dataclass
class KnowledgeDeps:    # Phase 2 additions
    rag_query: RAGQueryPort
    standards_query: StandardsQueryPort
    case_library: CaseLibraryQueryPort
    process_knowledge: ProcessKnowledgePort
    reranker: RerankPort                  # 🆕 BGE-reranker
    hybrid_search: HybridSearchPort       # 🆕 BM25 + dense + RRF

@dataclass
class MemoryDeps:       # Phase 2 additions
    search: MemorySearchPort
    read: MemoryReadPort
    write: MemoryWritePort
    block_manager: BlockManager
    compactor: ContextCompactor
    dual_write: DualWriteMemoryService    # 🆕 PG+Milvus
    milvus: MilvusKnowledgeAdapter        # 🆕 Milvus vector search

@dataclass
class GatewayDeps:      # Phase 2 additions
    read: CognitiveGatewayReadPort
    write: CognitiveGatewayWritePort
    nats_publisher: NATSPublisher         # 🆕 NATS JetStream
```

### 17.14 Architecture Doc Alignment — Final Checklist

| Architecture Doc Commitment | Implementation | Status |
|----------------------------|---------------|--------|
| DeepAgents (LangGraph封装) | LangGraph StateGraph + ToolNode + middleware pattern | ✅ Depend on LangGraph, borrow DeepAgents middleware |
| Temporal (L2) | CognitiveGateway → NATS JetStream → Temporal Signal | ✅ Event-driven, durable |
| DSPy + Guardrails (L4) | DSPy Signatures + Instructor + OnFailAction | ✅ DSPy for prompts, Instructor for format, Guardrails pattern for validation |
| NATS JetStream (L6) | 4 streams + KV config + subject namespace | ✅ Durable, exactly-once, backpressure |
| Milvus (L5) | HNSW index, hybrid search, dual-write PG+Milvus | ✅ Not Phase2 placeholder anymore |
| Event Sourcing + CAS (L5) | Redis Lua CAS + Streams + materialized views | ✅ Production-grade |
| MLLM + CV (L3/L4) | Hybrid vision (thumbnail + CV tool) per reasoning mode | ✅ Cost-controlled |
| Langfuse (new) | LLM observability replacing LLMCallTracker | ✅ Production-grade |
| LlamaIndex (borrowed) | Hybrid search + re-ranking + auto-merging + decomposition | ✅ Patterns, not dependency |