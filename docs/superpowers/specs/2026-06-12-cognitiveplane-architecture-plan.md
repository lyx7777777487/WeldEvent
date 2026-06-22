# Cognitive Plane 



---

## 一、整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    L1 Cognitive Plane                            │
├─────────┬─────────┬─────────┬─────────┬─────────┬───────────────┤
│Interact │Govern   │Control  │Gateway  │Knowledge│Memory │Capabil│
│  ion    │ ance    │         │         │         │       │ ity   │
└────┬────┴────┬────┴────┬────┴────┬────┴────┬────┴───┬───┴───┬───┘
     │         │         │         │         │        │       │
     ▼         ▼         ▼         ▼         ▼        ▼       ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Adapters (L5/L6)                           │
│  PostgreSQL │ Redis │ MinIO │ WeldMap HTTP │ NATS │ Milvus     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    L2 Temporal Control Plane                    │
│              (WorkflowTemplate → TemplateWorkflow)              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 二、7-Plane 职责划分

### 2.1 Interaction Plane（交互层）

**职责**：用户交互入口，处理所有外部输入

```
interaction/
├── api/chat.py          # REST/WebSocket 入口
├── context.py           # 上下文解析
├── session.py           # 会话管理
└── multimodal.py        # 多模态输入解析
```

**核心对象**：
- `UserMessage`：用户输入（文本/图片/标注）
- `InteractionResponse`：系统响应
- `ActiveContext`：当前活跃上下文

---

### 2.2 Governance Plane（治理层）

**职责**：审批、验证、升级、权限控制

```
governance/
├── approval.py          # 审批服务（Agent建议 → 人决策 → 系统执行）
├── tool_policy.py       # 工具调用策略（哪些工具需要人工确认）
├── escalation.py        # 升级追踪（异常情况升级到人工）
├── pipeline.py          # 4阶段验证管道
└── validators/          # 各阶段验证器
    ├── safety.py        # 安全验证
    ├── rule.py          # 规则验证
    ├── shadow.py        # 影子模式验证
    └── consistency.py   # 一致性验证
```

**核心流程**：
```
Agent决策 → Safety验证 → Rule验证 → Shadow验证 → Consistency验证 → 执行
                ↓ (失败)        ↓ (失败)
              Reask/Fix      Escalate to Human
```

---

### 2.3 Control Plane（控制层）

**职责**：Brain 核心决策引擎，ReAct 循环

```
control/
├── react.py              # ReAct 引擎（Thought → Action → Observation）
├── orchestrator.py       # Brain 编排器（驱动 ReAct 循环）
├── tool_registry.py      # 工具注册表
├── tools/                # 工具实现
│   ├── search_standards.py    # 查询标准
│   ├── search_cases.py        # 查询案例
│   ├── search_process.py      # 查询工艺知识
│   ├── read_weldmap.py        # 读取 WeldMap
│   ├── design_workflow.py     # 设计检测方案
│   ├── adjust_parameter.py    # 调整参数
│   ├── request_confirmation.py # 请求人工确认
│   ├── explain_decision.py    # 解释决策
│   ├── escalate.py            # 升级
│   └── archive_memory.py      # Agent-controlled 记忆归档
├── hooks.py              # 工具调用钩子（ALLOW/DENY）
├── event_log.py          # 事件日志（状态转换记录）
├── checkpoint.py         # 决策检查点/回滚
├── state_machine.py      # Brain 状态机（12种状态）
├── persona.py            # 角色选择（Planner/Copilot/CAA）
├── reasoning_mode.py     # 推理模式（ROUTINE/ADAPTIVE/EXPLORATORY）
├── planner.py            # 自建规划器（任务分解）
├── reflector.py          # 自建反思器（反思推理）
├── sub_agent.py          # 子代理委托
├── supervisor.py         # 监督中心（Health/Loop/Timeout）
├── decision_factory.py   # 决策工厂（按 Persona 生成不同输出）
├── fallback.py           # 3级降级策略
├── deps.py               # CognitiveDependencies 类型化 DI
├── ports.py              # 控制层端口（ReasoningPort, PlanningPort, etc.）
├── exceptions.py         # 控制层异常
├── repositories/         # InMemoryBrainDecisionRepository
└── adapters/             # MockDeepAgentsAdapter（legacy bridge）
```

**ReAct 循环**：
```
┌─────────────────────────────────────────────────┐
│                                                 │
│  ┌─────────┐    ┌─────────┐    ┌───────────┐  │
│  │ Thought │───▶│ Action  │───▶│Observation│──┤
│  └─────────┘    └─────────┘    └───────────┘  │
│       ▲                               │        │
│       └───────────────────────────────┘        │
│                   (循环)                        │
└─────────────────────────────────────────────────┘
```

**12 种 Brain 状态**：
1. `IDLE` → 空闲
2. `OBSERVING` → 接收输入
3. `UNDERSTANDING` → 理解上下文
4. `KNOWLEDGE_RETRIEVAL` → 知识检索
5. `MEMORY_RETRIEVAL` → 记忆检索
6. `MEMORY_MATCHING` → 记忆匹配（Routine 模式）
7. `REASONING` → 推理中
8. `DECISION_GENERATION` → 决策生成
9. `VALIDATION` → 验证
10. `PUBLICATION` → 发布
11. `WAITING_FEEDBACK` → 等待反馈
12. `ERROR` / `ABORT` → 错误/中止

---

