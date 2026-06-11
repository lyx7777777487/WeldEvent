# CognitivePlane Architecture Redesign — Aligned with WeldEvent 6-Layer System

**Date:** 2026-06-11
**Status:** Draft v4 (ReAct + borrowed patterns from OpenHands/Letta/Cline source research)
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

### Solution: Typed Dependency Container + Constructor Injection

```python
@dataclass
class CognitiveDependencies:
    """All L1 Cognitive Plane dependencies. Assembled only in composition root."""

    # Capability
    llm_provider: LLMProvider

    # Control
    orchestrator: BrainOrchestrator
    react_engine: ReActEngine
    supervisor: SupervisorCenter
    tool_policy: ToolPolicy
    hooks: list[BeforeToolHook]
    checkpoint_mgr: CheckpointManager

    # Knowledge
    rag_query: RAGQueryPort
    standards_query: StandardsQueryPort
    case_library: CaseLibraryQueryPort
    process_knowledge: ProcessKnowledgePort

    # Memory
    memory_search: MemorySearchPort
    memory_read: MemoryReadPort
    memory_write: MemoryWritePort
    memory_promotion: MemoryPromotionPort
    memory_confidence: MemoryConfidencePort
    block_manager: BlockManager          # 🆕 from Letta
    compactor: ContextCompactor          # 🆕 from Letta
    dual_write: DualWriteMemoryService   # 🆕 from Letta

    # Gateway (CognitiveGateway — Brain's write port to WeldMap)
    gateway_read: CognitiveGatewayReadPort
    gateway_write: CognitiveGatewayWritePort

    # Governance
    validation_pipeline: ValidationPipelinePort
    review_repository: HumanReviewRequestRepository
    tool_policy: ToolPolicy              # 🆕 from Cline

    # Copilot
    copilot: IndustrialCopilot

    # Interaction
    context_resolver: ContextResolver
```

### Before vs After

```python
# BEFORE: dict[str, Any] + global singleton
class BrainOrchestrator:
    async def execute_workflow_design(self, ..., ports: dict[str, Any]):
        if self._has_port(ports, "RAGQueryPort"):
            rag_port: RAGQueryPort = ports["RAGQueryPort"]

# AFTER: typed constructor injection + hooks + checkpoint
class BrainOrchestrator:
    def __init__(
        self,
        rag_query: RAGQueryPort,
        memory_search: MemorySearchPort,
        memory_write: MemoryWritePort,     # Fix P0-3: Memory write chain
        reasoning: ReasoningPort,
        planning: PlanningPort,
        reflection: ReflectionPort,
        validation: ValidationPipelinePort,
        gateway_write: CognitiveGatewayWritePort,
        decision_repo: BrainDecisionRepository,
        supervisor: SupervisorCenter,
        hooks: list[BeforeToolHook],       # 🆕 from OpenHands/Cline
        checkpoint_mgr: CheckpointManager, # 🆕 from Cline
    ):
        self._rag_query = rag_query
        self._memory_search = memory_search
        self._memory_write = memory_write
        self._hooks = hooks
        self._checkpoint_mgr = checkpoint_mgr
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
    # 1. Adapters to L5/L6
    db_engine = create_db_engine(DatabaseConfig.from_env())
    redis = RedisCache(RedisConfig.from_env())
    minio = MinioStorage(MinioConfig.from_env())
    weldmap = WeldMapHTTPClient(WeldMapConfig.from_env())
    setup_tracing(TelemetryConfig.from_env())

    # 2. LLM Capability
    llm_provider = _bootstrap_llm()

    # 3. Port Adapters
    decision_repo = PostgreSQLDecisionRepository(db_engine)
    memory_repo = HybridMemoryRepository(redis, db_engine)
    knowledge_rag = PostgreSQLRAGAdapter(db_engine)
    review_repo = PostgreSQLReviewRepository(db_engine)
    gateway_read = WeldMapReadGateway(weldmap)
    gateway_write = WeldMapWriteGateway(weldmap)

    # 4. Memory (Letta-inspired)
    block_manager = BlockManager()  # L0/L1 working memory blocks
    dual_write = DualWriteMemoryService(pg_repo=memory_repo, vector_store=redis)
    compactor = ContextCompactor(llm_provider=llm_provider)

    # 5. Governance (Cline-inspired)
    tool_policy = ToolPolicy()  # Default rules + per-tool overrides
    validation_pipeline = ValidationPipeline(...)
    escalation_tracker = EscalationTracker()  # Per-case isolation

    # 6. Control (BrainCore) — Hooks assembled first
    hooks = [
        SafetyHook(),                 # Block on safety status
        PolicyHook(tool_policy),      # Enforce ToolPolicy
    ]
    supervisor = SupervisorCenter()
    checkpoint_mgr = CheckpointManager(memory_write=memory_repo)
    orchestrator = BrainOrchestrator(
        rag_query=knowledge_rag,
        memory_search=memory_repo,
        memory_write=memory_repo,       # Fix P0-3: wire Memory write
        reasoning=LLMReasoningPort(llm_provider),
        planning=LLMPlanningPort(llm_provider),
        reflection=LLMReflectionPort(llm_provider),
        validation=validation_pipeline,
        gateway_write=gateway_write,
        decision_repo=decision_repo,
        supervisor=supervisor,
        hooks=hooks,                    # 🆕 beforeTool hooks
        checkpoint_mgr=checkpoint_mgr,  # 🆕 checkpoint/rollback
    )

    # 7. ReAct Engine
    tool_registry = ToolRegistry(deps)  # Registers 9 + archive_memory = 10 tools
    react_engine = ReActEngine(
        llm_provider=llm_provider,
        tool_registry=tool_registry,
        state_machine=BrainStateMachine(),
        supervisor=supervisor,
        hooks=hooks,
        compactor=compactor,
    )

    # 8. Copilot
    copilot = IndustrialCopilot(...)

    # 9. Interaction
    context_resolver = ContextResolver()

    return CognitiveDependencies(...)
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