### 2.4 Gateway Plane（关口层）

**职责**：L1 → WeldMap 唯一写出通道，强制状态一致性

```
gateway/
├── write_gateway.py     # 写出通道（Decision/Escalation/Instruction）
├── read_gateway.py      # 读取通道（WeldMap 查询）
├── events.py            # ActionEvent/ObservationEvent 事件对
└── weldmap_client.py    # WeldMap HTTP/gRPC 客户端
```

**核心约束**：
- **所有 Brain 决策必须通过 Gateway 写出**
- **禁止 Brain 直接访问 WeldMap**
- **Gateway 负责事件配对（Action-Observation pairing）**

```
Brain → CognitiveGateway → WeldMap
         (唯一通道)
```

---

### 2.5 Knowledge Plane（知识层）

**职责**：RAG 知识检索（标准、案例、工艺、设备）

```
knowledge/
├── rag.py               # RAG 查询入口
├── standards.py         # 标准查询
├── cases.py             # 案例库查询
├── process.py           # 工艺知识查询
└── equipment.py         # 设备知识查询
```

**查询流程**：
```
Query → Embedding → Vector Search → Re-rank → Result
```

---

### 2.6 Memory Plane（记忆层）

**职责**：工业记忆 L0-L5 分层管理

```
memory/
├── hierarchy.py         # L0-L5 层级定义
├── blocks.py            # Block-based 工作记忆
├── search.py            # 多级搜索（向量 + FTS + RRF）
├── promotion.py         # 记忆晋升（RAW → VALIDATED → PROMOTED）
├── compaction.py        # 上下文压缩
├── dual_write.py        # 双写（PG + 向量库）
└── confidence.py        # 置信度评分
```

**L0-L5 层级**：

| 层级 | 名称 | 存储 | 生命周期 |
|-----|------|------|---------|
| L0 | Realtime | Redis | 会话内 |
| L1 | Working | Redis | 会话间 |
| L2 | Case | PostgreSQL | 持久化 |
| L3 | Experience | PostgreSQL | 持久化 |
| L4 | Knowledge | PostgreSQL + Milvus | 永久 |
| L5 | Audit | PostgreSQL | 审计日志 |

---

### 2.7 Capability Plane（能力层）

**职责**：LLM 推理能力封装

```
capability/
├── ports.py             # LLMProvider ABC
├── provider.py          # LLMRequest/LLMResponse
├── deepseek.py          # DeepSeek 适配器
├── openai_compat.py     # OpenAI 兼容适配器
├── openai_provider.py   # OpenAI/DeepSeek provider 实现
├── mock.py              # Mock LLM（测试用，规范实现）
├── mock_provider.py     # Legacy re-export shim → mock.py
├── config.py            # LLMConfig, OpenAIConfig
└── tracking.py          # LLMCallTracker（待 Phase 2 替换为 Langfuse）
```

---

## 三、核心数据流

### 3.1 用户请求处理流程

```
用户输入
    │
    ▼
┌─────────────────┐
│ Interaction     │  解析 UserMessage
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Governance      │  验证权限、安全检查
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Control         │  ReAct 循环
│  ┌───────────┐  │
│  │ Thought   │  │  调用 Knowledge/Memory
│  │ Action    │  │  调用 Tools
│  │Observation│  │
│  └───────────┘  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Gateway         │  写出 Decision
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ WeldMap (L5)    │  状态更新
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Bridge          │  BrainDecision → WorkflowTemplate
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Temporal (L2)   │  工作流执行
└─────────────────┘
```

### 3.2 Human-Governed 决策模型

```
┌─────────────────────────────────────────────────────┐
│                                                     │
│   Agent ──────▶ Suggest ──────▶ Human              │
│                                     │               │
│                                     ▼               │
│                                  Decide             │
│                                     │               │
│                                     ▼               │
│   System ◀────── Execute ◀───────┘               │
│                                                     │
└─────────────────────────────────────────────────────┘
```

**关键点**：
- Agent 只能**建议**，不能直接执行
- Human 拥有**决策权**
- System 负责实际**执行**

---

## 四、Adapters 层

**职责**：隔离外部依赖，L1 连接 L5/L6 的唯一入口

```
adapters/
├── database/            # PostgreSQL
│   ├── decision_repo.py      # BrainDecision 持久化
│   ├── memory_repo.py        # Memory 持久化
│   └── knowledge_repo.py     # Knowledge 持久化
├── cache/
│   └── redis_client.py       # Redis L0/L1 缓存
├── storage/
│   └── minio_client.py       # MinIO 图片/报告存储
├── weldmap/
│   └── weldmap_http.py       # WeldMap HTTP 客户端
└── observability/
    ├── tracing.py            # OpenTelemetry
    └── metrics.py            # Metrics
```

---

## 五、Shared 共享内核

**职责**：最小化共享，仅 L1 内部共享

```
shared/
├── types.py              # ID 类型（DecisionId, CaseId, SessionId）
├── enums.py              # 跨 Plane 枚举（BrainStateType, ReasoningMode）
├── dto/                  # 跨 Plane DTO（规范路径）
│   ├── context.py        # ContextSnapshot, DomainEvent
│   ├── decision.py       # BrainDecision + DecisionOutput（re-export from dto_decision/）
│   ├── memory.py         # MemorySearchQuery, MemoryRecord
│   ├── knowledge.py      # RAGQuery, KnowledgeResult
│   ├── validation.py     # ValidationResult
│   ├── escalation.py     # Escalation（re-export from dto.gateway）
│   ├── gateway.py        # PublishResult, Escalation, WorkflowState
│   ├── collaboration.py  # HumanReviewRequest, FeedbackContent
│   ├── learning.py       # LearningContent, LearningEvent
│   ├── deepagents.py     # Conclusion, Plan, Strategy（待迁移入 control/）
│   ├── persona.py        # PersonaFrame, PersonaSelectionResult
│   ├── reasoning_mode.py # ReasoningModeSelectionInput/Result
│   └── weldmap_events.py # WeldMapEventType, WeldMapDomainEvent
├── dto_context.py        # Legacy shim → dto.context
├── dto_knowledge.py      # Legacy shim → dto.knowledge
├── ...（其余 dto_*.py 均为 shim）
└── dto_decision/         # BrainDecision 子包（独立，非单一文件）
    ├── __init__.py
    ├── decision.py
    ├── outputs.py        # 15 种 DecisionOutputContent
    ├── recommendation.py # WorkflowRecommendation, ParameterRecommendation
    ├── assessment.py     # RiskAssessment
    └── directive.py      # InvestigationDirective
```

---

## 六、与 L2 Temporal 集成

### 6.1 Bridge 架构

```
L1 Cognitive Plane                L2 Temporal Control Plane
        │                                    │
        ▼                                    ▼
┌───────────────┐                  ┌─────────────────────┐
│CognitiveGateway│ ──Decision──▶  │ EventConnector      │
└───────────────┘                  └──────────┬──────────┘
                                            │
                                            ▼
                                   ┌─────────────────────┐
                                   │ DecisionTranslator  │
                                   │ BrainDecision →     │
                                   │ WorkflowTemplate    │
                                   └──────────┬──────────┘
                                            │
                                            ▼
                                   ┌─────────────────────┐
                                   │ WorkflowLauncher    │
                                   │ Template → Temporal │
                                   └──────────┬──────────┘
                                            │
                                            ▼
                                   ┌─────────────────────┐
                                   │ TemplateWorkflow    │
                                   │ (Temporal Workflow) │
                                   └─────────────────────┘
```


---

## 七、实现优先级

### Phase 1（核心）
- [x] `control/react.py` — ReAct 引擎
- [x] `control/orchestrator.py` — Brain 编排器
- [x] `control/tool_registry.py` — 工具注册表
- [x] `control/tools/` — 核心工具实现（10 tools）
- [x] `gateway/write_gateway.py` — 写出通道
- [x] `control/hooks.py` — 工具调用钩子
- [x] `control/event_log.py` — 事件日志
- [x] `control/checkpoint.py` — 决策检查点
- [x] `control/persona.py` — 角色选择
- [x] `control/reasoning_mode.py` — 推理模式选择
- [x] `control/planner.py` — 自建规划器
- [x] `control/reflector.py` — 自建反思器
- [x] `control/sub_agent.py` — 子代理委托
- [x] `control/supervisor.py` — 监督中心
- [x] `control/decision_factory.py` — 决策工厂
- [x] `control/fallback.py` — 3级降级策略
- [x] `control/deps.py` — CognitiveDependencies 类型化 DI
- [x] `control/exceptions.py` — 控制层异常

### Phase 2（记忆+知识）
- [x] `memory/hierarchy.py` — L0-L5 层级
- [x] `memory/blocks.py` — Block-based 工作记忆
- [x] `memory/dual_write.py` — 双写（PG + 向量库）
- [x] `memory/compaction.py` — 上下文压缩
- [x] `memory/search.py` — 多级搜索
- [x] `memory/promotion.py` — 记忆晋升
- [x] `memory/archive.py` — 记忆归档
- [x] `memory/confidence.py` — 置信度评分
- [x] `knowledge/rag.py` — RAG 查询
- [x] `knowledge/standards.py` — 标准查询
- [x] `knowledge/cases.py` — 案例库查询
- [x] `knowledge/process.py` — 工艺知识查询
- [x] `knowledge/equipment.py` — 设备知识查询
- [x] `adapters/database/` — PostgreSQL 适配器（7 repo）
- [x] `adapters/cache/redis_client.py` — Redis L0/L1 缓存
- [x] `adapters/storage/minio_client.py` — MinIO 存储
- [x] `adapters/weldmap/weldmap_http.py` — WeldMap HTTP 客户端
- [x] `adapters/weldmap/event_sourcing.py` — CAS 事件溯源

### Phase 3（交互+治理）
- [x] `interaction/api/chat.py` — REST/WebSocket 入口
- [x] `interaction/api/notifications.py` — 通知推送
- [x] `interaction/session.py` — 会话管理
- [x] `interaction/context.py` — 上下文解析
- [x] `interaction/multimodal.py` — 多模态输入解析
- [x] `governance/pipeline.py` — 4阶段验证管道
- [x] `governance/approval.py` — 审批服务
- [x] `governance/tool_policy.py` — 工具调用策略
- [x] `governance/escalation.py` — 升级追踪（per-case isolation）
- [x] `governance/review.py` — 人工审查
- [x] `governance/on_fail.py` — OnFailAction
- [x] `governance/validators/` — 安全/规则/影子/一致性验证器
- [x] `copilot/qa.py` — 知识问答
- [x] `copilot/explain.py` — 决策解释
- [x] `copilot/investigate.py` — 异常调查
- [x] `copilot/govern.py` — 审批辅助
- [x] `copilot/operate.py` — 操作指导
- [x] `bridge/` — L1→L2 集成（EventConnector + DecisionTranslator + WorkflowLauncher）

### Phase 4（基础设施 — Phase 2 技术引入）
- [ ] `adapters/nats/` — NATS JetStream
- [ ] `adapters/milvus/` — Milvus 向量库
- [ ] `adapters/langfuse/` — LLM 可观测性
- [ ] LangGraph 替换手写 ReAct 引擎
- [ ] Instructor 替换 _try_parse()
- [ ] DSPy Signatures 替换手写 prompt
- [ ] Reranker + Hybrid Search 升级知识检索
- [ ] MLLM Vision 多模态推理

---

## 八、技术借鉴来源

> 本架构借鉴了以下开源项目的核心思想，但**不依赖它们**，而是将模式内化为 WeldEvent 自有实现。

### 8.1 OpenHands

| 借鉴点 | WeldEvent 实现 | 文件位置 |
|-------|---------------|---------|
| EventLog（Append-only 事件日志） | `control/event_log.py` | 状态转换记录为不可变事件 |
| Conversation-as-Runtime | `control/react.py` | ReAct 循环即运行时，无持久化状态机 |
| Action/Observation 配对 | `gateway/events.py` | ActionEvent/ObservationEvent 双 ID 配对 |

**核心思想**：所有状态转换都记录为不可变事件，支持回放和审计。

---

### 8.2 Letta（原 MemGPT）

| 借鉴点 | WeldEvent 实现 | 文件位置 |
|-------|---------------|---------|
| Block-based Memory | `memory/blocks.py` | 显式 checkpoint，XML 编译 |
| Dual-Write（PG + 向量库） | `memory/dual_write.py` | PG 永远写，向量库尽力写 |
| Context Compaction | `memory/compaction.py` | 昂贵→便宜降级链 |
| Memory Hierarchy | `memory/hierarchy.py` | L0-L5 分层，晋升规则 |

**核心思想**：记忆是分层的、可晋升的、可压缩的，工作记忆有显式边界。

---

### 8.3 Cline

| 借鉴点 | WeldEvent 实现 | 文件位置 |
|-------|---------------|---------|
| ToolPolicy（工具调用策略） | `governance/tool_policy.py` | 哪些工具需要人工确认 |
| Checkpoint（决策检查点） | `control/checkpoint.py` | 决策快照 + 回滚 |
| Shadow Mode（影子模式） | `governance/validators/shadow.py` | 先观察后执行 |

**核心思想**：AI 只能建议，人拥有决策权，系统支持回滚。

---

### 8.4 DeepAgents

| 借鉴点 | WeldEvent 实现 | 文件位置 |
|-------|---------------|---------|
| Middleware Pattern | `control/hooks.py` | 工具调用前后钩子 |
| TodoList Middleware | `control/tools/design_workflow.py` | 任务分解 |
| Sub-Agent Delegation | `control/sub_agent.py` | 子代理委托 |
| Summarization Middleware | `memory/compaction.py` | 上下文压缩 |

**核心思想**：通过中间件组合扩展能力，而非继承。

---

### 8.5 LangGraph

| 借鉴点 | WeldEvent 实现 | 文件位置 |
|-------|---------------|---------|
| StateGraph（状态图） | `control/react.py` | ReAct 循环作为状态图 |
| ToolNode（工具节点） | `control/tool_registry.py` | 工具注册和执行 |
| Interrupt（中断） | `control/hooks.py` | 人工确认时中断 |
| PostgresSaver（检查点） | `control/checkpoint.py` | 状态持久化 |

**核心思想**：图结构的状态机，支持中断和恢复。

---

### 8.6 DSPy

| 借鉴点 | WeldEvent 实现 | 文件位置 |
|-------|---------------|---------|
| Signatures（签名） | `control/dspy_signatures.py` | Prompt 即函数签名 |
| BootstrapFewShot | `control/dspy_optimize.py` | 自动优化 Prompt |

**核心思想**：Prompt 工程即编程，而非字符串拼接。

---

### 8.7 Instructor

| 借鉴点 | WeldEvent 实现 | 文件位置 |
|-------|---------------|---------|
| Pydantic 验证 | `capability/provider.py` | LLM 输出强制验证 |
| Retry 机制 | `capability/instructor_adapter.py` | 验证失败自动重试 |
| Mode.JSON | `capability/provider.py` | JSON 模式输出 |

**核心思想**：LLM 输出即结构化数据，验证失败自动修复。

---

### 8.8 Guardrails

| 借鉴点 | WeldEvent 实现 | 文件位置 |
|-------|---------------|---------|
| OnFailAction | `governance/on_fail.py` | REASK/FIX/FILTER/REFRAIN/ESCALATE |
| Validation Pipeline | `governance/pipeline.py` | 4 阶段验证管道 |

**核心思想**：验证失败有多种处理策略，而非简单报错。

---

### 8.9 LlamaIndex（模式借鉴，非依赖）

| 借鉴点 | WeldEvent 实现 | 文件位置 |
|-------|---------------|---------|
| Hybrid Search（BM25 + 向量） | `knowledge/hybrid_search.py` | 精确匹配 + 语义搜索 |
| Re-ranking | `knowledge/rerank.py` | BGE-reranker-v2-m3 |
| Auto-merging | `knowledge/` | 多 clause 匹配 → 返回完整 section |
| Query Decomposition | `knowledge/decomposition.py` | 复杂查询分解 |

**核心思想**：RAG 模式，但不依赖 LlamaIndex 库。

---

### 8.10 MLLM Vision（多模态推理）

| 借鉴点 | WeldEvent 实现 | 文件位置 |
|-------|---------------|---------|
| Hybrid Vision Strategy | `capability/vision.py` | ROUTINE/ADAPTIVE/EXPLORATORY 不同图像策略 |
| Thumbnail + CV Tool | `capability/vision.py` | 低成本 thumbnail + CV tool 获取细节 |

**三种推理模式的图像策略**：

| Reasoning Mode | Image Strategy | Cost | When |
|---------------|----------------|------|------|
| ROUTINE | CV tool only（无图像到 LLM） | 仅文本 tokens | 已知缺陷模式 |
| ADAPTIVE | 低细节 thumbnail（85 tokens）+ CV tool | 低 | 上下文感知 + 工具获取细节 |
| EXPLORATORY | 高细节图像 + CV tool | 高 | 需要视觉推理的新情况 |

**核心思想**：根据推理模式选择不同的图像策略，控制成本。

---

### 8.11 Event Sourcing + CAS（事件溯源 + 乐观锁）

| 借鉴点 | WeldEvent 实现 | 文件位置 |
|-------|---------------|---------|
| Event Sourcing | `adapters/weldmap/event_sourcing.py` | WeldMap 状态变更记录为不可变事件 |
| CAS（Compare-And-Swap） | `adapters/weldmap/event_sourcing.py` | Redis Lua 脚本实现原子 CAS |
| Materialized Views | `adapters/weldmap/event_sourcing.py` | 快速读取 + 事件回放审计 |

**CAS Lua 脚本**：
```python
# 原子写入：检查版本 → 写入数据 → 写入版本 → 记录事件
CAS_WRITE_LUA = """
local current = tonumber(redis.call('GET', key_version) or '0')
if current ~= expected_version then
    return {0, current}  -- 版本冲突
end
redis.call('SET', key_data, new_data)
redis.call('SET', key_version, new_version)
redis.call('XADD', key_events, '*', 'data', event_data)
return {1, new_version}  -- 成功
"""
```

**核心思想**：Event Sourcing 保证审计追踪，CAS 保证并发安全。

---

### 8.12 借鉴总结

| 来源 | 借鉴核心 | WeldEvent 内化 |
|-----|---------|---------------|
| OpenHands | EventLog, Conversation-as-Runtime | `event_log.py`, `react.py` |
| Letta | Block Memory, Dual-Write, Compaction | `blocks.py`, `dual_write.py`, `compaction.py` |
| Cline | ToolPolicy, Checkpoint, Shadow Mode | `tool_policy.py`, `checkpoint.py`, `shadow.py` |
| DeepAgents | Middleware Pattern | `hooks.py` |
| LangGraph | StateGraph, ToolNode, Interrupt | `react.py`, `tool_registry.py`, `hooks.py` |
| DSPy | Signatures | `dspy_signatures.py` |
| Instructor | Pydantic Validation, Retry | `instructor_adapter.py` |
| Guardrails | OnFailAction | `on_fail.py` |
| LlamaIndex | Hybrid Search, Re-ranking | `hybrid_search.py`, `rerank.py` |
| MLLM Vision | Hybrid Vision Strategy | `vision.py` |
| Event Sourcing | CAS, Materialized Views | `event_sourcing.py` |

**关键原则**：
-  借鉴**模式**和**思想**
-  不引入**依赖**
-  内化为 WeldEvent 自有实现

---

## 九、人工审查与中断机制

> Human-Governed 决策模型的核心实现：Agent 建议，Human 决策，System 执行。

### 9.1 Human-Governed 决策模型

```
┌─────────────────────────────────────────────────────┐
│                                                     │
│   Agent ──────▶ Suggest ──────▶ Human              │
│                                     │               │
│                                     ▼               │
│                                  Decide             │
│                                     │               │
│                                     ▼               │
│   System ◀────── Execute ◀───────┘               │
│                                                     │
└─────────────────────────────────────────────────────┘
```

**核心原则**：
- Brain **永远只提供建议**，不能直接执行
- 关键判定**须经人工确认**才能生效
- 这是工业安全的**架构底线**

---

### 9.2 ToolPolicy（工具调用策略）

**职责**：定义哪些 Tool 需要人工审批，哪些可自动执行。

| Tool | auto_approve | 说明 |
|------|-------------|------|
| `search_standards` | `true` | 查询类，低风险 |
| `search_cases` | `true` | 查询类，低风险 |
| `read_weldmap` | `true` | 读取类，低风险 |
| `design_workflow` | `true` | 设计方案，不直接执行 |
| `adjust_parameter` | `false` | **高风险**，需要人工确认 |
| `escalate` | `false` | **高风险**，需要人工确认 |
| `request_confirmation` | `true` | 请求确认本身不需要审批 |

**实现**：
```python
# governance/tool_policy.py
class ToolPolicy:
    rules: dict[str, ToolRule]

    def needs_approval(self, tool_name: str) -> bool:
        return not self.rules.get(tool_name, ToolRule()).auto_approve
```

---

### 9.3 request_confirmation Tool

**职责**：Agent 向 Human 请求确认。

```python
# control/tools/request_confirmation.py
class RequestConfirmationTool:
    name = "request_confirmation"

    async def execute(
        self,
        question: str,
        options: list[str],
        timeout: int = 300
    ) -> ConfirmationResult:
        """
        当需要人工确认时使用。例如：
        - 检测结果异常需要人工判定
        - 参数调整超出安全范围
        - 新缺陷类型需要专家评估
        """
        # 发送通知到 Human
        confirmation = await self._notification_channel.request_confirmation(
            question=question,
            options=options,
            timeout=timeout
        )
        return confirmation
```

---

### 9.4 LangGraph interrupt/resume

**职责**：中断 ReAct 循环，等待人工审批，然后恢复。

```python
# control/react.py
def build_react_graph(tools, checkpointer, interrupts=None):
    builder = StateGraph(BrainReActState)

    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))

    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_edge("tools", "agent")

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupts or [],  # e.g. ["tools"] for approval
    )
```

**流程**：
1. ReAct 循环执行到需要审批的 Tool
2. `interrupt_before=["tools"]` 中断执行
3. 系统发送通知到 Human（WebSocket/NATS）
4. Human 审批后发送 `Command(resume=...)`
5. ReAct 循环恢复执行

---

### 9.5 HumanGateSignal（人工审批信号）

**职责**：L2 Temporal 的人工审批信号。

```
L1 Cognitive Plane                L2 Temporal Control Plane
        │                                    │
        ▼                                    ▼
┌───────────────┐                  ┌─────────────────────┐
│request_confirmation│ ──Signal──▶ │ HumanGateSignal     │
└───────────────┘                  └──────────┬──────────┘
                                            │
                                            ▼
                                   ┌─────────────────────┐
                                   │ TemplateWorkflow    │
                                   │ 等待 HumanGateSignal │
                                   │ (Temporal.await)    │
                                   └─────────────────────┘
```

**Temporal 实现**：
```python
# controlplane/runtime/template_workflow.py
@workflow.defn
class TemplateWorkflow:
    @workflow.run
    async def run(self, template: WorkflowTemplate) -> None:
        for cp in template.control_points:
            # 执行 Activity
            await workflow.execute_activity(...)

            # Human Gate：等待人工审批
            if cp.human_gate:
                await workflow.wait_condition(
                    lambda: self._human_gate_signal_received
                )
```

---

### 9.6 中断恢复流程

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  ReAct 循环                                                 │
│  ┌─────────┐    ┌─────────┐    ┌───────────┐              │
│  │ Thought │───▶│ Action  │───▶│ Interrupt │───┐          │
│  └─────────┘    └─────────┘    └───────────┘   │          │
│       ▲                               │        │          │
│       │                               ▼        │          │
│       │                        ┌─────────────┐ │          │
│       │                        │ Human Gate  │ │          │
│       │                        │  (等待审批)  │ │          │
│       │                        └──────┬──────┘ │          │
│       │                               │        │          │
│       │                               ▼        │          │
│       │                        ┌─────────────┐ │          │
│       │                        │ Resume      │◀┘          │
│       │                        └─────────────┘            │
│       │                               │                   │
│       └───────────────────────────────┘                   │
│                   (恢复执行)                                │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

### 9.7 实时中断（随时打断）

**场景**：Human 随时可以打断正在执行的 ReAct 循环。

| 中断方式 | 实现 |
|---------|------|
| 新用户输入 | 新消息 = 新 ReAct 迭代，自然打断 |
| WebSocket 消息 | `ws.send({"action": "interrupt"})` |
| NATS Signal | `js.publish("weldevent.L1.cognitive.feedback.human")` |

**实现**：
```python
# interaction/api/chat.py
@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    while True:
        msg = await ws.receive_json()

        if msg.get("action") == "interrupt":
            # 打断当前 ReAct 循环
            await orchestrator.interrupt(session_id)
            # 等待 Human 指示
            await ws.send_json({"status": "interrupted", "awaiting": "human_input"})
```

---

### 9.8 审批记录与审计

**职责**：所有人工审批决策记录到 Audit Memory（L5）。

| 记录内容 | 说明 |
|---------|------|
| `decision_id` | 被审批的决策 ID |
| `human_id` | 审批人 ID |
| `action` | APPROVE / REJECT / MODIFY |
| `reason` | 审批理由（可选） |
| `timestamp` | 审批时间 |

**存储**：
```python
# adapters/database/audit_repo.py
class AuditRepo:
    async def record_approval(
        self,
        decision_id: DecisionId,
        human_id: str,
        action: ApprovalAction,
        reason: str | None
    ) -> None:
        # 写入 L5 Audit Memory
        ...
```

---

### 9.9 总结

| 机制 | 文件位置 | 说明 |
|-----|---------|------|
| ToolPolicy | `governance/tool_policy.py` | 定义哪些 Tool 需要人工审批 |
| request_confirmation | `control/tools/request_confirmation.py` | Agent 向 Human 请求确认 |
| LangGraph interrupt | `control/react.py` | 中断 ReAct 循环等待审批 |
| HumanGateSignal | L2 Temporal | Temporal 工作流等待人工审批 |
| 实时中断 | `interaction/api/chat.py` | WebSocket/NATS 随时打断 |
| Audit 记录 | `adapters/database/audit_repo.py` | 审批决策记录到 L5 |

---

## 十、关键设计决策

| 决策 | 选择 | 理由 |
|-----|------|------|
| 决策引擎 | ReAct 循环 | 统一 Thought/Action/Observation，替代 5 个独立 Mode |
| 写出通道 | CognitiveGateway 单一通道 | 强制状态一致性，防止 Brain 直接修改 WeldMap |
| 记忆管理 | L0-L5 分层 | 从实时到审计的完整记忆层次 |
| 人机协作 | Human-Governed 模型 | Agent 建议，Human 决策，System 执行 |
| 工具调用 | Hook + Policy | ALLOW/DENY 钩子，支持人工确认 |
| 状态恢复 | Checkpoint + EventLog | 支持决策回滚和审计 |

---

## 十一、技术栈

| 层级 | 技术 | 用途 |
|-----|------|------|
| L1 | LangGraph | ReAct 循环、状态机 |
| L1 | DSPy | Prompt 工程 |
| L1 | Instructor | 格式验证 |
| L2 | Temporal | 工作流编排 |
| L5 | PostgreSQL | 持久化 |
| L5 | Milvus | 向量搜索 |
| L5 | Redis | 缓存、Event Sourcing |
| L6 | NATS JetStream | 事件总线 |
| L6 | MinIO | 对象存储 |
| Observability | Langfuse | LLM 可观测性 |

---

## 十二、总结

**核心思想**：
1. **7-Plane 分层** — 清晰的关注点分离
2. **ReAct 统一模型** — 简化决策引擎
3. **Gateway 单一写出** — 强制状态一致性
4. **Human-Governed** — 人机协作安全模型
5. **Memory L0-L5** — 完整记忆层次
6. **Bridge L1→L2** — 事件驱动集成


架构代码细节
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
│   ├── multimodal.py             # Multi-modal input parser (text/image/annotation)
│   ├── entities/                 # Image entity etc.
│   ├── messages/                 # Message types (progress, log, zoom, etc.)
│   └── signals/                  # Event bus, instruction signals
│
├── governance/                   # Governance Plane — 审批/验证/升级
│   ├── __init__.py
│   ├── ports.py                  # ValidationPipelinePort, HumanReviewRepository, ApprovalServicePort
│   ├── approval.py               # ApprovalService
│   ├── tool_policy.py            # ToolPolicy
│   ├── escalation.py             # EscalationTracker (per-case isolation)
│   ├── review.py                 # HumanReview workflow
│   ├── permissions.py            # RBAC (Phase2: OPA)
│   ├── pipeline.py               # 4-stage ValidationPipeline (Safety→Rule→Shadow→Consistency)
│   ├── on_fail.py                # OnFailAction (REASK/FIX/FILTER/REFRAIN/ESCALATE)
│   ├── exceptions.py
│   ├── validators/
│   │   ├── safety.py
│   │   ├── rule.py
│   │   ├── shadow.py
│   │   └── consistency.py
│   ├── collaboration/            # Human collaboration adapters
│   └── repositories/             # In-memory review repos
│
├── control/                      # Control Plane — BrainCore决策引擎
│   ├── __init__.py
│   ├── ports.py                  # BrainDecisionRepository, ReasoningPort, PlanningPort, etc.
│   ├── react.py                  # ReAct Engine — 3-tier (Function Calling/Structured/Embedding+Rules)
│   ├── orchestrator.py           # BrainOrchestrator (typed injection via CognitiveDependencies)
│   ├── tool_registry.py          # Tool Registry — Brain可调用的全部Tool定义
│   ├── tools/                    # Tool implementations (10 tools)
│   │   ├── search_standards.py
│   │   ├── search_cases.py
│   │   ├── search_process.py
│   │   ├── read_weldmap.py
│   │   ├── design_workflow.py
│   │   ├── adjust_parameter.py
│   │   ├── request_confirmation.py
│   │   ├── explain_decision.py
│   │   ├── escalate.py
│   │   └── archive_memory.py
│   ├── hooks.py                  # BeforeToolHook — ALLOW/DENY
│   ├── event_log.py              # Append-only EventLog
│   ├── checkpoint.py             # Decision checkpoint/rollback
│   ├── state_machine.py          # BrainStateMachine (12 states)
│   ├── persona.py                # PersonaSelector (Planner/Copilot/CAA)
│   ├── reasoning_mode.py         # ReasoningModeSelector
│   ├── planner.py                # Self-built Planner
│   ├── reflector.py              # Self-built Reflector
│   ├── sub_agent.py              # Sub-Agent delegation
│   ├── supervisor.py             # SupervisorCenter
│   ├── decision_factory.py       # DecisionFactory
│   ├── fallback.py               # 3-tier fallback strategy
│   ├── deps.py                   # CognitiveDependencies typed DI
│   ├── exceptions.py             # Control-specific exceptions
│   ├── repositories/             # InMemoryBrainDecisionRepository
│   └── adapters/                 # MockDeepAgentsAdapter (legacy)
│
├── gateway/                      # CognitiveGateway — 认知关口
│   ├── __init__.py
│   ├── ports.py                  # CognitiveGatewayWritePort, CognitiveGatewayReadPort
│   ├── write_gateway.py          # Decision/Escalation/Instruction → WeldMap
│   ├── read_gateway.py           # WeldMap查询
│   ├── events.py                 # ActionEvent/ObservationEvent pair
│   ├── weldmap_client.py         # WeldMap HTTP client adapter
│   ├── adapters/                 # InMemoryGatewayAdapter
│   └── services/                 # Gateway services
│
├── knowledge/                    # Knowledge Plane — RAG知识检索
│   ├── __init__.py
│   ├── ports.py                  # RAGQueryPort, StandardsQueryPort, etc.
│   ├── rag.py
│   ├── standards.py
│   ├── cases.py
│   ├── process.py
│   ├── equipment.py
│   ├── adapters/                 # StubKnowledgeAdapter
│   └── repositories/             # In-memory knowledge repos
│
├── memory/                       # Memory Plane — 工业记忆L0-L5
│   ├── __init__.py
│   ├── ports.py                  # MemorySearchPort, MemoryReadPort, etc.
│   ├── hierarchy.py              # L0-L5 level definition
│   ├── blocks.py                 # Block-based working memory
│   ├── search.py                 # Multi-level search (hybrid: vector + FTS + RRF)
│   ├── promotion.py              # Memory promotion (RAW→VALIDATED→PROMOTED)
│   ├── compaction.py             # Context compaction
│   ├── dual_write.py             # Dual-write (PG always, vector best-effort)
│   ├── archive.py                # Archival storage
│   ├── confidence.py             # Real confidence scoring
│   ├── learning/                 # Learning module
│   ├── adapters/                 # Port adapters
│   ├── repositories/             # InMemoryMemoryRepository
│   └── services/                 # Memory services
│
├── capability/                   # LLM Capability — 大模型推理能力
│   ├── __init__.py
│   ├── ports.py                  # LLMProvider ABC
│   ├── provider.py               # LLMRequest/LLMResponse models
│   ├── deepseek.py               # DeepSeek adapter
│   ├── openai_compat.py          # OpenAI-compatible adapter
│   ├── openai_provider.py        # OpenAI/DeepSeek provider 实现
│   ├── mock.py                   # Mock LLM (canonical implementation)
│   ├── mock_provider.py          # Legacy re-export shim → mock.py
│   ├── config.py                 # LLMConfig, OpenAIConfig
│   ├── tracking.py               # LLMCallTracker (Phase 2 → Langfuse)
│   └── prompts/                  # Prompt templates
│
├── copilot/                      # Industrial Copilot (B-level)
│   ├── __init__.py
│   ├── qa.py                     # Standard/process/case Q&A
│   ├── explain.py                # Decision explanation
│   ├── investigate.py            # Anomaly root cause analysis
│   ├── govern.py                 # Approval/compliance assistance
│   └── operate.py                # Production line operation guidance
│
├── bridge/                       # L1→L2 集成
│   ├── __init__.py
│   ├── event_connector.py        # CognitiveGateway → DecisionTranslator
│   ├── decision_translator.py    # BrainDecision → WorkflowTemplate
│   └── workflow_launcher.py      # Template → Temporal
│
├── adapters/                     # L5/L6适配器
│   ├── __init__.py
│   ├── database/                 # PostgreSQL adapters (7 repos)
│   │   ├── engine.py
│   │   ├── models.py             # SQLAlchemy ORM models (6 tables)
│   │   ├── decision_repo.py
│   │   ├── memory_repo.py
│   │   ├── knowledge_repo.py
│   │   ├── workflow_repo.py
│   │   ├── review_repo.py
│   │   └── audit_repo.py
│   ├── cache/
│   │   └── redis_client.py       # Redis L0/L1 cache
│   ├── storage/
│   │   └── minio_client.py       # MinIO image/report storage
│   ├── observability/
│   │   ├── tracing.py            # OpenTelemetry
│   │   └── metrics.py
│   ├── weldmap/
│   │   ├── weldmap_http.py       # WeldMap HTTP client
│   │   └── event_sourcing.py     # CAS Lua + materialized views
│   └── migrations/               # Alembic migrations
│
└── shared/                       # 共享内核（最小化，仅L1内部共享）
    ├── __init__.py
    ├── types.py                  # ID types
    ├── enums.py                  # Cross-Plane enums
    ├── events.py                 # Event re-exports
    ├── dto/                      # Cross-Plane DTOs（规范路径）
    │   ├── context.py            # ContextSnapshot, DomainEvent, WeldMapSnapshot
    │   ├── decision.py           # Re-export from dto_decision/ subpackage
    │   ├── memory.py             # MemorySearchQuery, MemoryRecord
    │   ├── knowledge.py          # RAGQuery, KnowledgeResult
    │   ├── validation.py         # ValidationResult
    │   ├── escalation.py         # Escalation
    │   ├── gateway.py            # PublishResult, WorkflowState, etc.
    │   ├── collaboration.py      # HumanReviewRequest
    │   ├── learning.py           # LearningContent, LearningEvent
    │   ├── deepagents.py         # Conclusion, Plan, Strategy (待迁移入 control/)
    │   ├── persona.py            # PersonaFrame
    │   ├── reasoning_mode.py     # ReasoningModeSelectionInput/Result
    │   └── weldmap_events.py     # WeldMapEventType, WeldMapDomainEvent
    ├── dto_context.py            # Legacy shim → dto.context
    ├── dto_knowledge.py          # Legacy shim → dto.knowledge
    ├── ...（其余 dto_*.py 均为 legacy shim）
    └── dto_decision/             # BrainDecision 子包
        ├── __init__.py
        ├── decision.py
        ├── outputs.py            # 15 种 DecisionOutputContent
        ├── recommendation.py     # WorkflowRecommendation, ParameterRecommendation
        ├── assessment.py         # RiskAssessment
        └── directive.py          # InvestigationDirective
```