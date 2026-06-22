# CognitivePlane 架构方案

---

## 术语表（先读这节，再读全文）

文档引入了大量自定义术语，集中定义如下，便于通读时查阅。

### 分层与平面

| 术语 | 定义 |
|---|---|
| **L1 Cognitive Plane** | 认知平面，本方案的主体。LLM 在此自主决策 |
| **L2 Temporal Control Plane** | 工作流编排平面，阶段 5+ 引入。规则驱动、可重放 |
| **L4 工具层** | Tool / MCP / Skill 三类工具的统称，L1 LLM 和 L3 Activity 共享调用 |
| **L5 Data Plane** | 数据平面（WeldMap + PG + Redis + MinIO），single source of truth |
| **L6 Event Bus** | 事件总线（NATS JetStream），阶段 5+ 引入 |
| **7-Plane** | Interaction / Governance / Control / Gateway / Knowledge / Memory / Capability 七个职责平面 |

### LLM 与工具分级

| 术语 | 定义 |
|---|---|
| **LLMTier** | LLM 模型能力分级（1/2/3），启动时选定，不在运行时切换。Tier-1=function calling，Tier-2=structured output，Tier-3=keyword rules |
| **ToolReliability** | 工具可靠性分级（A/B/C），工具属性。Tier-A=可重试，Tier-B=必须精确，Tier-C=单次任务运行时降级 |
| **Tier-C vs LLMTier-3** | Tier-C 是 LLMTier-1/2 系统运行中临时降级（下个任务恢复）；LLMTier-3 是启动时永久选定（无 LLM 可用）|
| **Persona** | 推理深度策略（Planner/Copilot/CAA），架构根据任务自动选，非 LLM 选 |
| **ReasoningMode** | 成本控制策略（ROUTINE/ADAPTIVE/EXPLORATORY），架构根据 Memory 命中率自动选 |
| **AgentRole** | 角色（Explorer/Vision/Quality/Operator），LLM 通过 switch_role 工具自主切换 |

### Memory 与状态

| 术语 | 定义 |
|---|---|
| **Memory M0-M5** | 记忆层级（为避免与系统分层 L0-L5 冲突，Memory 层级统一用 M 前缀）。M0 RAW → M1 VALIDATED → M2 PROMOTED → M4 Knowledge，逐级晋升 |
| **Block** | Memory 的结构化单元（label/value/limit/tags），借鉴 Letta |
| **promotion_status** | Memory 条目的晋升状态：RAW / VALIDATED / PROMOTED |
| **correction vs validation** | correction=用户改 LLM 标注（source=operator_X，不进全局）；validation=用户确认 LLM 标注（直接 VALIDATED）|
| **BrainState** | 状态机的状态（12 态），观察 LLM 行为用，不约束 LLM |
| **EventLog** | 不可变事件日志，append-only，审计追溯用 |

### 工具与安全

| 术语 | 定义 |
|---|---|
| **BrainTool** | 工具的 ABC 基类，所有 Tool/MCP/Skill 实现 |
| **ToolRegistry** | 工具注册中心，LLM 通过它发现可调工具 |
| **MCP** | Model Context Protocol，外部工具接入协议（JSON-RPC 2.0）。不是 LLM 专属，L3 Activity 也可调 |
| **Skill** | 业务流程模板，多个 Tool/MCP 的预设组合，可选使用 |
| **Hook** | 工具调用前的拦截器（SafetyHook / PolicyHook），ALLOW/DENY 二元 |
| **ToolPolicy** | 工具安全等级规则（auto_approve / require_reason / max_calls_per_session）|
| **Capability Provider vs Tool** | Provider 是能力实现（Capability 层，如 WebSearchProvider）；Tool 是工具入口（Control 层，如 WebSearchTool）。Tool 调用 Provider |
| **Tool 协议层 vs Tool 实现层** | 协议层 = `control/tools/` 里 Tool 类的 BrainTool ABC + 函数签名（LLM 看到的接口）；实现层 = Tool 内部委托的 Provider/Adapter/MCP Client，按依赖分布到 Capability/Gateway/Adapters 各层 |

### 执行与协作

| 术语 | 定义 |
|---|---|
| **ReAct Loop** | LLM 自主循环：思考→调工具→观察→再思考。本方案唯一核心循环 |
| **AgentLoop** | ReAct Loop 的运行时容器，主循环 + feedback consumer 双 task |
| **Copilot** | ReAct 的预设入口（不是独立链路），5 个端点 = /chat + 预设角色+工具白名单 |
| **CognitiveDependencies** | 6 组 typed 依赖容器（Capability/Control/Knowledge/Memory/Gateway/Governance）|
| **WeldMap** | L5 数据平面的核心，single source of truth，Brain 唯一读写外部接口 |
| **SupervisorCenter** | ReAct Loop 的旁路监控器，检测循环/超时/健康，不参与决策 |
| **ContextCompactor** | 上下文压缩器，按语义密度分级压缩，不按时间 |

### 阶段与演进

| 术语 | 定义 |
|---|---|
| **系统阶段 0-6** | §十一 执行路线的整体能力演进阶段。0=LLM 参与决策，1=能搜索，2=能看图，3=反馈+MCP，4=反馈学习，5=多角色，6=知识库 |
| **角色阶段 1/2/3** | §3.4 单个角色工具白名单的扩充节奏，与系统阶段不同概念 |
| **阶段引入** | 每个组件标注"阶段 N 引入"，避免一开始全建出来 |

---

## 零、核心哲学

**LLM 决定做什么，架构决定不能做什么。**

系统不是流水线。系统是 LLM 的能力基底——提供工具、提供记忆、提供安全边界，然后让 LLM 自主决定每一步做什么。

```
❌ 旧思路: 人设计流水线，LLM 填空
   输入 → 步骤1(知识检索) → 步骤2(推理) → 步骤3(验证) → 步骤4(发布) → 输出
   LLM 是流水线的一个环节

✅ 新思路: LLM 自主思考，架构是基底
   LLM 收到任务 → LLM 自己规划怎么做 → LLM 自己决定用什么工具 → LLM 自己决定何时结束
   架构提供: 工具 + 记忆 + 安全边界
   架构不提供: 执行顺序、步骤规划、角色切换顺序
```

**固定 vs 动态：**

| 固定（架构决定） | 动态（LLM 决定） |
|:---:|:---:|
| 哪些工具可用 | 调哪个工具、什么时候调 |
| 安全边界（什么操作需审批） | 如何分析、如何推理 |
| 记忆如何存储/检索 | 什么时候查记忆、查什么 |
| 消息如何传递 | 回复什么内容、什么格式 |
| Hook 拦截规则 | 被拒绝后怎么调整策略 |

### 0.1 LLM 自主的边界 — 架构管什么，LLM 管什么

"LLM 自主"不等于"LLM 什么都自己定"。架构必须管一些事，否则系统跑不起来。但架构管的事分两种性质，不能混着说：

#### 一类：架构本来就该管的事（不是抢 LLM 的活）

这些事是架构的职责，让 LLM 来做反而出问题。长期如此，不会变。

| 架构管的事 | 为什么不能交给 LLM |
|---|---|
| ReasoningMode（成本控制）选择 | 成本控制是架构职责。让 LLM 自己选会倾向最贵的，账单失控 |
| DecisionOutput 类型归类 | 下游 API 要结构化 schema，这是系统契约，不是 LLM 的判断范围 |
| 启动上下文注入（CLAUDE.md 模式）| 企业语境（"我们公司焊接标准是 GB/T xxx"）本来就该架构提供，LLM 自己猜不到 |

#### 另一类：LLM 暂时不会，架构先代劳（以后还给 LLM）

这些事理想状态下应该 LLM 自己做，但现在的 LLM 还做不好，所以架构先替它做。等系统长到一定阶段，还给 LLM。

| 架构先代劳的事 | LLM 暂时做不好的原因 | 什么时候还给 LLM |
|---|---|---|
| Persona（推理深度）选择 | 任务特征匹配（Planner/Copilot/CAA）是架构职责，不是 LLM 能力盲区 | 永久由架构选（与 AgentRole 正交，见 §A.2）|
| Memory 自动注入 prompt | LLM 还没学会主动查什么记忆，架构先自动注入 | 阶段 5+：**LLM readiness 达标后**移除自动注入（见下） |

#### 阶段 5 移除自动注入的 readiness 标准

"阶段 5+ 还给 LLM"不是时间触发，是**能力达标触发**。LLM 必须先证明自己会主动查 Memory，架构才能撤掉自动注入——否则撤了就是盲目降级。

**移除自动注入的硬指标**（阶段 4 末期连续 2 周观测）：

| 指标 | 阈值 | 测量方式 |
|---|---|---|
| ADAPTIVE 模式下 LLM 主动调 `search_memory` 比例 | ≥ 70% | EventLog 统计：ADAPTIVE 任务中含 `search_memory` tool_call 的比例 |
| LLM 主动查询的 Memory 命中率（查到的条目相关性）| ≥ 60% | 查询后 LLM 是否在后续推理中引用（EventLog 中 search_memory → think 内容关联）|
| 移除自动注入后回归测试通过率 | 100% | A/B 测试：同一任务集跑"有自动注入"vs"无自动注入"，结果质量不下降 |

**未达标时**：阶段 5 不移除自动注入。继续观测，每 2 周复测一次。达标后才进入阶段 5。

**部分达标选项**：如果只有第 1 项达标（≥70%）但第 2 项未达标，可降级为"**仅 high-confidence 任务移除自动注入**"——低置信任务仍保留自动注入作为 fallback。这是渐进过渡，不是全有全无。

#### 两条规则

1. **架构替 LLM 做的看不见的事（Persona、Memory 注入），必须记进 EventLog**——LLM 下一轮能查到"上一轮被注入了什么"，否则等于架构在背后偷偷影响 LLM 决策
2. **架构永远不替 LLM 决定调哪个工具、调几次、按什么顺序**——这是 LLM 的内核，谁都不能动

简言之：**"LLM 在工具语义和成本约束内自主"**——比"LLM 全自主"更准确。架构管"约束"（成本、契约、企业语境），LLM 管"工具调用"（调什么、怎么调）。

---

## 一、整体架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                       前端 (React / Vue)                             │
│              WebSocket 实时通信 · REST API                          │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    L1 Cognitive Plane                                │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  入口层: Interaction + Copilot                                │  │
│  │                                                               │  │
│  │  Interaction (通用入口)         Copilot (预设入口)            │  │
│  │  /api/v1/chat · WebSocket      QA·Explain·Compliance·         │  │
│  │  会话管理 · 文件处理           Investigate·Operate             │  │
│  │                                (每个 = /chat + 预设 role+工具) │  │
│  └────────────────────────────┬──────────────────────────────────┘  │
│                               │                                     │
│                               ▼                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Governance (横切层 — 包裹 Control)                           │  │
│  │  Hook 拦截 (Safety/Policy) · ToolPolicy                       │  │
│  │  ↓ 每次工具调用都过这层，不是 LLM 主动调                      │  │
│  │  注: 验证管道(ValidationPipeline)不在 Governance 横切层       │  │
│  │      ——它只在决策写出时触发，归 Gateway 写出路径(见 §5.4)     │  │
│  │  ┌─────────────────────────────────────────────────────────┐  │  │
│  │  │              Control 控制层                             │  │  │
│  │  │                                                         │  │  │
│  │  │    ┌───────────────────────────────────────────┐        │  │  │
│  │  │    │       ReAct Loop (LLM 自主循环)            │        │  │  │
│  │  │    │    思考→调工具→[Hook检查]→执行→观察→       │        │  │  │
│  │  │    │    再思考→...→完成                         │        │  │  │
│  │  │    │    没有固定步骤 · LLM 全权决策             │        │  │  │
│  │  │    └───────────────────────────────────────────┘        │  │  │
│  │  └─────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                               │                                     │
│              Control 调用以下三类依赖                                │
│      ┌────────────────┬────────┴───────┬────────────────┐          │
│      ▼                ▼                ▼                ▼          │
│  ┌─────────┐    ┌──────────┐    ┌───────────┐    ┌──────────┐     │
│  │ Tools   │    │ Memory   │    │Capability │    │ Knowledge│     │
│  │ 工具集  │    │ 记忆     │    │ LLM/Vision│    │ 知识库   │     │
│  └────┬───┘    └──────────┘    └───────────┘    └──────────┘     │
│       │ 工具调用后端                                                │
│       ▼                                                            │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Gateway (WeldMap 关口层)                                   │   │
│  │  read_weldmap / write / search 的后端                       │   │
│  └─────────────────────────────────────────────────────────────┘   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       Adapters 适配层                               │
│   PostgreSQL │ Redis │ MinIO │ WeldMap HTTP │ DuckDuckGo │ Tavily  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     L2 Temporal Control Plane                       │
└─────────────────────────────────────────────────────────────────────┘
```

**层级关系说明**（图里画不下的语义）：

| 关系 | 说明 |
|---|---|
| 前端 → 入口层 | 唯一入口。Interaction 是通用 `/chat`，Copilot 是预设入口（preset role + 工具白名单），两者**并列**，不嵌套 |
| 入口层 → Control | 入口层只负责接收消息，调用 Control 的 ReAct Loop |
| Governance 包裹 Control | Governance 是**横切层**，不是平行节点——每次工具调用自动过 Hook，LLM 不主动调 Safety。验证管道不在 Governance（它只在决策写出时触发，归 Gateway，见 §5.4）|
| Control 调用 Tools/Memory/Capability/Knowledge | 四者都是 Control 的依赖，**同级**，不是 Control 的下游 |
| Tools 调用 Knowledge/Gateway | 知识库是 `search_standards` 等内部工具的后端；Gateway 是 `read_weldmap` 等外部工具的后端 |
| Tools 框（图中的"工具集"）| 仅画 **Tool 协议层 + 认知业务工具**（`control/tools/`）。外部能力工具（MCP）实现在 L4 `adapters/mcp/`，不在本图——通过 `mcp_registry.py` 注册到 ToolRegistry 后 LLM 看到的就是普通工具（详见 §13）|
| Capability 只放 LLM Provider | DeepSeek Chat / Volc Vision 是 LLM 能力，**web_search 不是 Capability——它是 Tool**（详见 §13 工具三层体系）|
| Adapters 接所有层 | PG/Redis/MinIO/WeldMap HTTP 是基础设施，被上面所有层调用 |
| L2 Temporal | 当前阶段不接入，未来规则系统引入时通过 Bridge（§5.9）连 |

### 1.1 两套范式混合 — 当前 LLM 系统 vs 未来规则系统

本方案不是"一套 LLM 系统"，而是**两套范式的协作**。这点必须在架构层明说，否则会把"LLM 自主"硬套到不该套的地方。

```
┌───────────────────────────────────────────────────────────────┐
│  当前阶段 (阶段 0-6 全部): LLM 系统 (ReAct Loop)                │
│  ─────────────────────────────────────                        │
│  执行体: ReActEngine + Tools + Memory                        │
│  特征: 不确定、自适应、对话/分析驱动                          │
│  典型: 标注建议、缺陷判定、动态工作流设计                     │
│  目前所有功能都走这条路                                       │
│                                                               │
│  未来阶段 (L2 数据平面就绪后): 规则系统 (Rule-Based Workflow)  │
│  ─────────────────────────────────────                        │
│  执行体: Temporal Workflow + 规则引擎                         │
│  特征: 确定性、可重放、人工流程数字化                         │
│  典型: 工单流转、签字审批、阈值触发的工艺单生成                │
│  LLM 不在主路径——只在 Copilot 副驾驶被人主动召唤             │
│  规则系统不在 LLM 主路径——只作为 LLM 失效时的兜底降级         │
└───────────────────────────────────────────────────────────────┘
```

**衔接点**（两套范式如何协作）：

1. **规则系统 → LLM**: 规则系统的某一步可以调用 `request_llm_advice(context)` 工具，把控制权交给 LLM ReAct Loop，拿到结果后回到 Workflow 继续
2. **LLM → 规则系统**: LLM 的 `design_workflow` 工具产生的工作流交给 Temporal 执行——LLM 设计、规则系统执行
3. **降级**: LLM 持续失败（§3.6 Tier-C）时，控制权回到规则系统的"保守预案"，不是无限重试 LLM

**为什么不统一**：尝试用 LLM 替代规则系统（如"让 LLM 决定签字流程"）会引入不可审计、不可重放的风险，违反工业场景的合规性。两套范式各司其职，比"一套通吃"更稳健。

**为什么不现在就引入规则系统**：规则系统依赖 L2 数据平面（WeldMap 全量事件溯源）落地，那是 Phase 2 之后的事。当前阶段所有决策路径都走 ReAct，规则系统的接口（`request_llm_advice`、`design_workflow` 产物格式）已在工具层预留，等 L2 就绪直接接入。

---

## 二、ReAct Loop — LLM 自主循环

### 2.1 核心循环

系统唯一的核心流程是 ReAct Loop。没有 14 步管线，没有固定角色顺序。

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐                 │
│  │  Think   │───▶│   Act    │───▶│ Observe  │──┐              │
│  │ LLM思考  │    │ 调用工具 │    │ 观察结果 │  │              │
│  └──────────┘    └──────────┘    └──────────┘  │              │
│       ▲                                         │              │
│       │                                         │              │
│       └────────────── LLM 决定继续或结束 ────────┘              │
│                                                                 │
│  LLM 全权决策:                                                  │
│  - 调什么工具?        → LLM 决定                                │
│  - 调几次?            → LLM 决定                                │
│  - 先查知识还是先看图? → LLM 决定                                │
│  - 要不要搜索标准?    → LLM 决定                                │
│  - 分析完要不要验证?  → LLM 决定                                │
│  - 什么时候结束?      → LLM 决定                                │
│                                                                 │
│  架构只提供:                                                    │
│  - 工具集 (LLM 可以调什么)                                      │
│  - 安全边界 (LLM 不能做什么)                                    │
│  - 上下文管理 (LLM 能看到什么)                                  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 LLMTier — 按模型能力分级 (启动时选定)

> **术语澄清**: 本节的 Tier 1/2/3 是 **LLM 模型能力分级 (LLMTier)**，启动时根据配置的 LLM 选定，不在会话中切换。这与 §3.6 的 **工具可靠性分级 (ToolReliability Tier-A/B/C)** 是两个独立维度——前者描述 LLM 用什么协议调工具，后者描述工具调用失败时怎么保障。两套 Tier 不要混用。

```
┌──────────────────────────────────────────────────────────┐
│                                                          │
│  LLMTier-1: Function Calling (主路径)                    │
│  支持 function calling 的现代模型 (DeepSeek, GPT-4, ...) │
│  LLM 完全自主: 思考 → 调工具 → 观察 → 再思考            │
│  schema 校验失败 → retry 一次                            │
│  质量: 最高                                              │
│                                                          │
│  LLMTier-2: Structured Output (兼容路径)                 │
│  适用场景: 模型不支持 function calling 但支持 JSON 模式  │
│  LLM 输出 {"intent":..., "tool":..., "args":...}        │
│  规则派发到工具                                          │
│  质量: 中等 (失去多步推理能力)                           │
│                                                          │
│  LLMTier-3: Keyword Rules (兜底路径)                     │
│  适用场景: LLM 完全不可用 / 配置为纯规则模式            │
│  关键词匹配 → 规则引擎 → 固定工具调用                   │
│  质量: 基础，但保证系统不瘫痪                            │
│                                                          │
│  关键: LLMTier 是启动时按配置的 LLM 能力选定的，          │
│  整个会话生命周期不切换。运行时的"异常兜底"是           │
│  另一套机制 (§3.6 ToolReliability)，不要混淆。           │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### 2.3 多模态输入 — 消息层与工具层的双轨

理论上 LLM 是多模态的。**工程现实是 function calling 参数有大小限制，base64 图片塞不进 tool_calls。** 因此图片走双轨：

```
消息层 (LLM 能直接看到图片):
  {"role": "user", "content": [
    {"type": "text", "text": "这张焊缝有什么缺陷？"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
  ]}
  → 用于: 用户上传图片时，图片本体进入对话上下文

工具层 (tool 参数只传引用):
  {"name": "analyze_image", "arguments":
   {"image_ref": "PENDING:session_id:0"}}
  → 用于: LLM 调工具操作图片时，只传图片 ID/索引
  → SessionStore 解析 PENDING:xxx → 取回真正的图片数据

为什么需要双轨:
  - function calling 的 tool_calls 字段有 token 限制 (通常 8K-32K)
  - 一张焊缝图 base64 后约 200KB-1MB，远超限制
  - 即使能塞进，重复在 tool_calls 中传递图片浪费 token

LLM 视角:
  - 在多模态消息里直接"看"图片
  - 在 tool 调用里通过 image_ref 引用图片
  - 不需要理解 base64，只需要传引用 ID

这不是"特殊路径"违反"LLM 自主"哲学
这是 function calling 协议的物理限制
LLM 仍然自主决定要不要看图、看几次、用什么工具处理
```

---

## 三、工具集 — LLM 的能力

工具是 LLM 可以调用的能力，不是流水线的步骤。LLM 自己决定什么时候调、调哪个、调几次。

### 3.1 工具清单

```
┌──────────────────────────────────────────────────────────────────┐
│                        工具集                                    │
│                                                                  │
│  信息获取类                                                      │
│  ├── web_search          搜索互联网 (DuckDuckGo/Tavily)          │
│  ├── search_standards    查询焊接标准 (NB/T47014 等)              │
│  ├── search_cases        查询历史案例                             │
│  ├── search_process      查询工艺知识                             │
│  ├── read_weldmap        读取 WeldMap 状态                        │
│  └── explain_decision    解释决策                                 │
│                                                                  │
│  操作执行类 (需安全审查)                                         │
│  ├── adjust_parameter    调整检测参数 (高风险)                     │
│  ├── design_workflow     设计/更新检测方案                         │
│  ├── request_confirmation  请求人工确认                           │
│  ├── escalate            升级到人工                               │
│  └── archive_memory      记忆归档                                 │
│                                                                  │
│  MCP 外部工具类 (动态注册)                                       │
│  ├── detect_defects      目标检测模型 (检测模型 API)               │
│  ├── annotate_label      创建标注 (Label Studio)                  │
│  ├── read_annotations    读取标注结果 (Label Studio)              │
│  ├── update_annotation   修改标注 (Label Studio)                  │
│  └── ... (更多 MCP 工具按需接入)                                  │
│                                                                  │
│  自我管理类 (LLM 自主决定何时使用)                                │
│  ├── switch_role         切换角色 (改变 prompt + 工具白名单)      │
│  ├── manage_plan         任务清单管理 (LLM 自主决定要不要拆任务)   │
│  └── spawn_investigator  派生子调查员 (并行隔离上下文调查)         │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 LLM 如何使用工具 — 完全自主

同一个问题，LLM 可能选择完全不同的策略：

```
场景: "分析这张焊缝图片"

LLM 策略 A (保守):
  Think: "我先搜索相关标准，确定验收要求"
  Act: search_standards("焊缝缺陷验收标准 NB/T47014")
  Observe: [夹渣≤t/3, 气孔≤...]
  Think: "好，现在看图片"
  Act: (直接看图，多模态)
  Think: "图片显示椭圆形暗区，边缘光滑，对照标准判定为气孔"

LLM 策略 B (直接):
  Think: "我直接看图分析"
  Act: (直接看图，多模态)
  Think: "看到缺陷，但不确定类型，搜一下类似案例"
  Act: search_cases("椭圆形暗区 焊缝缺陷")
  Think: "类似案例显示是气孔"

LLM 策略 C (研究型):
  Think: "先在网上搜索最新的检测方法"
  Act: web_search("焊缝气孔检测方法 2026")
  Think: "了解了最新方法，现在看图"
  Act: (直接看图)
  Think: "根据最新方法和图片，判定为气孔"

三种策略都是对的。架构不规定用哪种。
```

### 3.3 MCP 工具 — LLM 调用外部系统

LLM 不仅能调内部工具，还能通过 MCP (Model Context Protocol) 接入外部系统。

```
┌──────────────────────────────────────────────────────────────────┐
│                    MCP 工具接入层                                 │
│                                                                  │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────┐ │
│  │ 标注工具        │    │ 检测模型        │    │ 参数数据库  │ │
│  │ (Label Studio)  │    │ (目标检测API)   │    │ (WeldMap)   │ │
│  └────────┬────────┘    └────────┬────────┘    └──────┬──────┘ │
│           │                      │                     │        │
│           ▼                      ▼                     ▼        │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │                   MCP 协议层                                │ │
│  │   tool_discovery → LLM 看到可用工具                        │ │
│  │   tool_call → MCP 转发到外部系统                           │ │
│  │   tool_result → 外部系统返回结构化结果                      │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                              │                                   │
│                              ▼                                   │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │                   ReAct Loop                                │ │
│  │   LLM 自主决定调哪个 MCP 工具、什么时候调                  │ │
│  │   MCP 工具和内部工具地位平等，LLM 统一选择                 │ │
│  └──────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

**MCP 工具的发现与注册**：

```
外部系统接入流程:

1. 注册: 外部系统通过 MCP 协议声明自己的能力
   → tool_name, description, parameters_schema

2. 发现: ToolRegistry 自动加载 MCP 工具
   → LLM 在 ReAct Loop 中看到这些工具

3. 调用: LLM 自主决定调哪个 MCP 工具
   → MCP 转发调用到外部系统
   → 外部系统返回结果
   → 结果进入 LLM 的观察，LLM 继续推理

4. 治理: MCP 工具同样受 Hook 拦截和 ToolPolicy 约束
```

**MCP 工具示例**：

| MCP 工具 | 外部系统 | 用途 |
|---------|---------|------|
| `annotate_label` | Label Studio | 在图片上创建/修改标注框 |
| `detect_defects` | 检测模型 API | 对图片运行目标检测 |
| `read_annotations` | Label Studio | 读取当前标注结果 |
| `update_annotation` | Label Studio | 修改某个标注的类别/位置 |

LLM 自主决定怎么组合这些工具——先检测后标注？先标注后检测？边标注边检测？架构不规定。

### 3.4 角色工具 — LLM 自主切换

```
角色不是流水线阶段，是 LLM 可以自主切换的工作模式。

┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ Explorer │  │ Vision   │  │ Quality  │  │ Operator │
│ 探索者   │  │ 视觉专家 │  │ 质量专家 │  │ 操作专家 │
└──────────┘  └──────────┘  └──────────┘  └──────────┘

角色 = system prompt + 工具白名单

LLM 通过 switch_role("vision") 工具调用切换角色
→ 架构替换 system prompt
→ 架构过滤可用工具
→ LLM 继续在新角色下自主思考

LLM 不必须按 Explorer→Vision→Quality→Operator 顺序
LLM 可以: Vision → Explorer → Vision → Operator
LLM 可以: 不切换角色，全程用默认模式
LLM 可以: 切换到自己认为合适的任何角色
```

**角色定义与分阶段演进**：

> **概念区分**：本节的"阶段 1/2/3"指**角色工具白名单的扩充节奏**，不是 §十一 执行路线的"系统阶段 0-6"。两者关系：系统阶段是整体能力演进（LLM 能搜索→能看图→能反馈...），角色阶段是单个角色工具集随系统阶段扩充的细化。系统阶段 4 落地后，所有角色的"阶段 2+ 工具"才齐备。

```
┌──────────────────────────────────────────────────────────────────┐
│                    角色分阶段演进                                 │
│                                                                  │
│  Explorer 角色的工具集随系统演进而扩展:                          │
│                                                                  │
│  角色阶段 1 (系统阶段 1):  web_search 仅为搜索能力               │
│  角色阶段 2 (系统阶段 4): + search_standards, search_cases,       │
│                            search_process                         │
│  角色阶段 3 (系统阶段 4): + archive_memory, Memory.search 注入    │
│                                                                  │
│  角色定义不变，工具白名单随系统成长而扩充                        │
│  LLM 的能力自然增长，不需要改代码                                │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

| 角色 | System Prompt 侧重 | 角色阶段 1 工具 | 角色阶段 2+ 工具 |
|------|-------------------|----------|-----------|
| Explorer | 研究、搜索、整理信息 | web_search | + search_standards, search_cases, search_process, archive_memory |
| Vision | 图像分析、缺陷识别 | 多模态能力 | + search_standards, search_cases, MCP检测工具, MCP标注工具, archive_memory |
| Quality | 标准对照、合规判定 | web_search | + search_standards, search_cases, read_weldmap, request_confirmation, escalate, archive_memory |
| Operator | 操作指导、参数建议 | web_search | + read_weldmap, adjust_parameter, request_confirmation, escalate, archive_memory |

### 3.5 工具参数设计原则 — 适配 LLM 现实

"LLM 全权决策"的前提是 LLM 能可靠地调用工具。DeepSeek 等开源模型的 function calling 不如 GPT-4 稳定，工具参数必须为 LLM 不完美而设计。

```
┌──────────────────────────────────────────────────────────────────┐
│                    工具参数设计原则                                │
│                                                                  │
│  原则 1: 扁平优于嵌套                                             │
│    ✅ {"query": "Q345R预热", "domain": "welding"}                │
│    ❌ {"search": {"params": {"query":"...", "filter":{...}}}}    │
│    理由: LLM 对深层嵌套传参错误率显著上升                         │
│                                                                  │
│  原则 2: 避免大字段                                               │
│    ✅ {"image_ref": "PENDING:s001:0"}                            │
│    ❌ {"image_base64": "iVBORw0KGgoAAAANSUhEUgA..."}              │
│    理由: tool_calls 字段有 token 限制，大字段必丢失               │
│                                                                  │
│  原则 3: 枚举优于自由文本                                         │
│    ✅ {"urgency": "ROUTINE" | "URGENT" | "CRITICAL"}             │
│    ❌ {"urgency": "急一点但不是非常急"}                           │
│    理由: 枚举可静态校验，自由文本需要 LLM 二次解析                │
│                                                                  │
│  原则 4: 必填项最少化                                             │
│    ✅ search_standards(query)  其他参数有默认值                  │
│    ❌ search_standards(query, domain, year, level, format, ...)  │
│    理由: 必填项越多，LLM 漏传或填错的概率越大                     │
│                                                                  │
│  原则 5: schema 可校验，失败可重试                                │
│    工具调用前: ToolRegistry 用 JSON Schema 校验参数               │
│    校验失败: 回传 schema 错误信息给 LLM，让 LLM 重传              │
│    重试 1 次仍失败: 进入 ToolFailureObservation,LLM 自主决定下一步│
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 3.6 ToolReliability — 工具可靠性分层

> **术语澄清**: 本节的 Tier-A/B/C 是 **工具可靠性分级 (ToolReliability)**，描述某个具体工具调用失败时的保障策略——这是工具属性，不是 LLM 模型属性。区别于 §2.2 的 **LLMTier-1/2/3** (LLM 能力分级，启动时选定)。本节 Tier-C 是**单次任务的运行时降级**，不是会话级 LLMTier 切换。

并非所有工具都需要相同的可靠性。**搜索类失败可重试，操作类失败不可逆**。架构对两类工具采取不同的保障策略。

```
┌──────────────────────────────────────────────────────────────────┐
│                    工具可靠性分层 (ToolReliability)               │
│                                                                  │
│  Tier-A: 可重试工具 (信息获取类)                                  │
│    web_search, search_standards, search_cases, search_process,   │
│    read_weldmap, explain_decision, archive_memory                │
│                                                                  │
│    保障策略:                                                      │
│    - schema 校验失败 → 自动 retry 1 次                            │
│    - 工具内部错误 → 返回 ToolErrorObservation, LLM 自己决定       │
│    - 反复调用同一工具不报错 → SupervisorCenter 检测循环 → 终止    │
│                                                                  │
│  Tier-B: 必须精确工具 (操作执行类)                                │
│    adjust_parameter, design_workflow, escalate                   │
│                                                                  │
│    保障策略:                                                      │
│    - schema 校验失败 → 不重试，直接拒绝, 走 OnFailAction.REASK    │
│    - 参数缺少业务理由 (require_reason) → 拒绝                     │
│    - 高风险参数变化 → ValidationPipeline 二次拦截                 │
│    - 必须经过 PolicyHook 审批 (auto_approve=False)               │
│    - 所有调用记录到 EventLog 不可篡改                             │
│                                                                  │
│  Tier-C: 异常兜底 (单次任务运行时降级)                            │
│    适用范围: 当前 case 当前任务，不是会话级降级                   │
│    Vision 角色可用: vision_complete 直接调用                      │
│    Quality 角色可用: ValidationPipeline 直接出 Safety 判定        │
│    搜索角色可用: 关键词→规则引擎→固定工具                         │
│                                                                  │
│    触发条件 (任一满足即触发，仅影响当前任务):                     │
│    - 当前任务 LLM 连续 3 次 schema 校验失败                       │
│    - 当前任务 OnFailAction.REASK 达到 max_reask=3                 │
│                                                                  │
│    触发后行为:                                                    │
│    - 当前任务用兜底路径完成                                       │
│    - 当前 case 标记为"LLM 不稳定"，写入 EventLog                  │
│    - 通知操作员人工介入                                           │
│    - 下一个任务/会话仍然走 LLMTier-1 主路径，不持久化降级         │
│                                                                  │
│  关键: Tier-C 是 LLM 在当前任务出错时的安全网，不改变 LLMTier。   │
│  系统永远以配置的 LLMTier 启动，Tier-C 只是"这一次失败了的备用"。 │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

**Tier-C（运行时降级）vs LLMTier-3（启动时选定）的区别**:

两者都涉及"关键词 + 规则引擎"模式，但性质完全不同：

| 维度 | LLMTier-3 (§2.2) | Tier-C (§3.6) |
|---|---|---|
| 何时生效 | 系统启动时选定，永久 | 运行时触发，单次任务 |
| 触发条件 | 配置无 LLM API key，或主动选 Tier-3 | LLMTier-1/2 系统中 LLM 连续失败（3次 schema 错或 OnFailAction.REASK 达 max_reask）|
| 持续时间 | 整个系统生命周期 | 当前任务，下个任务恢复 LLMTier-1 |
| 关键词规则引擎 | 唯一可用路径（无 LLM） | LLM 失效的临时替代 |
| LLM 是否可用 | ❌ 不可用 | ✅ 仍可用（下个任务恢复）|

**为什么两者都存在**：
- LLMTier-3 是"穷版部署"——某些车间环境无外网/无 API key，系统仍要能跑（关键词 + 规则引擎兜底）
- Tier-C 是"富版故障"——LLMTier-1/2 系统运行中 LLM 临时故障（API 限流、模型宕机），不能让整个任务失败

**行为差异**：
- LLMTier-3 系统：所有任务都走关键词+规则，LLM 完全不参与
- Tier-C 触发：仅当前任务走关键词+规则，标记 case 不稳定，下个任务仍尝试 LLMTier-1

**反例**（避免混淆）：
- ❌ 把 Tier-C 当成"LLMTier-3 的运行时版本"——两者触发条件、持续时间、LLM 可用性都不同
- ❌ Tier-C 触发后永久降级到 LLMTier-3——这会让一次 LLM 抖动导致整个系统失去 LLM 能力

---

## 四、安全边界 — 架构决定不能做什么

安全是约束，不是流程。LLM 在约束内完全自由。

### 4.1 Hook 拦截

```
┌──────────────────────────────────────────────────────────────┐
│                    工具调用拦截                               │
│                                                              │
│  LLM 发起工具调用                                           │
│       │                                                      │
│       ▼                                                      │
│  ┌─────────────┐                                            │
│  │ SafetyHook  │ 安全状态为 BLOCK → DENY                   │
│  └──────┬──────┘                                            │
│         │ ALLOW                                             │
│         ▼                                                   │
│  ┌─────────────┐                                            │
│  │ PolicyHook  │ 工具需人工审批 → DENY (等待审批)         │
│  └──────┬──────┘                                            │
│         │ ALLOW                                             │
│         ▼                                                   │
│  执行工具                                                   │
│                                                              │
│  DENY 时: LLM 收到拒绝原因，自己决定下一步                 │
│  不是: 跳过该步骤继续流水线                                 │
│  而是: LLM 重新思考，可能换策略                             │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 ToolPolicy — 工具安全等级

| Tool | auto_approve | 说明 |
|------|:---:|------|
| web_search | ✅ | 搜索类，低风险 |
| search_standards | ✅ | 查询类，低风险 |
| search_cases | ✅ | 查询类，低风险 |
| search_process | ✅ | 查询类，低风险 |
| read_weldmap | ✅ | 读取类，低风险 |
| explain_decision | ✅ | 解释类，低风险 |
| archive_memory | ✅ | 记忆类，低风险 |
| switch_role | ✅ | 角色切换，低风险 |
| manage_plan | ✅ | 任务清单读写，低风险 |
| spawn_investigator | ✅ | 派生只读子调查员，低风险 |
| request_confirmation | ✅ | 请求确认本身不需审批 |
| design_workflow | ❌ | 需理由 |
| adjust_parameter | ❌ | **高风险**，需理由，每 session 最多 5 次 |
| escalate | ❌ | **高风险**，需理由 |

#### 4.2.1 MCP 工具的安全等级自动判定

MCP 工具是动态注册的（运行时通过 `tools/list_changed` 通知发现），不能像内置工具那样靠手工配表。判定按**三层规则**自动落入 ToolPolicy：

```
第一层: MCP annotations 字段 (优先级最高)
  MCP 协议允许工具声明 annotations:
  - readOnlyHint: true  → auto_approve=True
  - destructiveHint: true → auto_approve=False + require_reason=True
  - openWorldHint: true → auto_approve=False (访问外部系统)
  如果 MCP Server 提供这些字段，直接采纳。

第二层: 工具名前缀启发式 (无 annotations 时的兜底)
  read_*, get_*, list_*, search_*, find_*, query_*  → Tier-A (auto_approve=True)
  create_*, update_*, delete_*, import_*, send_*, publish_*, post_*  → Tier-B (require approval)
  其余未匹配前缀  → Tier-B 保守 (require approval)

第三层: 人工 override (覆盖前两层)
  governance/mcp_policy.yaml 可配置:
    label_studio.update_annotation: { auto_approve: false, require_reason: true }
  人工配置始终优先于自动判定。
```

#### list_changed 重判时机

MCP Server 在工具版本升级时会发送 `notifications/tools/list_changed`：

| 变更类型 | 重判行为 |
|---|---|
| 新工具上线 | 按上述三层规则自动判定 + 写入 EventLog |
| 工具名变更（前缀变了）| 重新按前缀启发式判定，旧策略失效 |
| annotations 变更 | 重新按第一层判定 |
| 工具下线 | 从 ToolRegistry 移除，LLM 下一轮看不到 |

**关键约束**:
- 自动判定结果在首次注册时计算，缓存到内存 ToolRegistry，**不在每次调用时重判**（避免性能开销）
- 所有自动判定 + 人工 override 都写入 EventLog，方便审计"为什么 update_annotation 不需审批"
- 默认偏保守：未识别前缀一律 require approval，宁可多一次人工点击也不放过潜在写操作

#### 阶段引入

| 阶段 | MCP 判定能力 |
|---|---|
| 阶段 3 | 三层规则全部到位，因为 detect_defects/annotate_label 一接入就要决定等级 |
| 阶段 5+ | governance/mcp_policy.yaml 可视化配置入口 |

### 4.3 验证管道 — 不在流程中，在决策写出时

```
验证不是流水线的一个步骤，是决策写出的守门人。

LLM 产生决策 → CognitiveGateway.publish_decision()
                    │
                    ▼
            ValidationPipeline      ← 归 Gateway 层 (§5.4)
            Safety → Rule → Shadow → Consistency
                    │
              ┌─────┴─────┐
              │           │
           通过        失败
              │           │
              ▼           ▼
          写出WeldMap  LLM 收到验证失败原因
                       LLM 自己决定怎么修正
                       (不是架构替它重试)
```

> **位置说明**: ValidationPipeline 在 `gateway/pipeline.py`，不在 `governance/`。
> 它只在 `publish_decision` 写出路径上触发，不是每次工具调用都过——
> 所以它是 Gateway 写出路径的专属步骤，不是 Governance 横切关注点。
> Governance 横切层只持有 Hook 拦截 + ToolPolicy（每次工具调用都过）。

### 4.4 冲突裁决 — 当人和 LLM 不一致时

"Human-Governed" 不等于"人永远对"。LLM 持续被否决可能反映：人理解错了、规范变了、或 LLM 看到了人忽略的特征。方案需要正面回答两类冲突。

#### 冲突类型 1: LLM 与单个操作员持续不一致

触发条件：同一操作员在 ≥3 个连续会话中 reject 同一类 LLM 判定（如反复把"气孔"改成"夹渣"）。

**双向裁决机制**:
```
连续 reject 计数 ≥ 阈值
    │
    ▼
LLM 触发 request_clarification 工具 (新增)
    │
    ├── 向人提问: "我注意到你连续 3 次把我判定的气孔改成夹渣。
    │              我看到的是 [图像特征列表]。你判断的依据是什么？"
    │
    ▼
人的回答有三种结果:
  A. 人给出新判据 → Memory.write(rule_correction, source=operator_X)
                  → LLM 学习，但标注来源（不是匿名 VALIDATED）
  B. 人意识到自己错了 → Memory.write(reverted_correction)
                       → 撤销之前的 corrections
  C. 人不解释 → 进入 §4.4 冲突类型 2 升级
```

**关键设计**:
- LLM 写入的 correction 都带 `source=operator_X`，不是无主体的"事实"
- 同一操作员的 corrections 形成 personal_profile，不污染全局 Memory
- 不同操作员对同一图像的相反判定都保留，跨操作员晋升才进入 VALIDATED（见类型 2）

#### 冲突类型 2: 人-人之间的判定冲突

触发条件：同一图像/案例，操作员 A 标"气孔"、操作员 B 标"夹渣"。

**仲裁规则**（在 ApprovalService §A.11 基础上扩展）:
| 冲突级别 | 仲裁路径 |
|---|---|
| 操作员之间 | 升级到质量工程师，质量工程师裁决写入 `verdict_by` |
| 操作员 + 质量工程师 | 升级到质量委员会（≥3 人投票）|
| 跨班次/跨厂区一致性差 | 触发 `escalate(reason=systematic_disagreement)` 通知体系负责人 |

**Memory 晋升规则**:
- 单人 correction → `source=operator_X` 只对该操作员生效
- 经质量工程师确认 → `promotion_status=VALIDATED` 全局生效
- 经委员会确认 + 跨案例验证 ≥3 次 → `promotion_status=PROMOTED` 进入规则系统

**并列修正处理（多名操作员互相矛盾且都未升级）**:

当同一图像有 ≥2 名操作员各自给出互相矛盾的修正（A 说气孔、B 说夹渣），且都未经质量工程师确认时，按以下规则处理：

| 阶段 | 处理 |
|---|---|
| 存储 | **并列存储**——每条 correction 独立写入 Memory，都标 `source=operator_X`（不同 X），不合并、不覆盖、不删除 |
| 查询 | `Memory.search` 返回所有命中项，按 `created_at` 倒序排列，metadata 标注"存在 N 条并列修正，互相矛盾" |
| LLM 决策 | LLM 在下一轮 ReAct 看到并列修正时，自行判断采信哪条；不允许架构替 LLM 选 |
| 升级触发 | 并列修正 ≥2 条且互相矛盾 → 自动触发 `request_clarification`，请质量工程师裁决 |
| 裁决后 | 质量工程师选定一条 → 该条晋升 `VALIDATED`，其它条标记 `superseded_by=<胜出条 id>` 但不删除（保留审计轨迹） |

关键约束：
- 矛盾的修正**不丢任何一条**——审计要求所有人工判定都可追溯
- 架构**不替 LLM 选**——查询返回多条让 LLM 自行判断，符合"LLM 决定做什么"
- 升级到 VALIDATED 是人工动作（质量工程师），不是系统自动——避免少数服从多数淹没专家判定

#### 关键约束

- ❌ LLM 不能自动 override 人——所有 LLM "反向质疑"必须通过 `request_clarification` 工具走对话，不能强行不接受 correction
- ❌ 单个操作员的 correction 不能立刻进入全局 Memory——必须经晋升路径
- ✅ EventLog 记录所有冲突和裁决——形成质量回溯证据链

---

## 五、7-Plane 职责

### 5.1 Interaction（交互层）

**职责**：用户交互入口，会话管理，实时通信

```
interaction/
├── api/
│   ├── chat.py              # POST /chat + WebSocket /ws
│   └── notifications.py     # 系统推送
├── agent_loop.py            # AgentLoop — 持续循环 + 推送 + 反馈
├── session.py               # 会话管理 (per-operator)
├── context.py               # 上下文解析
├── file_handler.py          # 文件处理 (图片/ZIP/PDF/Excel/CSV)
├── multimodal.py            # 多模态输入 → 统一消息格式
└── base.py                  # 消息类型定义
```

**两种交互通道**：

```
简单查询: POST /api/v1/chat/
  → ReActEngine.run() → 返回最终回复
  → 适用于: 简单问答、标准查询

实时交互: WebSocket /api/v1/chat/ws
  → AgentLoop 持续运行
  → 中间结果实时推送
  → 用户可随时反馈
  → 适用于: 图片检测、批量标注、复杂分析
```

**会话启动上下文 — 自动注入基底**（借鉴 Claude Code CLAUDE.md 模式）：

```
ReAct Loop 启动时，架构自动注入以下"世界观":

┌─────────────────────────────────────────────────────────────┐
│  WELDEVENT.md       项目级规约：核心标准/术语/默认假设       │
│                     (类似 Claude Code 的 CLAUDE.md)         │
│  OPERATOR.md        当前操作员偏好：常用单位/默认材料类型     │
│  CASE_BRIEF         当前 case 的简报：编号/批次/历史决策      │
│  Memory.search      相关历史修正/类似案例 (Few-Shot Context) │
└─────────────────────────────────────────────────────────────┘

注入位置: ReActEngine 初始化时拼接到 system prompt
注入方式: 提供"世界观"，不规定"步骤"
LLM 行为: 看到这些信息后自主决定要不要用、怎么用

关键: 这是上下文，不是流水线第一步
即使 LLM 完全不引用这些内容，依然算正确行为
```

**WebSocket 消息协议**：

```
服务端 → 客户端:
  {"type": "thinking",     "payload": {"content": "..."}}
  {"type": "tool_call",    "payload": {"tool": "search_standards", "args": {...}}}
  {"type": "tool_result",  "payload": {"tool": "search_standards", "output": {...}}}
  {"type": "decision",     "payload": {"content": "...", "confidence": 0.85}}
  {"type": "confirmation_request", "payload": {"question": "...", "options": [...]}}

客户端 → 服务端:
  {"type": "message",   "payload": {"content": "..."}}
  {"type": "feedback",  "payload": {"action": "modify", "target": "...", "value": "..."}}
  {"type": "confirm",   "payload": {"action": "approve", "decision_id": "..."}}
  {"type": "interrupt", "payload": {}}
```

**AgentLoop**：

> **并发模型**: AgentLoop 主循环本身不直接消费 feedback——直接 `await queue.get()` 会阻塞当前协程，违反"非阻塞并发"。正确模式是 **主循环 + feedback consumer 双 task**：主循环只跑 ReAct，feedback consumer 是独立 task 把用户反馈写入 Memory，主循环下一轮通过 `Memory.search` 自然看到新反馈。两者通过 Memory 通信，不直接握手。

```python
class AgentLoop:
    """持续运行的 Agent 循环。

    并发模型:
    - 主 task: ReAct Loop，永不阻塞等用户
    - feedback consumer task: 独立运行，把 feedback 写入 Memory
    - 两者通过 Memory 解耦，不通过共享 queue 握手
    """

    async def start(self, initial_input: str, images=None):
        # 启动两个独立 task
        loop_task = asyncio.create_task(self._react_loop(initial_input, images))
        feedback_task = asyncio.create_task(self._consume_feedback())
        try:
            await loop_task
        finally:
            feedback_task.cancel()  # 主循环结束时停止消费

    async def _react_loop(self, initial_input, images):
        """主循环 — 不阻塞等反馈，需要确认就推送然后继续推进。"""
        current_input = initial_input
        while not self._should_stop():
            step_result = await self._step(current_input, images)

            await self._push("thinking", step_result.thinking)
            await self._push("tool_calls", step_result.tool_calls)

            if step_result.needs_confirmation:
                # 推送确认请求 + 在 Memory 标记"待确认"
                # 不在这里 await queue.get()，主循环继续做其他事
                # 用户回复后由 feedback consumer 写 Memory，下一轮 LLM 自己看到
                await self._push("confirmation_request", step_result.question)
                self._memory.write_pending_confirmation(
                    session_id=self.session_id,
                    question=step_result.question,
                )
                # 主循环要么停在这里(单图任务)，要么进下一张图(批量任务)
                if self._mode == "single":
                    break  # 等下次用户消息触发新一轮
                else:
                    current_input = self._next_pending_task()  # 批量场景下推进
                    continue

            if step_result.is_final:
                await self._push("decision", step_result.decision)
                break

    async def _consume_feedback(self):
        """独立 task — 持续消费用户反馈，写入 Memory。

        主循环不直接 await 这个 queue。反馈进入 Memory 后，
        下一轮 LLM 推理时通过 Memory.search 自然感知。
        """
        while True:
            feedback = await self.feedback_queue.get()  # 这里阻塞是对的，本 task 就是消费者
            await self._memory.write(
                content=feedback.content,
                memory_type="user_correction",
                session_id=self.session_id,
                tags=[feedback.target, "feedback"],
            )
            # 同时清除"待确认"标记
            self._memory.clear_pending_confirmation(self.session_id)
```

**关键性质**:
- 主循环的"非阻塞"指的是**不阻塞等用户**，而不是不阻塞协程内部
- feedback consumer 内部 `await queue.get()` 是阻塞的，但它就是为了等反馈而存在的独立 task
- 主循环和 consumer 通过 Memory 通信，不通过共享变量直接握手——这让重启/重连后状态可恢复
- 单图模式：主循环在 `needs_confirmation` 后 break，等下次 WebSocket 消息触发新 AgentLoop
- 批量模式：主循环继续推进下一张图，前一张的反馈被 consumer 异步写入 Memory，LLM 在后续轮次自然看到

**单图模式 break + cancel feedback_task 的设计说明**（消除"反馈丢失"疑虑）:

单图模式下 break 后 `feedback_task.cancel()` 看起来会丢反馈，但这是有意的：

| 场景 | 处理 | 理由 |
|---|---|---|
| LLM 主动请求确认（`request_confirmation`）| 主循环 break，feedback_task cancel | LLM 已暂停等用户答，用户答案应该是新 WebSocket 消息触发新 AgentLoop，不属于"反馈" |
| 用户在 break 期间发的反馈 | 走新 AgentLoop 的 feedback_queue | 旧 feedback_task 属于上一轮，新轮重新建 queue；用户的反馈会被新轮的 consumer 接收 |
| 用户在批量模式中发反馈 | feedback_task 不 cancel，继续写 Memory | 批量模式主循环不 break，consumer 持续运行 |

**关键区分**：
- `request_confirmation` 的答案 = **新对话输入** → 触发新 AgentLoop（不是 feedback）
- 批量模式中用户改标注 = **反馈** → 走 feedback_queue → Memory.write（不中断主循环）

单图模式的"break 等用户"本质是"LLM 把话筒交给用户"，用户回复是新对话，不是对旧对话的反馈。所以 cancel 旧 feedback_task 不丢任何东西——旧轮该结束，新轮重新开始。

反例（避免）：
- ❌ 单图模式 break 后保留 feedback_task 等用户反馈——会让旧轮状态半死不活，新轮和旧轮的 feedback_queue 混淆
- ❌ 把 `request_confirmation` 的答案当 feedback 处理——答案应该进 LLM 的下一轮 thinking，不是写 Memory



**非阻塞并发**：

```
┌──────────────────────────────────────────────────┐
│              asyncio 事件循环                     │
│                                                  │
│  ┌─────────────┐  ┌─────────────┐               │
│  │ AgentLoop 1 │  │ AgentLoop 2 │  ...          │
│  │ (操作员A)   │  │ (操作员B)   │               │
│  │ 等待反馈    │  │ 推理中      │               │
│  │ await q.get │  │ await LLM   │               │
│  └─────────────┘  └─────────────┘               │
│                                                  │
│  多连接并行 · 等待时不占 CPU                      │
└──────────────────────────────────────────────────┘
```

---

### 5.2 Governance（治理层）

**职责**：工具调用的横切约束（Hook 拦截 + ToolPolicy）、审批、升级

> **范围澄清**: Governance 只持有**真正的横切关注点**——每次工具调用都过的 Hook 拦截、ToolPolicy 审批规则、EscalationTracker 升级追踪、OnFailAction 失败策略。
> **验证管道（ValidationPipeline）不在这里**——它只在决策写出时触发，不是每次工具调用都过，属于 Gateway 写出路径的专属步骤，见 §5.4。

```
governance/
├── hooks.py                 # SafetyHook + PolicyHook (每次工具调用都过)
├── tool_policy.py           # 工具策略 (哪些需审批)
├── approval.py              # 审批服务 (同步/异步)
├── escalation.py            # 升级追踪 (per-case 隔离)
├── review.py                # 人工审查
└── on_fail.py               # OnFailAction (REASK/FIX/FILTER/REFRAIN/ESCALATE)
```

**EscalationTracker**：

```
per-case 隔离，不共享状态

_states: dict[CaseId, EscalationState]

case-001 → consecutive_critical=2 → CONTINUE
case-002 → consecutive_critical=4 → COGNITIVE_FALLBACK
case-003 → consecutive_critical=5 → HUMAN_INTERVENTION
```

---

### 5.3 Control（控制层）

**职责**：ReAct 循环引擎、工具注册、角色管理、状态追踪

> **阶段引入提醒**: 下面的文件清单是**最终状态**，不是"阶段 1 就要建出来"的清单。每个文件标注了引入阶段，详见 §A 头部映射表。**严禁一次性把所有文件建出来**——这正是上次审计指出的"过度前瞻"。

```
control/
├── react.py                  # ReActEngine — 推理引擎 (阶段 3 引入)
├── agent_loop.py             # AgentLoop — 主循环+feedback consumer (阶段 3 引入)
├── roles.py                  # AgentRole 定义 (阶段 4 引入)
├── orchestrator.py           # BrainOrchestrator — LLM 可调用工具 (阶段 5 引入)
├── tool_registry.py          # ToolRegistry — 工具注册 (阶段 1 起即需要)
├── mcp_registry.py           # L4 MCP 工具发现与注册的薄桥接 (阶段 3 引入)
│                             # 只做发现+注册，工具实现在 L4 (见 §8 adapters/mcp/)
├── tools/                    # L1 认知层 Tool 协议层（BrainTool ABC + 函数签名）
│                             # 协议入口都在这里，但实现按 §13 判别原则下沉到 L4 / L1 平面内 Provider
│                             # 纯认知工具（语义只在 ReAct Loop 内有意义）：
│   ├── request_confirmation.py  # 阶段 3 — LLM 请求用户确认，纯认知行为
│   ├── request_clarification.py # 阶段 4 — LLM 反向提问，纯认知行为 (见 §4.4)
│   ├── request_image_detail.py  # 阶段 5 — LLM 主动请求图像升级，认知行为 (见 §A.3)
│   ├── switch_role.py           # 阶段 5 — LLM 自我管理角色切换
│   ├── manage_plan.py           # 阶段 5 — LLM 任务拆解 (借鉴 Claude Code TodoWrite)
│   ├── spawn_investigator.py    # 阶段 6 — LLM 派生子调查员 (借鉴 Claude Code Task tool)
│   ├── explain_decision.py      # 阶段 4 — LLM 解释自己的决策
│   ├── escalate.py              # 阶段 3 — LLM 主动升级，认知决策（写出走 Gateway）
│   ├── archive_memory.py        # 阶段 4 — LLM 主动归档，认知行为
│   ├── design_workflow.py       # 阶段 5 — LLM 设计工作流，纯认知产出（草案，不写出）
│                             # 边界用例（协议在 control/tools/，实现委托到 L4/Provider）：
│   ├── web_search.py            # 阶段 1 (✅ 完成) — 薄入口，调 capability/web_search.py
│   ├── search_standards.py      # 阶段 3 — 薄入口，调 knowledge/standards.py（最终走 adapters/retrieval/）
│   ├── search_cases.py          # 阶段 3 — 薄入口，调 knowledge/cases.py
│   ├── search_process.py        # 阶段 4 — 薄入口，调 knowledge/process.py
│   ├── read_weldmap.py          # 阶段 3 — 薄入口，调 GatewayReadDeps.read（无验证）
│   └── adjust_parameter.py      # 阶段 5 — 薄入口，调 GatewayWriteDeps.write（写出经 ValidationPipeline）
├── skills/                   # 业务流程 (阶段 6+，可选)
│                             # Skill = 声明式静态步骤；Orchestrator = LLM 动态规划
│                             # 两者关系与判别见 §13 "Skill vs Orchestrator 对比表"
│   ├── base.py               # Skill ABC + SkillStep
│   ├── weld_inspect.py
│   ├── data_annotation.py
│   └── report_generation.py
├── hooks.py                  # SafetyHook + PolicyHook (阶段 3 起需要)
├── persona.py                # PersonaSelector (阶段 4 引入，见 §A.2)
├── reasoning_mode.py         # ReasoningModeSelector (阶段 4 引入，见 §A.2)
├── decision_factory.py       # DecisionFactory (阶段 3 起步 1-2 种类型，见 §A.4)
├── supervisor.py             # SupervisorCenter (阶段 3 引入，循环/超时)
├── fallback.py               # ToolReliability Tier-C 兜底 (阶段 3 引入)
├── event_log.py              # Append-only EventLog (阶段 3 起即引入，见 §A.6)
├── checkpoint.py             # 决策检查点/回滚 (阶段 5+，见 §A.7)
├── state_machine.py          # BrainStateMachine (阶段 3 起 3 态→12 态，见 §A.1)
├── deps.py                   # CognitiveDependencies (阶段 1 起即引入)
├── ports.py                  # 控制层端口
└── exceptions.py             # 控制层异常
```

**阶段 1 实际只需要的文件**: `tool_registry.py` + `tools/web_search.py` + `deps.py` + `ports.py` + `exceptions.py`。其余全部不建。

**Orchestrator 是工具，不是管线**：

```
design_workflow 工具 → 调用 Orchestrator.execute()
                                │
                                ▼
                    Orchestrator 是 LLM 可以调用的一个"深度思考"工具
                    不是外部驱动的管线

                    LLM 觉得需要系统化规划时，调用 design_workflow
                    LLM 觉得自己直接推理就够了，不需要调用

                    Orchestrator 内部实现:
                    - 构造完整上下文 (知识+记忆+WeldMap状态)
                    - 多轮推理 (知识检索→推理→决策草案)
                    - 返回**决策草案**给 LLM (不直接写 WeldMap，不直接调 ValidationPipeline)

                    但这一切是 LLM 选择调用的，不是架构强制的
```

> **Orchestrator 不调 ValidationPipeline**：
> Orchestrator 在 Control 层，拿到的是 `GatewayReadDeps`（只读），拿不到 `GatewayWriteDeps.validation`。
> Orchestrator 输出的是**决策草案**——草案返回给 LLM 后，LLM 决定是否调 `adjust_parameter` / `escalate` 等写出工具，
> 这些写出工具走 Gateway 写出路径，由 Gateway 内部的 `ValidationPipeline` 守门。
> **正式验证只发生在 Gateway 写出时**，Orchestrator 内部不做预检——重复验证既冗余又会让"验证发生在哪里"变得不可追踪。
> 如果 LLM 想在调用前自我审查，应通过提示词工程（让 LLM 先列风险）或调 `search_standards` 等只读工具，而不是让 Orchestrator 持有 ValidationPipeline。

---

### 5.4 Gateway（关口层）

**职责**：Brain → WeldMap 唯一写出通道（含写出前的验证守门）

> **范围澄清**: Gateway 不仅做"HTTP 转发"——它持**决策写出前的验证管道**（ValidationPipeline + 4 类 validators）。
> 验证管道**只在决策写出时触发**，不是每次工具调用都过——所以它属于 Gateway 写出路径的专属步骤，不属于 Governance 横切层。
> WritePort 内部按操作类型路由不同验证策略：决策类走完整 4 阶段，通知类（notify_workflow_trigger）走轻量 schema 校验。

```
gateway/
├── ports.py                  # CognitiveGatewayWritePort + ReadPort (见 §7 拆分)
├── write_gateway.py          # Decision/Escalation/Instruction → WeldMap
│                             #   内部先过 ValidationPipeline，再调 adapters/weldmap/
├── read_gateway.py           # WeldMap 查询 (无需验证)
├── events.py                 # ActionEvent ↔ ObservationEvent 双ID配对
├── pipeline.py               # 4 阶段验证 (决策写出的守门人，从 Governance 移入)
├── validators/               # 验证器 (从 Governance 移入)
│   ├── safety.py             # 安全验证 (高压/有毒/爆炸 → BLOCK)
│   ├── rule.py               # 规则验证 (低置信度/空理由 → REJECT)
│   ├── shadow.py             # 影子验证 (独立置信度对比)
│   └── consistency.py        # 一致性验证 (事实/历史矛盾)
└── adapters/
    └── in_memory.py          # 内存实现
```

> **HTTP 客户端归属**: Gateway 不直接持有 HTTP 客户端——HTTP 调用归 `adapters/weldmap/weldmap_http.py`（见 §8）。
> `gateway/write_gateway.py` 委托 `adapters/weldmap/` 完成实际 HTTP 调用。

**核心约束**：所有 Brain 决策必须通过 Gateway 写出，禁止直接访问 WeldMap。

```
WritePort:
  publish_decision(decision) → PublishResult
  publish_escalation(escalation) → PublishResult
  publish_instruction(instruction) → PublishResult
  notify_workflow_trigger(case_id, config) → None

ReadPort:
  read_weldmap_snapshot(case_id) → WeldMapSnapshot
  read_workflow_state(case_id) → WorkflowState | None
  read_case_data(case_id) → CaseData | None
```

---

### 5.5 Knowledge（知识层）

**职责**：知识检索（标准、案例、工艺），Web 搜索补充，按主体域组织

```
knowledge/
├── ports.py                  # RAGQueryPort, StandardsQueryPort, etc.
├── rag.py                    # RAG 查询入口 — 调 adapters/retrieval/ 做向量+FTS+RRF
├── domains/                  # 知识主体域
│   ├── welding.py            # 焊接工艺域
│   ├── inspection.py         # 检测技术域
│   ├── quality.py            # 质量管理域
│   └── equipment.py          # 设备知识域
├── standards.py              # 标准查询 (查询逻辑 + metadata 过滤)
├── cases.py                  # 案例库查询 (查询逻辑 + metadata 过滤)
├── process.py                # 工艺知识查询 (查询逻辑 + metadata 过滤)
└── adapters/
    └── stub.py               # StubKnowledgeAdapter
```

> **检索基础设施共享约束**: Knowledge 不自建向量索引/FTS/RRF——这些**算法原语**下沉到 `adapters/retrieval/`（见 §8），Memory 也调用同一份原语。
> 共享层范围：向量检索 + 全文检索 + RRF 融合 + reranker。
> 各自独有：查询构造、metadata 过滤、数据 schema。
> 这样 Knowledge 的 RAG 和 Memory 的语义搜索使用同一套引擎，但保留各自的查询语义。

**知识主体域结构**：

```
┌──────────────────────────────────────────────────────────────────────┐
│                    知识库按主体域组织                                 │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  焊接工艺域 (Welding)                                       │    │
│  │  ├── 焊接方法: GMAW, GTAW, SAW, SMAW 参数范围              │    │
│  │  ├── 材料特性: Q345R, Q235B, 304SS 焊接参数               │    │
│  │  ├── 预热/后热: 温度、时间、适用条件                        │    │
│  │  └── 坡口设计: 形式、尺寸、适用板厚                         │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  检测技术域 (Inspection)                                    │    │
│  │  ├── 无损检测: RT, UT, MT, PT 方法与适用场景               │    │
│  │  ├── 缺陷图谱: 气孔、夹渣、裂纹、未熔合 影像特征           │    │
│  │  ├── 检测工艺: 透照参数、灵敏度、评定级别                   │    │
│  │  └── 验收标准: NB/T47014, GB/T3323, ASME 等级划分          │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  质量管理域 (Quality)                                       │    │
│  │  ├── 质量体系: ISO 3834, 特种设备制造许可证                 │    │
│  │  ├── 返修管理: 返修流程、次数限制、热处理要求               │    │
│  │  ├── 不合格品: 处置流程、让步接收条件                       │    │
│  │  └── 质量追溯: 档案管理、追溯链                              │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  设备知识域 (Equipment)                                     │    │
│  │  ├── 焊接设备: 电源、送丝机、焊枪 参数匹配                 │    │
│  │  ├── 检测设备: X射线机、超声仪、磁探仪 适用范围            │    │
│  │  ├── 辅助设备: 预热器、热处理炉 温控参数                   │    │
│  │  └── 校准维护: 设备校准周期、精度要求                       │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  域之间有关联:                                                      │
│  焊接工艺域.材料Q345R ←→ 检测技术域.RT透照参数                    │
│  检测技术域.缺陷图谱 ←→ 质量管理域.验收标准                       │
│  设备知识域.焊接电源 ←→ 焊接工艺域.GMAW参数                       │
│                                                                      │
│  查询时: 单域查询 + 跨域关联查询                                    │
│  search_standards(domain="welding", query="Q345R预热")              │
│  search_cases(domain="inspection", query="气孔影像特征")            │
│                                                                      │
│  LLM 自主决定查哪个域、要不要跨域                                   │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

**知识来源路由**：

```
查询进入
   │
   ▼
内部知识库 (Knowledge ports, 按主体域路由)
   │
   ├── 命中 → 返回
   │
   └── 未命中 → Web 搜索 (DuckDuckGo / Tavily)
                  │
                  ├── TAVILY_API_KEY → TavilySearchProvider
                  └── 否则 → DuckDuckGoSearchProvider (零配置)

但注意: LLM 也可以直接调 web_search，不依赖内部知识库
知识域和 Web 搜索是互补关系，不是降级关系
```

---

### 5.6 Memory（记忆层）

**职责**：M0-M5 分层记忆，反馈学习，上下文压缩

> **命名说明**：Memory 层级用 **M0-M5** 前缀，避免与系统分层（L1 Cognitive Plane / L2 Temporal / L4 工具层 / L5 Data Plane）的 L 前缀混淆。

```
memory/
├── ports.py                  # Search/Read/Write/Promotion/Archive/Confidence
├── hierarchy.py              # M0-M5 层级 + 晋升规则
├── blocks.py                 # Block 工作记忆 (XML编译, checkpoint, undo)
├── search.py                 # 多级搜索 (查询构造 + metadata 过滤)
│                             # 向量+FTS+RRF 算法原语共享 adapters/retrieval/ (见 §8)
├── promotion.py              # RAW → VALIDATED → PROMOTED
├── compaction.py             # 上下文压缩 (昂贵→便宜降级链)
├── dual_write.py             # 双写 (PG + 向量库)
├── archive.py                # 记忆归档
├── confidence.py             # 置信度 (similarity × source × recency × validations)
├── learning/                 # 学习模块
└── adapters/
    └── port_adapters.py      # 适配器
```

**M0-M5 层级**：

| 层级 | 名称 | 存储 | 生命周期 |
|-----|------|------|---------|
| M0 | Realtime | Redis | 会话内 |
| M1 | Working | Redis | 会话间 |
| M2 | Case | PostgreSQL | 持久化 |
| M3 | Experience | PostgreSQL | 持久化 |
| M4 | Knowledge | PG + Milvus | 永久 |
| M5 | Audit | PostgreSQL | 审计日志 |

**反馈学习 — Few-Shot Context**：

```
用户反馈 "这是气孔不是夹渣"
     │
     ▼
Memory.write(
  content="用户修正：椭圆形暗区应为气孔而非夹渣",
  memory_type="correction",
  tags=["缺陷判定","气孔","夹渣","椭圆形暗区"],
  source=operator_X,            ← correction 标注来源
  promotion_status=RAW          ← 待质量工程师确认才晋升 VALIDATED
)
     │
     ▼
下次推理:
Memory.search(当前特征描述) → 命中修正案例 (similarity > 0.7)
     │
     ▼
注入 system prompt: "历史修正: 椭圆形暗区→气孔而非夹渣"
     │
     ▼
LLM 参考此上下文推理 → 判断倾向"气孔"

不重新训练 · 不微调 · 纯上下文注入
```

**晋升规则**：

```
M0→M1  auto (会话结束)
M1→M2  auto (案例完成)
M2→M3  confidence>0.8 + 3次验证 + 人工确认 + 初始化 last_verified_at
M3→M4  confidence>0.95 + committee + 刷新 last_verified_at
```

**`last_verified_at` 字段**（ROUTINE 过期机制依赖，见 §A.2）：
- M0/M1/M2 条目无 `last_verified_at`——不触发 ROUTINE（架构只在 M3+ 探针命中时考虑 ROUTINE）
- M3 条目 `last_verified_at` = 人工确认时刻
- M4 条目 `last_verified_at` = committee 通过时刻
- 复审 task 通过时刷新；复审失败降级 confidence 或归档（条目不再参与 ROUTINE 探针）

**用户反馈分两种性质，晋升路径不同**（与 §4.4 冲突裁决一致）：

| 反馈类型 | 含义 | promotion_status | 是否进全局 Memory |
|---|---|---|---|
| **validation**（确认）| 用户确认 LLM 标注正确（"img1 你判对了"）| 直接 `VALIDATED` | ✅ 全局生效 |
| **correction**（修正）| 用户改 LLM 标注（"img1 是气孔不是夹渣"）| `source=operator_X`，不自动晋升 | ❌ 只对该操作员生效，需质量工程师确认才晋升 `VALIDATED` |

**关键约束**（消除之前的描述矛盾）：
- 单个操作员的 correction **不直接进全局 Memory**——避免个人偏见污染全局
- validation 可以直接 `VALIDATED`——确认正确不涉及争议
- correction 经质量工程师确认（§4.4 冲突类型 2）才晋升 `VALIDATED` 全局生效
- §6.3 数据标注场景里"用户修改 img1 标注"属于 **correction**，不是 validation——其 `Memory.write` 应标 `source=operator_X`，不直接 `VALIDATED`

---

### 5.7 Capability（能力层）

**职责**：LLM 推理能力封装，Web 搜索

```
capability/
├── ports.py                  # LLMProvider ABC
├── provider.py               # LLMRequest / LLMResponse
├── openai_provider.py        # complete, stream, embed, vision_complete
├── config.py                 # LLMConfig, OpenAIConfig
├── web_search.py             # WebSearchProvider
│                             #   TavilySearchProvider (需 key)
│                             #   DuckDuckGoSearchProvider (零配置)
│                             #   AutoWebSearchProvider (自动选择)
├── mock.py                   # MockLLMProvider
└── deepseek.py               # DeepSeek adapter
```

> **web_search 的双层归属澄清**（消除"web_search 既是 Capability 又是 Tool"的误解）:
>
> - `capability/web_search.py` 里的 `WebSearchProvider`（Tavily/DuckDuckGo 适配器）是**能力实现**——属于 Capability 层，封装外部搜索 API 的调用细节
> - `control/tools/web_search.py` 里的 `WebSearchTool`（BrainTool 实现）是**工具入口**——属于 Control 层的 Tool 层，是 LLM 在 ReAct 中调用的对象
>
> 两者关系：**Tool 调用 Provider**。LLM 调 `web_search` 工具 → 工具内部调 `WebSearchProvider.search()` → Provider 调 Tavily/DuckDuckGo API。
>
> 这和 LLM 的分层一致：`capability/deepseek.py`（DeepSeek 适配器，Capability 层）被 Control 层的 ReActEngine 调用，但 LLM 本身不是 Tool。Capability 层放"能力实现"，Control 层的 Tool 层放"LLM 可调用的工具入口"。

**LLM 提供商**：

| 提供商 | 用途 | 配置 |
|-------|------|------|
| DeepSeek | 通用推理 | DEEPSEEK_API_KEY |
| Volc | 多模态视觉 | VOLC_API_KEY |
| DuckDuckGo | Web搜索(零配置) | 无需 key |
| Tavily | Web搜索(高质量) | TAVILY_API_KEY |

---

### 5.8 Copilot（副驾驶层）

> **定位声明**: Copilot **不是独立 LLM 链路**，是 **ReAct Loop 的 5 个预设入口配置**。每个 Copilot API 本质等于"`POST /chat` + 预设角色 + 预设工具白名单 + 预设系统提示"。这避免了"两套 ReAct"的架构二义。

**职责**：5 个专业副驾驶——每个对应一种被 B-level 用户主动召唤的使用模式

```
copilot/
├── qa.py                     # 知识问答    = ReAct + Explorer + readonly_tools
├── explain.py                # 决策解释    = ReAct + Quality + DecisionRepo
├── investigate.py            # 异常调查    = ReAct + Vision + Knowledge+Memory
├── compliance.py             # 合规协助    = ReAct + Quality + ReviewRepo
└── operate.py                # 操作指导    = ReAct + Operator + GatewayRead
```

> **命名说明**: Copilot 子模块原名 `govern`，与基础设施层 `Governance`（治理平面）同名易混。
> `Governance` 是横切层（Hook/Policy/审批/升级），`copilot/compliance` 是面向用户的合规协助入口（审批进度查询、合规检查辅助）。验证管道(ValidationPipeline)不在 Governance，归 Gateway 写出路径（见 §5.4）。
> 两者职责不同：前者是架构约束，后者是产品功能。改名 `compliance` 消除歧义。

#### Copilot 与 ReAct Loop 的关系

```
POST /api/v1/copilot/qa  ─┐
POST /api/v1/copilot/explain ─┤
POST /api/v1/copilot/investigate ─┼─► CopilotRouter
POST /api/v1/copilot/compliance ─┤        │
POST /api/v1/copilot/operate ─┘       │
                                       ▼
                              组装预设的 ReActContext:
                              - role: 预设角色
                              - allowed_tools: 预设白名单
                              - system_prompt_addon: 任务特化提示
                              - decision_output_types: 该 Copilot 支持的输出类型
                                       │
                                       ▼
                              ReActEngine.run(user_input, context)
                              (跟 POST /chat 走完全相同的引擎)
```

#### 为什么要有独立 API 而不直接用 `/chat`

| 场景 | 走 `/chat` | 走 Copilot 端点 |
|---|---|---|
| 操作员日常对话 | ✅ 默认入口 | ❌ 不需要 |
| 程序化集成（车间面板/移动 App）| ❌ 需要前端组装角色 | ✅ URL 即配置，少出错 |
| 权限隔离（实习生只能调 QA）| ❌ 在 `/chat` 里做权限检查复杂 | ✅ API 路由直接做 ACL |
| 监控/计费分类 | ❌ 难分类 | ✅ URL 自带分类标签 |

#### 阶段引入时机

| Copilot | 引入阶段 | 理由 |
|---|---|---|
| qa | 阶段 4 (知识库可用后) | 没有知识库的 QA 是无源之水 |
| explain | 阶段 4 (DecisionRepo 稳定后) | 早期决策少，无需独立解释端点 |
| investigate | 阶段 5 (Memory 学习阶段跑通后) | 需要历史数据支撑 |
| compliance | 阶段 4 (审批流出现后) | 阶段 3 之前没有正式审批 |
| operate | 阶段 5 (生产线集成后) | 早期没有 L2 工作流可指导 |

阶段 1-3 不需要 Copilot 端点，所有交互走 `/chat` 即可。Copilot 是"产品化包装"，不是"新能力"。

---

### 5.9 Bridge（L1→L2 桥接层）

```
bridge/
├── event_connector.py        # CognitiveGateway → DecisionTranslator
├── decision_translator.py    # BrainDecision → WorkflowTemplate
└── workflow_launcher.py      # Template → Temporal
```

---

## 六、核心数据流

### 6.1 LLM 自主分析焊缝图片

```
前端上传图片 + "分析这张焊缝"
    │
    ▼
WebSocket → AgentLoop
    │
    ▼
ReActEngine.run(user_input, images=[base64...])
    │
    ▼
LLM 收到多模态消息 (text + image)
    │
    ├── LLM 决定: "我看到了疑似气孔，先查一下验收标准"
    │   └── tool_call: search_standards("焊缝气孔验收标准")
    │       └── Hook: ALLOW (查询类，低风险)
    │       └── 结果: 气孔≤2mm合格
    │
    ├── LLM 决定: "标准拿到了，再看一下类似案例"
    │   └── tool_call: search_cases("气孔 焊缝 检测案例")
    │       └── 结果: 3个类似案例
    │
    ├── LLM 决定: "案例中也有修正记录，查一下记忆"
    │   └── (Memory.search 注入到上下文)
    │   └── 结果: 历史修正: "圆形暗区→气孔"
    │
    ├── LLM 决定: "综合标准、案例和图片，判定为气孔，1.5mm，合格"
    │   └── 最终回答
    │
    └── 推送 decision 到前端

注意: 上述步骤不是架构规定的，是 LLM 自己选择的
LLM 可能选择完全不同的步骤和顺序
```

#### 6.1.1 多模态视觉 vs detect_defects 工具 — 协作机制

LLM 看图有两条路径：**直接多模态看图**（消息层 image_url）和**调 `detect_defects` 工具**（CV/目标检测模型量化结果）。哪条先走、谁辅助谁，全部由 LLM 自主决策——架构只规定两件事：

**约束 1: 默认入场是 Thumbnail (§A.3)**
- 消息层只放低分辨率缩略图 (~85 tokens)
- LLM 看到图的"轮廓"，但细节不足以做精确测量
- 这是成本闸门，不是给 LLM 的"半成品"

**约束 2: 工具结果回流为 tool_result + 可选的 image_ref**
- `detect_defects(image_id)` 返回结构化框: `[{box, label, score}, ...]`
- 同时可附带"标注后的可视化图" image_ref，下一轮注入消息层
- LLM 拿到的是"量化数字 + 可选可视化"，而不是替换原图

**协作模式**（LLM 自主选择，下面只是常见模式举例）：

```
模式 A: 工具优先 (适合规则触发的常规检测)
  Thumbnail 入场 → detect_defects → 拿到 5 个框
  → LLM 看 Thumbnail + 框列表 → 觉得分类可疑
  → request_image_detail(level="roi", roi=框3) → 升级看清局部
  → 综合判定: "框3 是气孔不是夹渣"

模式 B: 视觉优先 (适合 Explorer 探索新缺陷类型)
  Thumbnail 入场 → LLM 直接看图 → 形成假设
  → detect_defects 验证位置/数量 → 量化对比
  → 假设修正 → 写回 Memory

模式 C: 双轨并行 (Vision 角色复杂场景)
  Thumbnail 入场 → LLM 看图同时调 detect_defects (并发 tool_calls)
  → 两个结果交叉比对: LLM 看到的 vs CV 检出的
  → 不一致触发 request_image_detail 复核
```

**关键点**:
- 三种模式都不是架构选的，是 LLM 在工具语义和成本提示下自我决定
- `detect_defects` 是 Tier-A 可重试工具（信息收集类），自动 ALLOW
- 标注场景里 `detect_defects` 通常先跑（生成预标注框），LLM 的角色是"基于框给建议 + 处理用户修正"
- 多模态原图（非 Thumbnail）只在 LLM 主动 `request_image_detail` 后注入——这条规则在标注/检测/探索场景都成立

```

### 6.2 Human-Governed 决策

```
┌─────────────────────────────────────────────────────┐
│   Agent ──────▶ Suggest ──────▶ Human              │
│                                     │               │
│                                     ▼               │
│                                  Decide             │
│                                     │               │
│                                     ▼               │
│   System ◀────── Execute ◀─────────┘              │
└─────────────────────────────────────────────────────┘

Agent 只能建议，不能直接执行
关键判定须经人工确认才能生效
```

### 6.3 数据标注场景 — 完整数据流

这是系统的北星用例：上传 50 张焊缝图片 → LLM 逐张分析 → 标注 → 用户修改 → 系统学习 → 越标越准。

```
┌─────────────────────────────────────────────────────────────────────┐
│                    数据标注场景完整流程                               │
│                                                                     │
│  1. 上传: 用户上传 50 张焊缝图片                                     │
│     POST /api/v1/chat/ + files=[img1..img50]                        │
│                                                                     │
│  2. LLM 规划: "50 张图，我需要制定标注策略"                          │
│     LLM 自主决定:                                                    │
│     ├── 先看前几张图了解类型和分布                                    │
│     ├── 搜索相关标准确定缺陷类别                                      │
│     ├── 设计标注方案（类别、判定标准）                                │
│     └── 请求用户确认方案                                              │
│                                                                     │
│  3. 逐张处理 (非阻塞，asyncio 并发):                                 │
│                                                                     │
│     ┌─────────┐   ┌─────────┐   ┌─────────┐                       │
│     │ 图片 1  │   │ 图片 2  │   │ 图片 3  │  ...                   │
│     │ 推理中  │   │ 推理中  │   │ 等待    │                        │
│     └────┬────┘   └────┬────┘   └─────────┘                       │
│          │              │                                           │
│     LLM 分析:     LLM 分析:                                         │
│     "检测到3个   "检测到1个                                          │
│      缺陷"        缺陷"                                             │
│          │              │                                           │
│     MCP 工具:     MCP 工具:                                         │
│     detect_       detect_                                           │
│     defects()     defects()                                         │
│          │              │                                           │
│     LLM 决定:     LLM 决定:                                         │
│     annotate_    annotate_                                          │
│     label()      label()                                            │
│          │              │                                           │
│          ▼              ▼                                           │
│     推送结果:     推送结果:                                         │
│     {image:1,    {image:2,                                          │
│      boxes:[...]} boxes:[...]}                                      │
│     ↓ 前端实时渲染  ↓ 前端实时渲染                                  │
│                                                                     │
│  4. 用户修改 (非阻塞):                                               │
│     用户在图片1上修改标注 → WebSocket feedback                       │
│     {"type":"feedback",                                             │
│      "target":"annotation",                                         │
│      "image_id": 1,                                                 │
│      "action":"modify",                                             │
│      "original": {"label":"夹渣","box":[...]},                      │
│      "corrected": {"label":"气孔","box":[...]}}                      │
│              │                                                      │
│              ▼                                                      │
│     Memory.write(                                                   │
│       content="用户修正: img1 区域3 应为气孔而非夹渣",               │
│       tags=["气孔","夹渣","缺陷判定"],                              │
│       source=operator_X,           ← correction 不直接晋升           │
│       promotion_status=RAW         ← 待质量工程师确认才 VALIDATED    │
│     )                                                               │
│              │                                                      │
│     系统【不卡住等待】→ 继续处理图片4,5,6...                        │
│              │                                                      │
│  5. 学习即时生效:                                                    │
│     处理图片7时:                                                     │
│     Memory.search("椭圆形暗区") → 命中 img1 修正案例                │
│     → 注入 system prompt: "历史修正: 椭圆形暗区→气孔"               │
│     → LLM 对图片7的判断倾向"气孔"                                   │
│                                                                     │
│  6. 批量完成后:                                                      │
│     50 张图全部处理完 → 汇总报告推送前端                            │
│     每张图的标注历史、用户修改、系统学习记录均可追溯                 │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**非阻塞关键机制**：

```
┌──────────────────────────────────────────────────────────────────────┐
│                    非阻塞并发模型                                     │
│                                                                      │
│  asyncio 事件循环中:                                                 │
│                                                                      │
│  Task-1: 图片1 → 推理 → 标注 → 推送 → 等待反馈?                    │
│  Task-2: 图片2 → 推理 → 标注 → 推送 → 等待反馈?                    │
│  Task-3: 图片3 → 推理 → ...                                         │
│  ...                                                                 │
│                                                                      │
│  关键: 系统不卡在"等用户改完图片1"才处理图片2                        │
│                                                                      │
│  反馈处理是独立的:                                                   │
│  用户修改图片1的标注 → feedback_queue.put(modification)              │
│  系统正在处理图片5 → Memory.write(correction)                        │
│  系统处理图片7时 → Memory.search 命中修正 → 即时学习                 │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────┐      │
│  │  时间线:                                                    │      │
│  │                                                            │      │
│  │  T0: 开始处理 img1, img2, img3 (并行)                      │      │
│  │  T1: img1 标注推前端, img2 标注推前端, img3 推理中          │      │
│  │  T2: 用户修改 img1 标注 → Memory.write → 系统继续 img4     │      │
│  │  T3: img3, img4 标注推前端 (img1 的修正已生效)             │      │
│  │  T4: 用户修改 img2 标注 → Memory.write → 系统继续 img5     │      │
│  │  ...                                                       │      │
│  │  Tn: 50 张图全部处理, 学习持续累积                         │      │
│  └────────────────────────────────────────────────────────────┘      │
│                                                                      │
│  技术实现:                                                           │
│  - asyncio.create_task() 并发处理每张图                              │
│  - asyncio.Semaphore(N) 限制同时进行中的推理数（见下方并发限流）     │
│  - feedback_queue: asyncio.Queue 接收用户反馈                        │
│  - Memory.write 是 await 但不阻塞主循环                              │
│  - Memory.search 每次推理前自动执行 (上下文注入)                     │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

**并发限流与重试策略**（阶段 3 引入，工程必备）：

50 张图同时 `create_task` 看上去很美，但 LLM 提供商有 QPS 限制——DeepSeek 典型上限 5–10 QPS，无脑并发 50 个会立刻撞 429。需要 Semaphore + 重试 + 退避三件套。

```python
# capability/llm_pool.py（阶段 3 引入）
import asyncio
import random
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")

class LLMCallPool:
    """LLM 并发限流 + 重试 + 指数退避

    设计要点：
    1. Semaphore 控制同时在飞的 LLM 调用数（不是任务数）
    2. 重试也要走 Semaphore——否则 50 任务 × 平均 1.5 次重试 = 75 真实调用
    3. 429 / 503 退避，其它错误（schema 失败等）由 ReAct 层 OnFailAction 处理
    """

    def __init__(
        self,
        max_concurrent: int = 5,      # DeepSeek 安全值，给重试留余量
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
    ) -> None:
        self._sem = asyncio.Semaphore(max_concurrent)
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            async with self._sem:                # 重试也占信号量
                try:
                    return await fn()
                except RateLimitError as exc:    # 429
                    last_exc = exc
                    delay = min(
                        self._base_delay * (2 ** attempt) + random.uniform(0, 0.5),
                        self._max_delay,
                    )
                except ServiceUnavailableError as exc:  # 503
                    last_exc = exc
                    delay = min(self._base_delay * (2 ** attempt), self._max_delay)
                except Exception:
                    raise                         # schema/business 错误不在这里重试
            if attempt < self._max_retries:
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc
```

**为什么是 5 而不是 50**：

| 维度 | 计算 |
|------|------|
| DeepSeek 限制 | 5–10 QPS（账号级，所有任务共享） |
| 单次调用延迟 | 视觉推理 ~3–8 秒（含 image input） |
| Semaphore=5 | 实际 QPS ≈ 5 / 5 = 1 QPS，留足重试余量 |
| Semaphore=10 | 实际 QPS ≈ 2，正常但偶发抖动会触 429 |
| Semaphore=50 | 瞬时 50 个 in-flight，第 11 个起立即 429，全部重试 |

50 张图并发 ≠ 50 个 LLM 调用并发。`asyncio.create_task` 起 50 个任务，但这些任务在 `LLMCallPool.call` 处排队，真正同时打 LLM 的只有 5 个。其余 45 个任务挂在 Semaphore 上不消耗 LLM 配额，只占少量内存。

**调用点收口**：
- `capability/deepseek.py / openai_compat.py` 内部走 `LLMCallPool.call`，不暴露原始 client
- `ReActEngine` 不感知限流，直接 `await llm_provider.complete(...)` 即可
- `LLMCallPool` 单例注入到 `CapabilityDeps`（Phase 1b）

**配置项**（`config/llm.yaml`）：
```yaml
llm:
  provider: deepseek
  pool:
    max_concurrent: 5      # 生产环境根据账号 QPS 调整
    max_retries: 3
    base_delay: 1.0
    max_delay: 30.0
```

**与 OnFailAction 的边界**：
- `LLMCallPool` 只处理传输层错误（429/503/网络断开）
- Schema 解析失败、structured output 不合规 → `OnFailAction.REASK` 在 ReAct 层处理（§17.4），不走 Pool 重试

**MCP 标注工具在 ReAct Loop 中的调用**：

```
LLM 看到图片 → ReAct Loop 自主决定:

迭代1: Think: "我需要先检测缺陷位置"
       Act:  detect_defects(image_id="img1")  ← MCP 工具
       Observe: [{box:[x,y,w,h], score:0.92, class:"defect"}, ...]

迭代2: Think: "检测到3个缺陷，需要标注具体类型"
       Act:  annotate_label(image_id="img1", boxes=[...],
             labels=["待判定","待判定","待判定"])
       Observe: annotation_id="ann-001", status="draft"

迭代3: Think: "根据图片特征和标准，判定为2个气孔1个夹渣"
       Act:  update_annotation(annotation_id="ann-001",
             labels=["气孔","气孔","夹渣"])
       Observe: annotation updated

迭代4: Think: "标注完成，推送前端供用户确认"
       Act:  request_confirmation(question="img1标注3个缺陷，请确认")
       Observe: 等待用户反馈 (非阻塞，系统继续处理其他图)

注意: 以上步骤是 LLM 自主选择的，不是架构规定的
LLM 可能: 先搜标准再检测、直接看图判定不调检测工具、...
```

### 6.4 动态工作流设计 — LLM 实时设计

LLM 不仅能执行预定义任务，还能根据业务临时需求设计和更新工作流。

```
┌──────────────────────────────────────────────────────────────────────┐
│                    动态工作流设计                                     │
│                                                                      │
│  用户: "我们需要对这批容器做 100% 射线检测，设计一个方案"            │
│                                                                      │
│  LLM 自主规划:                                                       │
│  ┌───────────────────────────────────────────────────────────────┐   │
│  │ 1. Think: "100%射线检测，我需要了解规范要求"                  │   │
│  │    Act:  web_search("NB/T47014 射线检测 100% 要求")           │   │
│  │    Observe: "t≥20mm 压力容器需 100% RT"                       │   │
│  │                                                               │   │
│  │ 2. Think: "还需要看本批容器的具体情况"                        │   │
│  │    Act:  read_weldmap(case_id="batch-2026-06")                │   │
│  │    Observe: 22 台容器, t=22~38mm, Q345R+Q235B                │   │
│  │                                                               │   │
│  │ 3. Think: "设计检测方案"                                      │   │
│  │    Act:  design_workflow(                                     │   │
│  │            name="batch-rt-100pct",                            │   │
│  │            description="100%射线检测方案",                     │   │
│  │            steps=[                                            │   │
│  │              {step:"外观检查", tool:"visual_inspect"},         │   │
│  │              {step:"射线透照", tool:"rt_inspect"},             │   │
│  │              {step:"底片评定", tool:"film_evaluate"},          │   │
│  │              {step:"出具报告", tool:"generate_report"}         │   │
│  │            ])                                                 │   │
│  │    Observe: workflow_created, id="wf-rt-001"                  │   │
│  │                                                               │   │
│  │ 4. Think: "方案设计完成，需要人工确认后启动"                  │   │
│  │    Act:  request_confirmation(question="方案: ...，确认？")    │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  用户确认后:                                                         │
│  Gateway.notify_workflow_trigger() → Temporal 启动工作流             │
│                                                                      │
│  工作流运行中可动态更新:                                             │
│  用户: "第 8 台容器发现裂纹，后续需要增加 UT 复检"                  │
│  LLM: 分析裂纹情况 → 更新工作流 → 增加 UT 步骤                     │
│  → design_workflow 更新 wf-rt-001 → Temporal 继续执行新步骤         │
│                                                                      │
│  关键: 工作流是 LLM 设计的，不是预定义模板                          │
│  工作流可以实时更新，不是固定不变的                                  │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

**工作流存储与版本**：

```
Memory.write(
  content="100%射线检测方案: batch-rt-100pct",
  memory_type="workflow_template",
  tags=["射线检测","100%","Q345R"],
  promotion_status=VALIDATED
)

下次类似场景:
Memory.search("射线检测方案") → 命中历史方案
→ 注入 prompt: "历史方案参考: batch-rt-100pct"
→ LLM 参考历史方案 + 当前情况 → 设计新方案
→ 不从零开始，站在历史经验上
```

### 6.5 反馈学习

```
T1: 上传图A → LLM判断"夹渣"
T2: 用户修正"这是气孔" → Memory.write(correction, source=operator_X, RAW)
    （correction 不直接晋升 VALIDATED，待质量工程师确认；但当前操作员下次推理仍能看到自己的修正）
T3: 上传图B → Memory.search(特征) → 命中修正 → 注入prompt → LLM判断"气孔"
```

### 6.6 上下文管理 — 判定、理解、压缩三段策略

> **为什么单独成章**: 用户反馈（尤其是勾画+文字的多模态反馈）本质是**高密度上下文填充**。如果只往 LLM 上下文里塞而不管理，会出现两种灾难：(1) token 爆炸——50 张图每张画 3 个框 + 100 字，几轮就撑爆；(2) 语义丢失——ContextCompactor 把"用户在 img1 (120,80) 画框写'这是裂纹不是夹渣'"压成"用户改了 img1"，反馈白学。上下文管理必须是显式策略，不是事后补丁。

LLM 能看到什么由架构决定（§0 核心哲学第三条）。本节定义上下文从"产生 → 理解 → 压缩"的完整策略。

#### 第一段：判定 — 什么进上下文，按相关性评分 + token 预算筛选

上下文来源有 5 类，不是全塞，按相关性评分筛选：

```
┌──────────────────────────────────────────────────────────────────┐
│  上下文来源                    │ 相关性 │ 处理                   │
├────────────────────────────────┼────────┼────────────────────────┤
│  System prompt                 │  1.0   │ 必留 (Persona+工具白名单)│
│  (Persona + 安全规则 + 工具表)  │        │                        │
│                                │        │                        │
│  当前任务的 EventLog           │  1.0   │ 必留 (当前 ReAct 迭代链) │
│  (thinking/tool_call/result)   │        │                        │
│                                │        │                        │
│  当前任务的用户反馈             │  1.0   │ 必留 (含勾画+文字)      │
│  (刚收到的 feedback)           │        │                        │
│                                │        │                        │
│  Memory.search 命中            │  0.9   │ 必留 (历史修正案例)     │
│  (source=operator 的高置信)    │        │                        │
│                                │        │                        │
│  同批次其它任务的反馈摘要       │  0.7   │ 压缩后留 (只留判定结论) │
│  (img1-30 已完成的修正)        │        │                        │
│                                │        │                        │
│  Memory.search 命中            │  0.6   │ 仅元数据 (标题+标签)    │
│  (工作流模板、低相关项)         │        │                        │
└──────────────────────────────────────────────────────────────────┘

token 预算分配 (ReAct 主循环走 DeepSeek-Chat 32K；多模态调用走 Volc Doubao-Vision-Pro-32K 独立预算，不进 ReAct 上下文):
  system prompt:        2K   (固定)
  当前任务 EventLog:    8K   (当前 ReAct 迭代的完整链)
  当前任务用户反馈:      8K   (含勾画 image_ref + 结构化文本)
  Memory 命中:          4K   (历史修正 + 模板元数据)
  同批次摘要:           2K   (压缩后)
  LLM 输出余量:         8K   (thinking + tool_call)
  ──────────────────────────
  总计:                32K
```

预算超限时，按相关性从低到高砍：先砍 0.6 的元数据，再砍 0.7 的摘要，最后才动 0.9 的 Memory 命中。**当前任务相关项（1.0）永不砍**。

> **注**：ReAct 主循环用 DeepSeek-Chat（文本推理）；多模态调用通过 `vision_complete` 走 Volc Doubao-Vision-Pro-32K，独立预算不进 ReAct 上下文。预算配置在 `config/context_budget.yaml`，不在代码硬编码。

#### 第二段：理解 — 多模态反馈转结构化，不直接塞 LLM

勾画+文字反馈不能直接以原始 image_url + text 塞进 LLM 上下文——那会让 LLM 自己去"看懂画框对应哪条标注"，不可靠。先用 `ContextUnderstanding` 模块转结构化：

```python
# memory/context_understanding.py（阶段 4 引入）

class ImageAnnotationFeedback:
    """用户勾画+文字反馈的结构化表示"""
    target_image: str                    # "img1"
    target_region: BoundingBox           # 从 drawing 提取 (120,80)-(200,160)
    target_label_was: str                # 从 EventLog 查: "夹渣"
    user_corrected_to: str               # 从 text 提取: "裂纹"
    user_reasoning: str                  # 从 text 提取: "边缘平直、长度>5mm"
    evidence_drawing: ImageRef           # 保留原图引用 (img1_annotated.png)

class ContextUnderstanding:
    """把原始多模态反馈转结构化对象"""

    async def understand(
        self,
        raw_feedback: RawFeedback,       # image_url + drawings + text
        event_log: EventLog,             # 查 target_label_was
    ) -> ImageAnnotationFeedback:
        # 1. 从 drawings 提取 BoundingBox (前端已有坐标，无需 LLM)
        # 2. 从 EventLog 查该区域之前的标注
        # 3. 从 text 提取 corrected_to + reasoning (轻量 LLM 调用或规则解析)
        # 4. 保留 evidence_drawing 作为原图引用
        ...
```

**理解后的结构化对象**才进 Memory + 上下文。原始 image_url 只作为 `evidence_drawing` 引用保留，不重复塞 token。

关键约束：
- `user_reasoning`（"为什么改"）是反馈学习的核心信号——**提取时宁可有噪声不可丢失**
- `target_label_was` 必须从 EventLog 查证，不能靠 LLM 猜——否则反馈学习会学错
- 如果 text 解析不出 corrected_to，标记 `needs_clarification` 走 `request_clarification` 工具反向问用户

#### 第三段：压缩 — 按语义密度分级，不按时间

ContextCompactor 按"语义密度"决定压缩顺序，**不是按"旧消息先压"**。语义密度 = 信息量 ÷ token 占用：

```
┌──────────────────────────────────────────────────────────────────┐
│  压缩优先级 (从先压到最后压):                                     │
├──────────────────────────────────────────────────────────────────┤
│  优先级 1 (先压): 工具调用元数据                                  │
│    原始: detect_defects(img1, threshold=0.5) → [box1, box2, ...] │
│    压缩: "detect_defects 调用 1 次，检出 2 个缺陷"                │
│    理由: 元数据可从 EventLog 重放恢复，LLM 不需要每次看完整参数   │
│                                                                  │
│  优先级 2: 已确认任务的 thinking 链                               │
│    原始: img1-30 的完整 ReAct thinking (每张 4 轮迭代)            │
│    压缩: "img1-30: 全部标注完成，主要缺陷类型: 气孔(18) 夹渣(12)" │
│    理由: 历史思考过程对当前任务相关性低，结论足够                  │
│                                                                  │
│  优先级 3: Memory 命中的低相关项                                  │
│    处理: 直接丢弃 (相关性 0.6 的项不进压缩，直接不注入)           │
│    理由: 低相关项压缩后语义损失大，不如不注入                     │
│                                                                  │
│  优先级 4: 用户反馈的结构化摘要                                   │
│    原始: {target: img1, region: ..., was: "夹渣", to: "裂纹",     │
│           reasoning: "边缘平直、长度>5mm", evidence: image_ref}   │
│    压缩: "img1 (120,80)-(200,160): 夹渣→裂纹 (边缘平直、长度>5mm)"│
│    理由: 保留判定结论 + reasoning，丢 evidence_drawing 的 image_ref│
│                                                                  │
│  优先级 5 (最后压): 当前任务的原始反馈                            │
│    处理: 永不压缩                                                 │
│    理由: 当前任务上下文是 LLM 推理的唯一依据，压缩=自杀           │
└──────────────────────────────────────────────────────────────────┘
```

**不可压缩的红线**（无论 token 多紧张都不动）：
1. `user_reasoning`（用户为什么改）—— 反馈学习核心信号
2. `target_label_was` → `user_corrected_to`（改前改后的标签对）—— 学习目标
3. 当前 ReAct 迭代的最后一次 tool_result —— LLM 决策的直接依据
4. System prompt 中的安全规则 —— 架构约束不可丢

#### 引入时机

| 阶段 | 上下文管理能力 |
|------|--------------|
| 阶段 0-2 | 无压缩，token 不紧张（纯文本 + 单图） |
| 阶段 3 | 基础 compaction.py：按 token 简单截断（LastN 策略） |
| 阶段 4 | ContextUnderstanding 模块：勾画反馈转结构化；按相关性评分筛选 |
| 阶段 5+ | 语义密度分级压缩：完整三段策略上线 |

阶段 4 是关键节点——反馈学习上线时如果没有 ContextUnderstanding，多模态反馈会直接以原始形式塞 LLM，token 爆炸 + 语义丢失同时发生。

### 6.7 L1↔L2 工作流协作 — 标注场景的完整链路

> **为什么单独成章**: §6.3 数据标注场景只覆盖了 L1 内部路径（ReAct → MCP 标注 → WebSocket 推前端）。但工业场景的真实链路是"用户上传 → LLM 写工作流 → Temporal 跑 activity → activity 调 L4 标注工具 → 结果回认知层和前端 → 用户反馈 → 工作流自身迭代"。这条链路穿越 L1/L2/L3/L4 四层，接口必须显式设计，否则各层各自为政。

#### 执行模型区分 — 何时走 L1 ReAct，何时触发 L2 Temporal

LLM 在 ReAct 内自主决定走哪条路径——架构不规定"批量必须走 L2"：

| 场景 | LLM 选择 | 理由 |
|---|---|---|
| 单张图、需要 LLM 看图推理 | L1 ReAct 直接调 MCP 标注工具 | LLM 决策驱动，少量任务 |
| 批量 50 张图、规则明确 | 调 `trigger_batch_annotation` 工具触发 L2 | L1 ReAct 并发不足以表达任务依赖，且无需每张图都 LLM 推理 |
| 批量 + 部分需要 LLM 介入 | L2 跑批量，特定 activity 内回调 L1 ReAct | 混合模式，L2 编排 + L1 推理 |

`trigger_batch_annotation` 是 LLM 可调的内部工具，**LLM 自主决定何时用**——这是 LLM 自主权的边界，架构不替 LLM 决定"该走批量了"。

#### 五条接口

**接口 0：LLM 决定触发 L2（L1 内部）**

```python
# LLM 在 ReAct 内调 trigger_batch_annotation 工具
await tools.trigger_batch_annotation(
    image_ids=["img1", ..., "img50"],
    annotation_config={"label_schema": [...], "priority": "ROUTINE"},
    reason="50 张图批量标注，规则明确，无需逐张推理"
)
# 工具内部调 Gateway.notify_workflow_trigger
```

**接口 1：L1 → L2 触发工作流（已预留，复用）**

复用 `CognitiveGatewayWritePort.notify_workflow_trigger`，Bridge 层翻译成 Temporal `start_workflow`：

```python
# Gateway WritePort 已有接口
gateway.notify_workflow_trigger(
    case_id=case_id,
    config={
        "workflow_type": "annotation",
        "image_ids": [...],
        "annotation_config": {...}
    }
)
# Bridge.workflow_launcher 翻译成 temporal_client.start_workflow(...)
```

**接口 2：L3 Activity → L4 MCP 标注工具（关键：共享工具层）**

L3 Activity 直接调 L4 MCP 标注工具——**与 L1 LLM 调同一份工具实现**（§13 分层约束）。人工等待通过 Temporal Signal 处理，不用轮询：

```python
# L3 Activity 定义（Temporal workflow code）
@workflow.defn
class AnnotationWorkflow:
    @workflow.run
    async def run(self, config: AnnotationConfig) -> AnnotationResult:
        results = []
        for image_id in config.image_ids:
            # Activity A: 提交标注任务到 L4 MCP 工具
            task_id = await workflow.execute_activity(
                submit_annotation_activity,
                image_id,
                start_to_close_timeout=timedelta(seconds=30),
            )
            # Workflow 挂起，等 Signal（人工标注完成）
            annotation_result = await workflow.wait_condition(
                lambda: self._results.get(task_id) is not None
            )
            results.append(annotation_result)
        return AnnotationResult(results=results)

    @workflow.signal
    async def annotation_completed(self, task_id: str, result: dict):
        """标注系统完成后发 Signal，Workflow 自动恢复"""
        self._results[task_id] = result

# Activity 内部调 L4 MCP 工具（与 L1 LLM 共享）
async def submit_annotation_activity(image_id: str) -> str:
    # 调 L4 MCP 标注工具，与 L1 ReAct 调的是同一个 annotate_label
    mcp_tool = tool_registry.get_tool("annotate_label")
    result = await mcp_tool.execute(image_id=image_id, ...)
    return result.task_id
```

**关键约束**：
- L3 Activity 和 L1 LLM 调同一个 `annotate_label` 工具，工具实现只有一份（DRY）
- 工具不感知调用方——MCP 协议无状态，L1 走 ReAct、L3 走 workflow 编排是调用上下文的区别
- Hook 拦截对两种调用方都生效——L3 调工具也过 SafetyHook + PolicyHook

**接口 3：L2 完成 → L1 感知（通过 WeldMap，不用 NATS 双写）**

L2 不直接调 L1（架构约束：Brain 决策必须通过 Gateway 写出，反向也成立）。正确路径：

```
Temporal Workflow 完成
    → 写入 WeldMap annotation 分区（single source of truth）
    → WeldMap 写入后自动广播事件（NATS 由 WeldMap 发，不是 Temporal 发）

L1 感知有两种方式：
  方式 A (主动读): L1 LLM 在 ReAct 内调 read_weldmap 工具
  方式 B (被动通知): L1 订阅 NATS subject "weld.annotation.completed"
                    → feedback_consumer 接收 → Memory.write(annotation_result)
                    → 主循环下轮 Memory.search 命中
```

**关键约束**：**数据源唯一（WeldMap），NATS 只是通知机制，不是第二条数据通道**。Temporal 只写 WeldMap，NATS 事件由 WeldMap 自己广播——避免双写一致性陷阱。

**接口 4：前端修正反馈穿越三层**

```
前端用户修改标注 (勾画 + 文字)
    → WebSocket feedback 消息（§5.1 已定义格式）
    → AgentLoop feedback_consumer 接收
    → 三步处理:
        步骤1: Memory.write(correction, VALIDATED)   ← 即时学习生效（M1 内）
        步骤2: 判断是否需要回写标注系统
               - 如果是 L1 ReAct 标注 → LLM 下轮调 update_annotation 工具
               - 如果是 L2 Temporal 标注 → 触发轻量 correction_workflow
        步骤3: 修正完成后发 NATS subject "weld.annotation.corrected"
               → L1 订阅 → 更新前端进度
```

**轻量 correction_workflow**（如果原标注是 L2 跑的）:

```python
@workflow.defn
class AnnotationCorrectionWorkflow:
    @workflow.run
    async def run(self, correction: AnnotationCorrection) -> dict:
        # 调 L4 MCP update_annotation 工具
        await workflow.execute_activity(
            update_annotation_activity,
            correction.task_id,
            correction.corrected_label,
        )
        return {"status": "corrected"}
```

#### 工作流级学习 — 工作流模板入 Memory

工作流不只是"LLM 设计一次就完事"，要能基于反馈迭代提升：

```
工作流跑完 → 收集所有用户反馈 + 执行日志
    → LLM 在新一轮 ReAct 看反馈 → 生成 v2 工作流
    → v2 作为 memory_type=workflow_template 写入 Memory
    → 下次类似任务: LLM 调 design_workflow 时先 Memory.search("类似任务的工作流模板")
    → 命中 v2 → 基于 v2 修改而不是从零设计
```

Memory 类型扩展：
- `memory_type=annotation_result`：单次标注结果
- `memory_type=annotation_correction`：用户修正
- `memory_type=workflow_template`：工作流模板（含 steps、成功率、适用场景）

#### 引入时机

| 接口 | 引入阶段 | 说明 |
|---|---|---|
| 接口 0 (LLM 决定触发) | 阶段 5 | `trigger_batch_annotation` 工具随 L2 接入引入 |
| 接口 1 (L1→L2 触发) | 阶段 5 | 复用 notify_workflow_trigger，Bridge 落地 |
| 接口 2 (L3→L4 + Signal) | 阶段 5+ | Temporal Signal 机制阶段 5+ 才有 |
| 接口 3 (L2→WeldMap→L1) | 阶段 5+ | WeldMap L2 事件溯源就绪后 |
| 接口 4 (前端反馈穿越) | 阶段 3 (L1 部分) + 阶段 5+ (L2 部分) | L1 反馈链路阶段 3 即有；L2 回写部分阶段 5+ |
| 工作流模板入 Memory | 阶段 6 | Memory 类型扩展在工作流主体域阶段 |

阶段 3-4 不需要这套接口——那时所有标注都走 L1 ReAct，没有 L2。**提前实现这套接口是过度设计**。

#### 已知留白（落地时回填）

- **NATS subject 命名规范**：`weld.annotation.completed` / `weld.annotation.corrected` 是草案，正式 schema 在阶段 5+ 引入 NATS 时统一定义。当前阶段不写正式 schema，避免过早设计。
- **Temporal Workflow ID 命名规范**：`annotation_<case_id>_<timestamp>` 是草案，正式规范在阶段 5+ 落地。
- **L4 工具的 L1/L3 调用上下文区分**：当前 ToolRegistry 不区分调用方，阶段 5+ 引入 L2 后可能需要加 `caller_context` 字段记录调用来源（用于审计），但工具行为不变。

---

## 七、CognitiveDependencies

```python
@dataclass
class CapabilityDeps:
    """L1 平面内的纯能力 Provider（无副作用、可替换）。
    MCP 工具不在这里——它是 L4 工具实现，归 adapters/mcp/，通过
    ControlDeps.mcp_registry 注册到 ToolRegistry 供 LLM 调用。
    """
    llm_provider: LLMProvider | None = None
    web_search: WebSearchProvider | None = None

@dataclass
class ControlDeps:
    orchestrator: BrainOrchestrator | None = None
    decision_repo: BrainDecisionRepository | None = None
    mcp_registry: MCPRegistry | None = None  # 注册 adapters/mcp/ 下的 MCPAdapter 到 ToolRegistry

@dataclass
class KnowledgeDeps:
    rag_query: RAGQueryPort | None = None
    standards_query: StandardsQueryPort | None = None
    case_library: CaseLibraryQueryPort | None = None
    process_knowledge: ProcessKnowledgePort | None = None

@dataclass
class MemoryDeps:
    search: MemorySearchPort | None = None
    read: MemoryReadPort | None = None
    write: MemoryWritePort | None = None
    promotion: MemoryPromotionPort | None = None
    archive: MemoryArchivePort | None = None
    confidence: MemoryConfidencePort | None = None

@dataclass
class GatewayReadDeps:
    """读 WeldMap — 无需验证，Control 层可拿。

    read_weldmap 工具直接用这个 dep。
    """
    read: CognitiveGatewayReadPort | None = None

@dataclass
class GatewayWriteDeps:
    """写 WeldMap — 必须过验证管道。

    **关键约束**: 只注入到 Gateway 写出路径内部，Control 层拿不到裸 WritePort。
    WritePort 内部按操作类型路由不同验证策略:
    - 决策类 (publish_decision/escalation/instruction): 完整 4 阶段验证
    - 通知类 (notify_workflow_trigger): 轻量 schema 校验 (非 Brain 决策，不需 SafetyHook 重判)
    """
    write: CognitiveGatewayWritePort | None = None
    # ValidationPipeline 归 Gateway 写出路径，不归 Governance 横切层
    # (只在 publish_decision 时触发，不是每次工具调用都过)
    validation: ValidationPipelinePort | None = None

# GatewayDeps 保留作为容器，但拆成 read/write 两个子组——
# 这样 Control 层可以只拿 GatewayReadDeps，拿不到 GatewayWriteDeps，
# 从类型上阻断"绕过验证管道直接写 WeldMap"的代码路径。
@dataclass
class GatewayDeps:
    read: GatewayReadDeps | None = None
    write: GatewayWriteDeps | None = None  # 仅 Gateway 写出路径内部使用

@dataclass
class GovernanceDeps:
    # Governance 只持有真正的横切关注点:
    # - Hook 拦截 (SafetyHook/PolicyHook) — 在 ControlDeps 中注入
    # - ToolPolicy — 在 ControlDeps 中注入
    # - 审批/升级/审查/失败策略
    review_repo: HumanReviewRequestRepository | None = None
    learning_repo: LearningEventRepository | None = None
    escalation: EscalationTracker | None = None
    # 注: ValidationPipeline 已移到 GatewayWriteDeps (见上)

@dataclass
class CognitiveDependencies:
    capability: CapabilityDeps
    control: ControlDeps
    knowledge: KnowledgeDeps
    memory: MemoryDeps
    gateway: GatewayDeps
    governance: GovernanceDeps
```

**注入边界约束**（编译期类型守护）：

```python
# composition root 构造时:
gateway_read = GatewayReadDeps(read=WeldMapReadGateway(...))
gateway_write = GatewayWriteDeps(
    write=WeldMapWriteGateway(...),
    validation=ValidationPipeline(...),
)
gateway = GatewayDeps(read= gateway_read, write= gateway_write)

# 注入到 Control 层时——只给 read，不给 write:
control = ControlDeps(
    ...,
    gateway_read= gateway_read,    # ✅ Control 能读 WeldMap
    # gateway_write= 不传           ✅ Control 拿不到裸 WritePort
)

# 写出只在 Gateway 内部:
# gateway/write_gateway.py 内部:
class WeldMapWriteGateway:
    def __init__(self, write_port, validation):
        self._write = write_port
        self._validation = validation

    async def publish_decision(self, decision):
        result = self._validation.validate(decision)  # 必过验证
        if not result.ok:
            return PublishResult(rejected=True, reason=result.reason)
        return await self._write.publish_decision(decision)
```

> **类型守护的意义**: Python 没有真正的私有/可见性，但通过把 WritePort 包在 `GatewayWriteDeps` 里、且只在 Gateway 内部组装，任何 Control 层代码要拿 WritePort 都得显式穿过 Gateway 写出路径——编译期类型检查 + 代码评审可以拦住绕过验证的写出。

---

## 八、Adapters 适配层

```
adapters/
├── database/                  # PostgreSQL
│   ├── engine.py              # AsyncDatabaseEngine
│   ├── models.py              # ORM (6 表)
│   ├── decision_repo.py       # BrainDecision
│   ├── memory_repo.py         # Memory (M2-M5)
│   ├── knowledge_repo.py      # Knowledge
│   ├── workflow_repo.py       # WorkflowState
│   ├── review_repo.py         # HumanReview
│   └── audit_repo.py          # AuditEntry (M5)
├── cache/
│   └── redis_client.py        # Redis M0/M1
├── storage/
│   └── minio_client.py        # MinIO
├── weldmap/
│   ├── weldmap_http.py        # WeldMap HTTP 客户端 (Gateway 委托给它)
│   └── event_sourcing.py      # CAS + Materialized Views
├── retrieval/                 # 检索算法原语 (Knowledge + Memory 共享)
│   ├── vector_index.py        # 向量索引 (pgvector → Milvus 阶段 5+)
│   ├── fulltext.py            # FTS 全文检索
│   ├── rrf.py                 # RRF 融合 (k=60, weights 0.5/0.5)
│   └── reranker.py            # Reranker 重排序 (阶段 5+ 引入)
├── mcp/                       # MCP 外部工具适配器
│   ├── label_studio.py        # Label Studio 标注工具
│   ├── detection_api.py       # 目标检测模型 API
│   └── base.py                # MCPAdapter ABC
├── observability/
│   ├── tracing.py             # OpenTelemetry
│   └── metrics.py
└── migrations/                # Alembic
```

---

## 九、技术栈

| 层级 | 技术 | 用途 | 状态 |
|-----|------|------|------|
| L1 | DeepSeek API | 通用推理 | ✅ 已接入 |
| L1 | Volc API | 多模态视觉 | ✅ 已配置 |
| L1 | DuckDuckGo Lite | Web搜索(零配置) | ✅ 已接入 |
| L1 | Tavily | Web搜索(高质量) | ✅ 代码就绪 |
| L1 | FastAPI | REST + WebSocket | ✅ 已接入 |
| L1 | MCP (Model Context Protocol) | 外部工具集成 | 🔜 阶段 3 |
| L1 | Label Studio MCP | 数据标注工具 | 🔜 阶段 3 |
| L1 | 检测模型 MCP | 目标检测 API | 🔜 阶段 3 |
| L2 | Temporal | 工作流编排 | 🔜 阶段 5+（见下方引入条件）|

**L2 Temporal 引入条件**（明确"🔜"的含义）:

L2 不是按时间表引入，是按**信号驱动**引入。满足以下任一条件才上 Temporal：

| 触发信号 | 说明 |
|---|---|
| 批量规则驱动的多步骤编排 | 50+ 张图标注、批量报告生成等场景，L1 ReAct 并发（asyncio + Semaphore）不足以表达任务依赖（A 完成才能 B、C 并行后等 D）|
| 需要可恢复的长流程 | 跨日/跨班次的标注任务，断电/重启后能从断点续跑 |
| 需要 human-in-loop 工作流 | 审批节点、质量工程师签字等正式流程，要可审计可重放 |
| L1 与外部规则系统协作 | WeldMap L2 事件溯源就绪，需要 Bridge 把 L1 决策翻译成 Temporal Signal |

**阶段 3 不需要 Temporal**：阶段 3 的"人机反馈 + 检测/标注 MCP"走 AgentLoop feedback_consumer 双任务模型（§5.1），人工等待通过 Memory 通信实现，不依赖 Temporal Signal。强行用 Temporal 处理阶段 3 的人工等待是过度设计。

**阶段 5+ 引入 Temporal 后**，原 AgentLoop feedback_consumer 不删——它仍然负责 L1 ReAct 内部的实时反馈；Temporal 负责跨任务/跨会话的工作流编排。两者并存，职责不同。
| L5 | PostgreSQL | 持久化 | 🔜 替换内存 |
| L5 | pgvector | 向量搜索 | 🔜 知识库K4 |
| L5 | Redis | L0/L1缓存 | 🔜 替换内存 |
| L5 | MinIO | 图片存储 | 🔜 图片场景 |
| L6 | NATS JetStream | 事件总线 | 🔜 替换内存 |
| Observability | OpenTelemetry | Tracing+Metrics | 🔜 生产部署 |
| Observability | Langfuse | LLM可观测 | 🔜 替换tracking |

---

## 十、关键设计决策

| 决策 | 选择 | 理由 |
|-----|------|------|
| 核心循环 | ReAct Loop | LLM 自主决定每一步做什么，不是流水线填空 |
| 角色模型 | 单Agent + 自主切换 | LLM 通过 switch_role 工具切换，不是架构强制顺序 |
| 角色分阶段 | 工具白名单随系统成长扩充 | Explorer 先有 web_search，后接入知识库/记忆库 |
| 推理深度 | Persona (Planner/Copilot/CAA) | LLM 调 design_workflow 时自动 Planner，不是架构规定角色 |
| 成本控制 | ReasoningMode (ROUTINE/ADAPTIVE/EXPLORATORY) | 架构根据 Memory 命中率自动选择，不是 LLM 选择 |
| 图像精度 | Thumbnail 默认入场 + LLM 主动升级 | 架构给低成本默认值，LLM 通过 request_image_detail 工具自主升级，不替 LLM 决定"看不看清" |
| 图片处理 | 多模态消息走 ReAct | 不建旁路，LLM 看图后自主决定用什么工具 |
| 决策格式 | DecisionFactory 15 种输出类型 | LLM 自由决定内容，架构决定格式 |
| 状态追踪 | 12-state BrainStateMachine | 追踪 LLM 行为用于审计/超时检测，不控制 LLM 行为 |
| 学习机制 | Few-Shot Context | Memory 检索 + prompt 注入，不重新训练 |
| 反馈学习 | 非阻塞 Memory.write | 系统不等用户改完一张图才处理下一张 |
| 外部工具 | MCP 协议 | Label Studio、检测模型等通过 MCP 动态接入 |
| 工具三层 | Tool/MCP/Skill | LLM 统一视角，不区分来源；Skill 可选不强制 |
| 启动上下文 | WELDEVENT.md/OPERATOR.md/CASE_BRIEF + Memory 注入 | 借鉴 Claude Code CLAUDE.md，提供世界观不规定步骤 |
| 任务管理 | manage_plan 工具 | 借鉴 Claude Code TodoWrite，LLM 自主决定要不要拆任务 |
| 并行调查 | spawn_investigator 工具 | 借鉴 Claude Code Task tool，LLM 自主派生隔离上下文子调查员 |
| 工作流设计 | LLM 动态设计 | 不是预定义模板，LLM 根据业务需求实时设计/更新 |
| 知识组织 | 4 主体域 | 焊接/检测/质量/设备，按域组织，支持跨域关联 |
| 写出通道 | CognitiveGateway 单一通道 | 强制一致性，验证是守门人不是步骤 |
| 验证失败 | OnFailAction 5 种策略 | 不同验证失败有不同策略，不是简单报错 |
| 工具调用 | Hook 拦截 + Policy | 安全边界，不是流程控制 |
| 决策回滚 | Checkpoint + EventLog | 支持回滚和审计，LLM 被拒绝后可回退重推理 |
| 并发安全 | Event Sourcing + CAS | WeldMap 写出原子更新，防止并发冲突 |
| Web 搜索 | DuckDuckGo + Tavily | 零配置可用，有 key 升级 |
| 记忆管理 | M0-M5 分层 + 晋升 | 完整记忆层次，修正案例直接 VALIDATED |
| 人机协作 | Human-Governed | Agent 建议，Human 决策 |
| L1→L2 | Bridge (EventConnector + DecisionTranslator + WorkflowLauncher) | BrainDecision → Temporal 工作流 |
| DI 模式 | CognitiveDependencies | 6组依赖，编译时类型检查 |

---

## 附录 A、Control 层技术细节 (按阶段渐进引入)

以下内容来自旧方案，用新哲学重新定位：这些是 LLM 运行的**架构基底**，不是流水线步骤。LLM 自主决定做什么，但做出来的东西需要有状态追踪、决策结构、成本控制。

> **避免一次造完**: 每个子节标注"阶段 X 引入"，区分**当下必需** vs **未来引入**。专家批评的"过度前瞻"主要发生在把 12 状态机/15 决策类型/L0-L5 一次造满——本节用阶段映射切分，按需要才长出来。

| 子节 | 引入阶段 | 当下必需性 |
|---|---|---|
| A.1 BrainStateMachine | 阶段 3 (LLM 介入) | 阶段 0-2 用 IDLE/RUNNING/DONE 三态足够；12 态在阶段 5+ 才补全 |
| A.2 Persona + ReasoningMode | 阶段 4 (多角色协作) | 阶段 3 单 Persona 即可；ReasoningMode 在阶段 4 与 Persona 同阶段引入（见 §A.2 行 1106、2656）|
| A.3 图像渐进获取 | 阶段 2 (LLM 看图) | 阶段 0-1 不送图给 LLM；request_image_detail 在 5+ 引入 |
| A.4 DecisionFactory | 阶段 3 | 起步只有 1-2 种 DecisionOutput；15 种类型按角色逐个引入 |
| A.5 Planner/Reflector/SubAgent | 阶段 6+ | 阶段 3-5 不需要深度推理；spawn_investigator 是 SubAgent 的轻量版 |
| A.6 EventLog | 阶段 3 (审计要求) | 起步即引入，不可省 |
| A.7 Checkpoint | 阶段 5+ (有回滚需求) | 早期靠 EventLog 重放即可 |
| A.8 OnFailAction | 阶段 4 (验证失败需多策略) | 阶段 3 直接 ESCALATE 即可 |
| A.9 Event Sourcing + CAS | 阶段 5+ (并发写) | 早期单写者无需 CAS |
| A.10 Bridge (L1→L2) | 未来规则系统引入时 | 当前阶段不引入规则触发工作流，等 L2 数据平面就绪 |
| A.11 ApprovalService | 阶段 4 (CRITICAL/URGENT 出现) | 阶段 3 全部 ESCALATE 给人 |

阅读建议：第一次落地只实现"阶段 X 引入"=当前阶段号或更早的子节，其它做接口预留即可。

### A.1 BrainStateMachine — ReAct Loop 的状态追踪

> **定位声明**: 状态机是**观察者**而非约束者。状态由 LLM 行为派生（"LLM 调了 search_standards" → 标记 KNOWLEDGE_RETRIEVAL），而不是反过来约束 LLM "现在该 KNOWLEDGE_RETRIEVAL 了"。状态转换不限制 LLM 下一步可调什么工具——LLM 始终可以从任何状态跳到任何状态。

状态机存在的唯一目的：**给 SupervisorCenter / EventLog / 前端 UI 一个可读的"LLM 现在在做什么"信号**。删掉它系统照样跑，只是看不见。

#### 分阶段引入（避免一开始就 12 态）

| 阶段 | 状态数 | 状态集 | 引入原因 |
|---|---|---|---|
| 阶段 0-2 | 不需要状态机 | — | LLM 系统早期阶段，对话流简单，无需状态追踪 |
| 阶段 3 | 3 态 | IDLE / RUNNING / DONE | LLM 介入，前端要显示"思考中" |
| 阶段 4 | 5 态 | + WAITING_FEEDBACK / ERROR | 引入人工确认和错误展示 |
| 阶段 5+ | 12 态 (完整) | 见下表 | 审计要求细粒度状态，前端展示工具类型 |

```
完整 12 态 (阶段 5+ 才需要):

IDLE                空闲，等待输入
OBSERVING           接收用户输入/图片
UNDERSTANDING       理解上下文 (LLM 内部)
KNOWLEDGE_RETRIEVAL 正在调用知识工具 (search_standards 等)
MEMORY_RETRIEVAL    正在调用记忆工具 (Memory.search)
MEMORY_MATCHING     记忆匹配 (Routine 模式自动跳过推理)
REASONING           LLM 推理中 (无工具调用的纯思考)
DECISION_GENERATION 生成决策输出
VALIDATION          决策写出前验证 (Gateway 守门人)
PUBLICATION         决策写入 WeldMap
WAITING_FEEDBACK    等待人工确认 (request_confirmation 后)
ERROR / ABORT       错误/中止
```

**状态派生规则**（架构观察、不强制）：

| LLM 行为 | 派生状态 |
|---|---|
| 调用 `search_*` 类工具 | KNOWLEDGE_RETRIEVAL |
| 调用 `Memory.search` | MEMORY_RETRIEVAL |
| 无 tool_call 的纯文本输出 | REASONING |
| 调用 `request_confirmation` 等待 | WAITING_FEEDBACK |
| 调用 `design_workflow` / `adjust_parameter` | DECISION_GENERATION → VALIDATION → PUBLICATION |
| Hook 拒绝 + LLM 重试 | 状态不变（仍记为发起时的状态）|

**状态机不允许做的事**:
- ❌ 不能拒绝 LLM 的工具调用（这是 Hook 的事）
- ❌ 不能强制 LLM 走某条路径（这是规则系统的事）
- ❌ 不能基于状态选择工具集（ToolPolicy 是工具白名单的事）

**状态机允许做的事**:
- ✅ SupervisorCenter 监控状态停留时间 → 超时检测
- ✅ EventLog 记录所有状态转换 → 审计追溯
- ✅ 前端订阅状态变化 → 进度可视化

#### SupervisorCenter 检测机制实现

SupervisorCenter 是 ReAct Loop 的旁路监控器，**不参与决策**，只负责检测异常并触发干预。三类检测：

**1. 循环检测（最常见）**

通过 EventLog 的 TOOL_CALL 事件回看最近 N 次工具调用：

| 模式 | 判定 | 处置 |
|---|---|---|
| 连续 3 次调同一工具 + 参数完全相同 | **确认循环** | 立即终止当前 ReAct，返回 `ReasoningLoopError` |
| 连续 5 次调同一工具（参数不同） | **疑似循环** | 触发 `request_clarification`，问 LLM "你在反复调 X，是否需要帮助？" |
| 连续 10 次任意工具调用无 DECISION 产出 | **空转** | 触发 `request_clarification`，问 LLM "你的目标是什么？" |

判定"参数完全相同"用 `json.dumps(args, sort_keys=True)` 哈希比较，避免 dict 顺序干扰。

**2. 超时检测**

每个 BrainState 有最大停留时间，超时即触发处置：

| 状态 | 超时阈值 | 处置 |
|---|---|---|
| OBSERVING | 60s | 强制进入 UNDERSTANDING |
| UNDERSTANDING | 120s | 触发 `request_clarification` 问 LLM 进度 |
| RUNNING (单工具调用) | 30s | 终止当前工具调用，记录 `ToolTimeoutError` |
| WAITING_FEEDBACK | 不超时 | 等真实 HumanGateSignal，不靠定时器（见 §A.1 闭环 4 修订） |
| ERROR | 30s | 强制 ESCALATED，进人工 |

**3. 健康检测**

定期（每 10 次工具调用）采样：

| 指标 | 阈值 | 处置 |
|---|---|---|
| LLM 调用成功率 | < 80% | 触发 `LLMUnavailableError` → 降级到 keyword 模式 |
| 工具失败率 | < 70% | 标记 case 不稳定，下个任务用 LLMTier-1 |
| Memory 写入延迟 P95 | > 2s | 告警，不中断（Memory 是异步的） |

**关键约束**：
- SupervisorCenter **只触发干预，不做决策**——所有处置最终都走 `request_clarification` 或 `escalate` 工具，由 LLM 或人决定下一步
- 检测到异常时**先记 EventLog，再触发干预**——审计轨迹完整
- 阈值通过 `config/supervisor.yaml` 配置，不在代码硬编码——不同部署环境可调

```python
# control/supervisor.py（阶段 3 引入）
class SupervisorCenter:
    def __init__(self, event_log: EventLog, config: SupervisorConfig):
        self._log = event_log
        self._cfg = config

    async def check_loop(self) -> LoopDetection | None:
        recent = self._log.query_last(event_type=TOOL_CALL, n=10)
        # 同工具同参数 ≥3 → 确认循环
        # 同工具任意参数 ≥5 → 疑似循环
        # 任意工具 ≥10 无 DECISION → 空转
        ...

    async def check_timeout(self, current_state: BrainState, entered_at: float) -> TimeoutDetection | None:
        max_dwell = self._cfg.max_dwell_seconds(current_state)
        if time.time() - entered_at > max_dwell:
            return TimeoutDetection(state=current_state, exceeded_by=time.time() - entered_at - max_dwell)
        return None
```

### A.2 Persona + ReasoningMode — 推理深度和成本控制

Persona 和 ReasoningMode 不是角色（角色是 Explorer/Vision/Quality/Operator），而是**推理深度和成本策略**。LLM 不需要显式切换——架构根据任务特征自动选择。

```
Persona (推理深度):

┌──────────────┬─────────────────────────────────────────────────────┐
│ Planner      │ 系统化规划：任务分解 → 子任务 → 执行计划             │
│              │ LLM 调用 design_workflow 时自动激活                  │
│              │ 输出: WorkflowRecommendation                        │
├──────────────┼─────────────────────────────────────────────────────┤
│ Copilot      │ 辅助推理：分析 → 建议 → 等待确认                    │
│              │ 默认模式，适合大多数场景                              │
│              │ 输出: ParameterRecommendation / Explanation          │
├──────────────┼─────────────────────────────────────────────────────┤
│ CAA          │ 自主行动：分析 → 决策 → 执行                        │
│              │ 高置信度 + 低风险操作时自动激活                      │
│              │ 输出: InvestigationDirective                         │
└──────────────┴─────────────────────────────────────────────────────┘

ReasoningMode (成本控制):

┌──────────────┬─────────────────────────────────────────────────────┐
│ ROUTINE      │ 记忆匹配优先：Memory.search 命中 → 直接复用         │
│              │ 跳过推理，节省 LLM tokens                            │
│              │ 适合: 已知缺陷模式、重复性检测                        │
├──────────────┼─────────────────────────────────────────────────────┤
│ ADAPTIVE     │ 上下文感知推理：thumbnail + 工具获取细节              │
│              │ LLM 推理 + 工具辅助                                  │
│              │ 适合: 大多数检测场景                                  │
├──────────────┼─────────────────────────────────────────────────────┤
│ EXPLORATORY  │ 深度推理：完整图像 + 多轮推理 + 广泛知识检索          │
│              │ 最高质量，最高成本                                    │
│              │ 适合: 新缺陷类型、复杂判定、首次遇到的情况            │
└──────────────┴─────────────────────────────────────────────────────┘

注意：ReasoningMode **不决定图像精度**。图像精度由 §A.3 单独定义：
架构始终以 Thumbnail 默认入场，LLM 通过 request_image_detail 工具自主升级。
ReasoningMode 只影响"是否调 Memory / 是否多轮推理 / 检索范围"，不动图像层。

自动选择逻辑 (架构决定，非 LLM 决定):
  Memory.search 命中且相似度 > 0.9 → ROUTINE
  已知缺陷类别 + 标准参数范围 → ADAPTIVE
  未知缺陷 / 首次遇到 / 用户要求深度分析 → EXPLORATORY
```

#### 三轴正交关系（消除 Persona / Role / ReasoningMode 混淆）

系统存在三个独立维度，**两两正交**，由不同主体选择：

| 维度 | 含义 | 谁选 | 选择依据 |
|---|---|---|---|
| **AgentRole** | 工作模式（工具白名单 + system prompt 风格） | LLM | LLM 通过 `switch_role` 工具自主切换 |
| **Persona** | 推理深度（Planner/Copilot/CAA） | 架构 | 任务特征（调 design_workflow → Planner；高置信低风险 → CAA；默认 → Copilot）|
| **ReasoningMode** | 成本控制（ROUTINE/ADAPTIVE/EXPLORATORY） | 架构 | Memory 命中率（>0.9 → ROUTINE；已知模式 → ADAPTIVE；未知 → EXPLORATORY）+ fallback 信号（见下）|

#### ReasoningMode fallback 信号（解决 Memory 冷启动期）

Memory 命中率 >0.9 需要 M3+ 条目积累（M2→M3 需 3 次人工验证，M3→M4 需 committee）——这是人类吞吐瓶颈，不是代码问题。阶段 4 引入 ReasoningModeSelector 后，相当长时间内 Memory 没有高置信条目，纯靠命中率会导致**所有任务走 ADAPTIVE/EXPLORATORY（最贵档）**，成本控制承诺失效。

**冷启动期 fallback 信号**（不依赖 Memory 命中率）：

| 信号源 | 取值 | 触发 ReasoningMode |
|---|---|---|
| `UrgencyLevel`（用户/上游传入）| ROUTINE | ROUTINE（跳过 Memory 探针）|
| `UrgencyLevel` | URGENT | ADAPTIVE |
| `UrgencyLevel` | CRITICAL | EXPLORATORY（强制全推理）|
| 任务类型分类（IntentClassifier 产出）| 查询类（标准查询/案例查询）| ADAPTIVE |
| 任务类型分类 | 决策类（缺陷判定/工艺调整）| EXPLORATORY |
| Memory 探针 | 命中率 >0.9 | ROUTINE（覆盖 urgency=ROUTINE 之外的判断）|

**优先级**：CRITICAL urgency > Memory 探针 > 任务类型 > 其他 urgency。即用户标 CRITICAL 时即使 Memory 命中高也强制 EXPLORATORY——安全优先于成本。

**冷启动期声明**：阶段 4 早期（M3 条目 < 100 条）Memory 探针命中率长期 <0.9，ReasoningMode 主要由 urgency + 任务类型驱动。这是预期行为，不是 bug。Memory 攒到 M3 条目 >100 且 >30% 命中率后，探针才成为主导信号。

#### Role × Persona 兼容矩阵

| Role ＼ Persona | Planner | Copilot | CAA |
|---|:---:|:---:|:---:|
| **Explorer** | ✅ 设计研究方案 | ✅ 默认 | ❌ 探索不决断 |
| **Vision** | ❌ | ✅ 默认 | ✅ 高置信度缺陷自动判定 |
| **Quality** | ✅ 合规方案规划 | ✅ 默认 | ❌ 合规判定必须人审 |
| **Operator** | ❌ | ✅ 默认 | ⚠️ 仅限低风险读操作；`adjust_parameter` 永远走 Copilot + 人工审批 |

**关键约束**：
- 架构不允许选非法组合（如 Quality + CAA），即使 Memory 命中率高、置信度高
- LLM 不能通过 `switch_role` 改变 Persona——Persona 由架构选，与 Role 正交
- ReasoningMode 与 Role/Persona 完全正交

**Role×Persona 冲突裁决**（LLM 切 Role 后与当前 Persona 非法组合时）：

LLM 通过 `switch_role` 切角色是异步发生的——切之前架构选的 Persona 可能与新 Role 非法组合（如 LLM 从 Vision 切到 Quality，但当前 Persona 是 CAA）。裁决规则：

| 情形 | 架构行为 | LLM 感知 |
|---|---|---|
| LLM 切到合法组合 | 接受 switch_role，Persona 不变 | tool_result: "switched to Quality, persona=Copilot" |
| LLM 切到非法组合（如 Quality+CAA） | 接受 switch_role，**Persona 自动降级到该 Role 的默认 Persona**（见矩阵"默认"列）| tool_result: "switched to Quality, persona downgraded CAA→Copilot (Quality+CAA 非法)" |
| LLM 切到 ⚠️ 风险组合（如 Operator+CAA） | 接受 switch_role，Persona 降级到 Copilot，**且该会话剩余生命周期内 CAA 被禁** | tool_result: "switched to Operator, persona=Copilot, CAA disabled for session (risk control)" |

**原则**：架构不拒绝 `switch_role`（LLM 自主权），但架构强制 Persona 适配（成本/安全约束）。LLM 收到降级信号后可自主决定是否继续——如果继续操作即视为接受降级后的 Persona。

**EventLog 记录**：所有 Role×Persona 冲突裁决必须记进 EventLog，含原 Persona、新 Persona、降级原因。审计可追溯。

#### ReasoningMode 与 Memory 注入的衔接

§0.1 说"阶段 5+ LLM 显式调 search_memory，架构不再自动注入 Memory"。但 §A.2 选 ReasoningMode 又依赖 Memory 命中率——架构仍要查 Memory。衔接机制是**轻量 Memory 探针**：

- 会话开始，架构执行 `Memory.probe(query=current_task_signature)`，只取 similarity score + count，**不取 content**，**不进 LLM prompt**
- 根据 probe 结果选 ReasoningMode
- LLM 收到 ReasoningMode 标签（system prompt 内），自主决定是否调 `search_memory` 拿详情

阶段 0-3 无 Memory 探针（ReasoningModeSelector 阶段 4 才引入，探针作为其依赖不可能更早）；阶段 4 探针 + 自动注入并存；阶段 5+ 探针保留、自动注入移除。

**ROUTINE 模式语义澄清**：ROUTINE 下架构跳过 LLM 推理，直接返回探针命中的 Memory 条目；自动注入仅在 ADAPTIVE/EXPLORATORY 模式下发生。ROUTINE 是 ReAct 的**唯一合法旁路**——架构在 Memory 高置信命中时直接返回缓存结果，跳过 LLM 推理。这是 §0.1 "架构永远不替 LLM 决定调哪个工具"的**显式例外**：ROUTINE 语义下"无需推理"（高置信命中 = 已知答案），不存在"调哪个工具"的决策。该例外由架构基于 Memory 命中率严格触发，LLM 不能主动声明 ROUTINE。

**ROUTINE 过期机制**（解决 stale Memory 条目风险）：

焊接标准会更新（GB/T 换版）、缺陷模式会演化（新材料新工艺）。ROUTINE 直接返回 Memory 条目跳过 LLM 推理——如果条目过期，工业安全场景下是责任问题，不是性能问题。Memory 条目必须带 `last_verified_at` 字段，ROUTINE 触发前架构检查：

| 条目状态 | `last_verified_at` 年龄 | ROUTINE 行为 |
|---|---|---|
| fresh | ≤ 90 天 | 正常触发 ROUTINE，返回条目 |
| stale | 90-180 天 | 不触发 ROUTINE，降级 ADAPTIVE，条目作为 strong hint 注入 |
| expired | > 180 天 | 不触发 ROUTINE，降级 EXPLORATORY，条目作为 weak hint 注入 + 异步触发复审 task |

**复审 task**：expired 条目自动入队 `memory_review_queue`，由质量工程师周期性复审。复审通过刷新 `last_verified_at`；复审失败降级 confidence 或归档。

**关键约束**：
- `last_verified_at` 在 M2→M3 晋升时初始化（人工确认时刻）
- M3→M4 committee 通过时刷新
- 标准/规则类条目（M4 Knowledge）的过期阈值可按标准类型差异化（如国标 365 天、企业标准 180 天）——具体阈值在 §八 Memory 实现时定

### A.3 图像细节渐进式获取 — LLM 主动请求

> **哲学一致性自检**: 早期版本曾按 ReasoningMode 由架构自动选 Thumbnail/全图/纯文本，这相当于架构在 LLM 不知情下偷换图像精度，违反"LLM 决定做什么"原则。**修订后的策略**: 架构始终以低成本默认值（Thumbnail）入场，LLM 自己判断信息不足时通过工具显式升级——架构不替 LLM 做"看不看清"的决定。

#### 默认入场策略 (架构层硬性约束)

会话开始时图像统一以 Thumbnail (~85 tokens) 注入到消息层。这是**成本闸门**，不是策略选择：

- 高分辨率原图存储在 MinIO，仅服务端持有
- 消息层只携带 Thumbnail 占位 + image_id 引用
- LLM 默认看到的是"压缩版本 + CV Tool 量化结果"

#### LLM 主动升级 — request_image_detail 工具

当 LLM 判断 Thumbnail 不足以决策时，调用工具显式请求：

```python
request_image_detail(
    image_id: str,           # 引用 (从 ContextSnapshot 获取)
    level: Literal["high", "roi"],  # high=全图高细节, roi=区域裁剪
    roi: Optional[BBox] = None,     # level=roi 时必填
    reason: str,             # require_reason=True (借鉴 Tier-B 工具策略)
)
→ 返回: 新 image_ref，下一轮注入到消息层 (替换或追加)
```

**关键性质**:
- LLM 知道自己看的是 Thumbnail (System prompt 明示)
- LLM 知道升级有成本 (工具描述里写明 token 消耗)
- 升级动作被 PolicyHook 拦截（每会话上限 N 次，防止挥霍）
- 升级历史记入 EventLog，便于回放和成本分析

#### CV Tool 辅助 — 量化代替"看清"

很多时候 LLM 要的不是更高分辨率，而是定量信息。CV Tool 不消耗图像 tokens：

- `measure_dimensions(image_id, points)` — 像素到 mm 标定
- `crop_roi(image_id, bbox)` — 区域提取（输出新 image_id，可再 request_image_detail）
- `analyze_histogram(image_id)` — 明暗/对比度分布
- `detect_edges(image_id)` — 轮廓提取

CV Tool 默认开放，LLM 自己决定调用顺序。

#### 阶段映射

| 阶段 | 图像策略需求 |
|---|---|
| 阶段 0-1 (无图像能力) | 不送图给 LLM，仅文本对话 |
| 阶段 2 (LLM 看图) | 默认 Thumbnail，LLM 视情况升级 |
| 阶段 3+ (LLM 介入决策) | 默认 Thumbnail + detect_defects MCP 工具辅助 |
| 阶段 5+ (复杂视觉推理) | LLM 频繁升级 + ROI 裁剪 |

架构不预设"哪个阶段用哪种模式"，由 LLM 在工具语义下自我决定。

### A.4 DecisionFactory — 决策输出类型

LLM 产生决策后，DecisionFactory 根据当前 Persona 格式化输出为结构化类型。LLM 自由决定内容，架构决定格式。

```
15 种 DecisionOutput 类型:

WorkflowRecommendation    方案推荐 (Planner)
  ├── name, description
  ├── steps: [{step, tool, params}]
  └── risk_assessment

ParameterRecommendation   参数建议 (Copilot)
  ├── parameter_name, current_value, recommended_value
  ├── reason, confidence
  └── safety_impact

InvestigationDirective    调查指令 (CAA)
  ├── target, method
  ├── expected_findings
  └── urgency

RiskAssessment            风险评估
Explanation               解释说明
ConfirmationRequest       确认请求
EscalationNotice          升级通知
MemoryArchiveDecision     记忆归档决策
DefectDetectionResult     缺陷检测结果 (Vision 角色)
AnnotationSuggestion      标注建议 (Vision 角色)
StandardComplianceResult  标准符合性判定 (Quality 角色)
ProcessAdjustment         工艺调整建议 (Operator 角色)
WorkflowUpdate            工作流更新 (设计后追加步骤)
KnowledgeGapReport        知识缺口报告
ErrorRecovery             错误恢复策略
FreeTextResponse          自由文本 (兜底)
```

**15 种类型的阶段引入映射**（与其它子节风格一致，避免一开始就 15 种全上）:

| DecisionOutput 类型 | 引入阶段 | 引入理由 |
|---|---|---|
| FreeTextResponse | 阶段 0 | 兜底类型，LLM 没结构化输出时用 |
| Explanation | 阶段 1 | web_search 回答需要解释 |
| ConfirmationRequest | 阶段 3 | request_confirmation 工具引入 |
| EscalationNotice | 阶段 3 | escalate 工具引入 |
| DefectDetectionResult | 阶段 3 | detect_defects MCP 引入 |
| AnnotationSuggestion | 阶段 3 | annotate_label MCP 引入 |
| ErrorRecovery | 阶段 3 | OnFailAction 基础策略 |
| ParameterRecommendation | 阶段 4 | Copilot 端点引入 |
| WorkflowRecommendation | 阶段 5 | design_workflow 工具引入 |
| WorkflowUpdate | 阶段 5 | 动态工作流更新 |
| RiskAssessment | 阶段 5 | 复杂决策需风险评估 |
| StandardComplianceResult | 阶段 4 | Quality 角色 + 知识库 |
| ProcessAdjustment | 阶段 5 | Operator 角色 + 生产线集成 |
| InvestigationDirective | 阶段 6 | CAA 自主行动 + 深度推理 |
| MemoryArchiveDecision | 阶段 4 | archive_memory 工具引入 |
| KnowledgeGapReport | 阶段 6 | 知识库主体域就绪后 |

**关键约束**：
- 起步阶段 0-2 只需 `FreeTextResponse + Explanation` 两种
- 阶段 3 接 MCP 后批量引入 5 种（Detection/Annotation/Confirmation/Escalation/ErrorRecovery）
- 阶段 5-6 引入剩余高级类型
- LLM 不感知"当前阶段支持哪些类型"——DecisionFactory 在 LLM 输出后判定，如果不支持的类型，降级为 FreeTextResponse 并记 EventLog

### A.5 Planner/Reflector/SubAgent — 深度推理能力

这些是 LLM 在 ReAct 循环中可以使用的**内部推理工具**，不是外部流水线步骤。LLM 自主决定是否需要深度推理。

```
Planner (自建规划器):
  当 LLM 调用 design_workflow 工具时激活
  内部: 任务分解 → 子任务排序 → 依赖分析 → 执行计划
  输出: WorkflowRecommendation

Reflector (自建反思器):
  当 LLM 觉得推理结果不确定时自主使用
  内部: 回顾推理链 → 找出薄弱环节 → 提出修正
  输出: 更高置信度的决策

SubAgent (子代理委托):
  当 LLM 需要并行调查多个方面时自主使用
  通过 spawn_investigator 工具触发 (借鉴 Claude Code Task tool)
  内部: 创建子任务 → 并行执行 → 汇总结果
  输出: 多视角综合分析

关键: 这些能力是 LLM 可以选择调用的，不是必须走的步骤
```

#### spawn_investigator 上下文隔离边界

子调查员不是父 LLM 的克隆，是有明确读写边界的隔离执行单元。边界规则：

| 资源 | 子调查员权限 | 理由 |
|---|---|---|
| 父 WeldMap 快照 | ✅ 只读 | 子任务需要知道父任务的上下文（case_id、已有标注），但不能改父的状态 |
| 父 Memory | ❌ 不可读 | 父的推理 Memory 含父的偏见/偏好，子调查应独立形成判断；强制隔离避免回声室 |
| 父 EventLog | ❌ 不可读 | 同上，子的推理链必须独立 |
| 子独立 Memory 分区 | ✅ 可读写 | 子调查过程中产生的所有 Memory 写入独立分区，prefix `investigator_<id>_` |
| 子独立 EventLog | ✅ 可写 | 子的 ReAct 迭代记录在自己的 EventLog |
| 工具集 | ✅ 与父相同 | 工具层共享，子可以调同样的 Tool/MCP（受同一 ToolPolicy 约束） |
| Capability (LLM) | ✅ 共享 | 同一 LLM Provider，但用独立 conversation |
| 结论回交 | ✅ 单向写回父 | 子调查完成，结论作为 `tool_result` 回到父 LLM 的 ReAct 下一轮 |

**父子结论矛盾时的裁决**：

子调查员返回的结论与父 LLM 当前判断矛盾时，**父 LLM 决定谁赢**——不是架构自动采信子、也不是自动采信父。父 LLM 看到子的结论后，在下一轮 ReAct 自主判断：

- 接受子结论 → 父更新自己的判断
- 拒绝子结论 → 父在 thinking 里说明拒绝理由，继续原判断
- 不确定 → 父再 spawn 第二个独立子调查员做交叉验证

关键约束：
- **子调查员的 Memory 写入不自动晋升到全局 Memory**——子的发现只是"线索"，要进全局必须父 LLM 显式调 `archive_memory` 工具
- **子调查员不能 spawn 子子调查员**——避免递归爆炸，深度限制 1 层
- **子调查员的 ToolPolicy 与父一致**——不能因为隔离就绕过安全边界

反例（避免）：
- ❌ 子调查员共享父 Memory——会让子的判断被父的偏见污染，违背"并行调查多视角"的初衷
- ❌ 子调查员能改父 WeldMap——破坏 single source of truth
- ❌ 子结论自动覆盖父——架构不能替父 LLM 决定采信谁

**隔离 vs 反馈学习的权衡说明**:

子调查员不读父 Memory 会带来一个张力：如果父 LLM 刚从用户反馈学到了"椭圆形暗区→气孔"，子调查员看不到这个修正，可能仍判"夹渣"——反馈学习成果没传递给子。

这是**有意的隔离代价**，理由：

| 场景 | 该不该传反馈给子 | 机制 |
|---|---|---|
| 子调查用于"独立验证父的判断" | ❌ 不传 | 子要看父的偏见盲点，传了反馈就失去独立性 |
| 子调查用于"延续父的反馈学习做深入调查" | ✅ 该传 | 但不通过读父私有 Memory，而是通过全局 Memory |

**正确的反馈传递路径**（不破坏隔离）：
```
用户反馈 → 父 LLM 收到 → 父 LLM 调 archive_memory 工具写全局 Memory
                                ↓
                        全局 Memory (VALIDATED)
                                ↓
子调查员调 search_memory 工具 → 命中全局 Memory → 看到反馈
```

关键：反馈进全局 Memory 是**父 LLM 的显式动作**（调 archive_memory），不是自动共享。父 LLM 决定哪些反馈值得上升到全局，子调查员通过工具调用读全局——这保持了"子不读父私有 Memory"的隔离，又让重要反馈能跨调查员传递。

反例（避免）：
- ❌ 子调查员直接读父私有 Memory——破坏隔离，子被父偏见污染
- ❌ 反馈自动进全局 Memory 不经父 LLM 决策——违反"LLM 决定做什么"，且会让噪音反馈淹没全局

### A.6 EventLog — 不可变审计追踪

```
EventLog (Append-only):

BrainEventType 枚举:
  STATE_TRANSITION   状态转换
  TOOL_CALL          工具调用
  TOOL_RESULT        工具返回
  VALIDATION         验证结果
  DECISION           决策生成
  CHECKPOINT         检查点保存

BrainEvent (frozen dataclass):
  event_id: str
  timestamp: datetime
  event_type: BrainEventType
  source: str          # "react_engine" | "orchestrator" | "gateway"
  data: dict           # 事件详情

ToolCallEvent:
  tool_call_id: str
  tool_name: str
  arguments: dict

ToolResultEvent:
  action_id: str       # 关联回 ToolCallEvent
  tool_call_id: str    # 双 ID 配对 (借鉴 OpenHands)
  success: bool
  result: dict
  error: str | None

用途:
  - 审计: 每次推理的完整事件链可追溯
  - 调试: 开发时查看 LLM 的推理轨迹
  - 回放: CheckpointManager.replay_event_log()
  - 视图: project_view() → DecisionPipelineView (决策管道视角)
```

### A.7 Checkpoint — 决策检查点/回滚

```
DecisionCheckpoint (frozen):
  checkpoint_id: str
  case_id: CaseId
  state: BrainStateMachine.state
  decision: BrainDecision | None
  event_log_length: int
  created_at: datetime

CheckpointManager:
  save()                  → 保存当前状态快照
  restore(checkpoint_id)  → 回滚到指定检查点
  replay_event_log()      → 从事件日志重放恢复
  apply_patch(diff)       → 结构化决策差异修补 (借鉴 Cline)

使用场景:
  - 验证失败 → 回滚到 CHECKPOINT 重新推理
  - 用户拒绝 → 回滚到 PUBLICATION 前修改决策
  - 超时恢复 → 从最近的 Checkpoint 恢复
```

### A.8 OnFailAction — 验证失败策略

```
验证管道 (决策写出时的守门人) 失败时:

OnFailAction 枚举:
  REASK     重新向 LLM 提问，附上验证失败原因
  FIX       自动修复 (如: 补充缺失理由)
  FILTER    过滤掉不合格部分，保留合格部分
  REFRAIN   不输出，告知用户无法满足要求
  ESCALATE  升级到人工处理

选择逻辑:
  Safety 验证失败 → ESCALATE (安全无妥协)
  Rule 验证失败   → REASK (LLM 补充理由)
  Shadow 验证失败 → FIX (自动对齐置信度)
  Consistency 失败  → REASK (LLM 解决矛盾)
```

### A.9 Event Sourcing + CAS — 并发安全

```
WeldMap 状态变更使用 Event Sourcing:

1. 所有变更记录为不可变事件 (M5 Audit)
2. Materialized View 提供快速读取
3. 事件回放支持审计追溯

CAS (Compare-And-Swap) 保证并发安全:

CAS_WRITE_LUA = "
local current = tonumber(redis.call('GET', key_version) or '0')
if current ~= expected_version then
    return {0, current}  -- 版本冲突
end
redis.call('SET', key_data, new_data)
redis.call('SET', key_version, new_version)
redis.call('XADD', key_events, '*', 'data', event_data)
return {1, new_version}  -- 成功
"

用于: Gateway 写出 WeldMap 时的原子更新
防止: 多个 Agent 同时修改同一个 Case 状态
```

### A.10 Bridge (L1→L2) — CognitiveGateway 到 Temporal

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

HumanGateSignal (L2 人工审批):
  TemplateWorkflow 执行到 human_gate 检查点时
  → Temporal.await() 等待人工信号
  → L1 的 request_confirmation 触发审批
  → 用户确认后 HumanGateSignal 发送到 L2
  → Temporal 工作流继续执行
```

### A.11 ApprovalService — 审批服务

```
两种审批模式:

同步审批 (CRITICAL/URGENT):
  LLM 调用高风险工具 → PolicyHook DENY
  → 同步等待人工审批 (WebSocket 推送通知)
  → 用户 approve/reject
  → LLM 收到结果继续推理

异步审批 (ROUTINE):
  非紧急操作 → 记录审批请求
  → LLM 继续其他工作
  → 用户后续审批
  → 结果写入 Memory 供下次使用

审批记录 → M5 Audit Memory (decision_id, human_id, action, reason, timestamp)
```

---

## 十一、执行路线

```
阶段 0: LLM 真正参与决策         ✅ 完成
阶段 1: LLM 能搜索外部信息       ✅ 完成
阶段 2: LLM 能看图说话           ⬜ 下一步
  ├── Volc Vision 多模态接入 ReActEngine
  ├── 图片上传 → 多模态消息 → LLM 看图推理
  └── 阶段 2 不依赖任何外部 MCP 工具，完成后系统就能"看图说话"

阶段 3: 实时人机反馈 + 检测/标注 MCP 工具 ⬜ 阶段 2后
  ├── WebSocket AgentLoop 持续运行 (主循环 + feedback consumer 双 task)
  ├── 中间结果实时推送 (thinking/tool_call/tool_result)
  ├── MCP 检测工具接入 (detect_defects) — 阶段 3 引入（与 §A 表一致）
  ├── MCP 标注工具接入 (annotate_label/update_annotation)
  └── 用户反馈通过 feedback consumer 写 Memory，主循环不阻塞

阶段 4: 系统从反馈中学习         ⬜ 阶段 3后
  ├── Memory.write(correction, VALIDATED)
  ├── Memory.search 注入 system prompt
  ├── 标注场景非阻塞并发 (50张图)
  └── 批量标注: 反馈即时学习，越标越准

阶段 5: 多角色自主切换           ⬜ 阶段 4后
  ├── 4 角色定义 (Explorer/Vision/Quality/Operator)
  ├── switch_role 工具调用
  ├── 角色分阶段工具白名单
  └── 动态工作流设计/更新

阶段 6: 知识库主体域             ⬜ 阶段 5后
  ├── 焊接工艺/检测技术/质量管理/设备知识 4 域
  ├── 跨域关联查询
  ├── 知识库与 Memory 融合 (M4 Knowledge 层)
  └── Web 搜索作为知识库补充

每个阶段必须用真实 LLM 端到端跑通后才能开始下一个
每个阶段都是 LLM 自主能力的扩展，不是流水线步骤的增加
```

### 11.1 新旧并存策略 — 解决渐进 vs 删除 vs DRY 三向矛盾

方案多处主张"删除 *Input/*Output wrapper、deepagents、shared/ports"，又主张"按阶段渐进引入"，又主张"DRY 不重复"。这三条字面上互相打架，落地时必须明确**新旧并存的具体策略**。

#### 策略：分类删除 + 一次性切换 + 短期影子并存

| 类别 | 处理 | 理由 |
|---|---|---|
| 死代码（无引用、未接入测试）| **阶段 1 起步即删除** | 没有兼容期成本，留着只是迷惑 |
| 弱耦合接口（*Input/*Output wrapper） | **跟随调用方一起改**，最后清理空壳 | 改一个调用方一次，不全局批量 |
| 核心范式（deepagents/Mode dispatch → ReAct） | **一次性切换**，不留兼容层 | 两套范式并存会导致 LLM 同时被两套规则约束 |
| 数据层（旧 Memory schema → 新 Block schema） | **影子并存 + 数据迁移脚本**，旧 schema 在阶段 4 完成后下线 | 数据迁移期不能丢历史标注 |

#### 具体落地（什么时候干什么）

```
阶段 0 (已完成):
  - LLM 真正参与决策
  - 旧的 Mode dispatch 还在跑

阶段 1 (web search):
  ☑ 删: shared/commands.py, shared/queries.py (零引用死代码)
  ☑ 删: interaction/llm/tracking.py (LLMCallTracker 未接入)
  ☑ 删: src/deepagents/ (空 stub)
  ☑ 删: src/brain/, src/gateway/, src/human_collaboration/ 等已迁出的旧目录
  ☐ 留: 旧 Mode classes 暂留（阶段 3 才换 ReAct）

阶段 2 (LLM 看图说话):
  ☐ 改: ReActEngine 增加多模态消息支持
  ☐ 阶段 2 不引入 MCP，detect_defects 推迟到阶段 3
  ☐ 留: 旧 Mode 仍主导对话流（图片场景例外，走 ReAct）

阶段 3 (人机反馈 + 检测/标注 MCP):
  ☑ 一次性切换: 删除全部 5 个 Mode classes + classifier + router
  ☑ ReAct 接管所有对话流
  ☑ 引入 MCPRegistry + detect_defects + annotate_label
  ☑ *Input/*Output wrapper 跟随被改的 Port 一起删

阶段 4 (反馈学习):
  ☑ 启动 Memory schema 迁移，新 Block schema 上线
  ☑ 旧 Memory 进入只读影子模式（前 2 个迭代周期）
  ☑ 验证后下线旧 Memory schema

阶段 5+:
  ☑ 不再有"旧代码"的概念，所有改动都是新功能
```

#### 关键约束

- **DRY 优先于渐进**: 同一概念（如 ReAct Loop）不能新旧两套并存——会导致 LLM 行为不可预测
- **删除优先于保留**: 任何"也许以后用得上"的旧代码立即删，git 历史够用
- **影子并存只用于数据**: 代码层面不允许影子并存，数据层面允许（迁移脚本期间）

### 11.2 为什么 Phase 1 自建 ReAct 而不直接用 LangGraph

§12.2 验证 LangGraph 是成熟方案，但 Phase 1 选择自建 ReAct，Phase 2 才换 LangGraph。这看似违反"不重新发明轮子"，需要明确论证。

#### 选择：Phase 1 自建（约 800-1200 行 control/react.py + tool_registry + hooks）

**论证**:

| 维度 | 自建 ReAct (Phase 1) | 直接 LangGraph (Phase 1) |
|---|---|---|
| 复杂度 | 单文件 ReActEngine + 简单循环 | LangGraph 编译图 + checkpointer + Send + interrupt |
| 学习成本 | 最低，团队读完 §2 就能改 | 中等，需要理解 LangGraph 概念模型 |
| 调试难度 | print + EventLog 即可 | 需要 LangGraph trace 工具 |
| 阶段 1-3 适配性 | 完美匹配（简单 ReAct 就够）| 杀鸡用牛刀 |
| 阶段 5+ 复杂场景 | 自建会显出局限（Send 类并发、interrupt+resume 难自实现） | 原生支持 |
| 切换成本 | Phase 2 重写 ~1000 行（一次性） | 无切换 |

#### 不直接上 LangGraph 的真实理由

1. **阶段 1-4 不需要 LangGraph 的高级功能**: 没有动态 fan-out（`Send`）、没有跨会话 resume——简单的 `while not done: step()` 已经足够
2. **团队对自建代码的掌控更高**: Phase 1 是范式从"Mode dispatch → ReAct"的切换期，自建意味着每一行都是团队理解的，避免 LangGraph 抽象层带来的"魔法"困惑
3. **Phase 2 切换成本可控**: 自建 ReActEngine 的接口（`run(user_input, context, session) → InteractionResponse`）和 LangGraph compiled graph 的 `astream()` 接口语义对齐——Phase 2 替换是"换实现保接口"，工具/Hook/EventLog 都不动

#### 何时切换到 LangGraph (Phase 2 触发条件)

| 触发信号 | 例子 |
|---|---|
| 需要并发 sub-investigation | spawn_investigator 真正落地后，自建难维护 |
| 需要 resume 中断的会话 | 长时间标注任务跨日恢复 |
| 需要分布式 checkpoint | 多机部署，状态需要持久化在 Postgres |
| 自建代码超过 1500 行还在加功能 | 维护成本超过切换成本 |

满足任一条件即切换。Phase 2 切换不是"计划"，是"信号驱动"。

#### 反例：哪些技术栈不应自建

- **LLM Provider**: 直接用 SDK，不自建（自建 = 重新实现 OpenAI 协议）
- **Vector DB**: 直接用 Milvus/pgvector，不自建（自建 = 重新实现 ANN 索引）
- **Workflow Engine**: 直接用 Temporal，不自建（自建 = 未来规则系统引入时白做）

ReAct 是"协议级"的简单循环，自建成本可控；以上三者是"协议级"的复杂系统，自建是浪费。

### 11.3 已知留白 — 落地时回填

以下内容是设计上有意留白，等代码跑起来才有真正答案，现在写细了反而是猜。按阶段回填：

| 留白项 | 回填时机 | 说明 |
|---|---|---|
| **NATS subject 命名规范** | 阶段 5+ 引入 NATS 时 | `weld.annotation.completed` 等是草案，正式 schema 待 NATS 落地后统一定义 |
| **Temporal Workflow ID 命名规范** | 阶段 5+ 引入 Temporal 时 | `annotation_<case_id>_<timestamp>` 是草案 |
| **端到端错误恢复示例** | 阶段 3 落地后 | "LLM 推理错 → Hook 拒绝 → 重试 → 仍失败 → Tier-C 降级 → 人工介入"完整数据流示例，需真实跑通后补 |
| **并发安全具体场景** | 阶段 5+ Event Sourcing 落地后 | "两操作员同时改同一 Case"的冲突解决流程示例，需 CAS 真实跑通后补 |
| **Copilot 5 端点 API schema** | 阶段 4 Copilot 落地时 | 每个端点的请求/响应 schema，REST API 形式化定义 |
| **观测性 SLO 指标表** | 阶段 3 落地后 | 决策延迟 P95、LLM 调用成功率、Memory 命中率等指标的具体阈值 |
| **数据迁移顺序** | Phase 1a mechanical rename 前 | 从 `validation/` `brain/` `deepagents/` 到新结构的迁移脚本顺序 |
| **端到端测试场景矩阵** | 阶段 3 落地后 | "50 张图标注 + 中途 5 张反馈 + 网络抖动"等集成测试场景清单 |
| **L4 工具的 caller_context 字段** | 阶段 5+ L2 接入后 | ToolRegistry 是否需要加 caller_context 记录调用来源（L1 LLM vs L3 Activity），用于审计 |
| **EventLog 与 Memory M5 Audit 的关系** | **Phase 3 启动前** | EventLog schema 设计前置约束——决策"事实源 + 异步物化视图"模型 |
| **SequencePolicy（监管强制序列）** | 阶段 5+ Operator/Quality 成熟时 | Hook 是单点拦截，不能表达"必先 A 后 B"。监管要求的 mandatory tool sequence 需扩展机制 |
| **spawn_investigator breadth cap** | 阶段 6 spawn_investigator 引入时 | 加 `max_concurrent_investigators=3` 配置项，与 Token Budget 叠加防失控 |
| **request_clarification 元递归熔断** | 阶段 4 request_clarification 引入时 | 单会话调用 ≥N 次未解决 → 强制 escalate，防 SupervisorCenter 触发的元递归 |
| **M0→M1 自动晋升触发条件** | 阶段 4 Memory 成熟时 | 定义"会话结束"判定（显式登出 + 30 分钟无活动；网络抖动断连不触发）|
| **三 plane 包结构：独立 pyproject vs monorepo** | 阶段 3 落地后 | cognitiveplane / controlplane / executionplane 当前各自 pyproject.toml，跨包 import 路径痛苦。阶段 3 末评估是否合并为单 monorepo 包（uv workspace 或 pip editable）。决策依据：阶段 3 实际跨包 import 频次 + CI 构建时间 |
| **ROUTINE 过期阈值差异化** | 阶段 4 Memory 成熟时 | §A.2 过期机制默认 90/180 天，但国标（GB/T）vs 企业标准 vs 案例记忆的合理阈值不同。阶段 4 末期按条目类型定差异化阈值表 |

**为什么留白**：这些项目的设计依赖运行时反馈——比如"两操作员同时改 Case"的冲突模式，只有真实跑过才知道常见冲突类型是什么，提前设计会基于猜测。等代码跑起来回填，比现在硬写更可靠。

**留白不等于遗漏**：上述项已在相应章节标注"阶段 X 落地时回填"，是有意的渐进设计，不是文档未完成。

### 11.4 阶段验收标准

"每个阶段必须用真实 LLM 端到端跑通后才能开始下一个"——这句话需要可检验。以下为各阶段最小验收标准。

#### 阶段 2 — LLM 能看图说话

| # | 验收项 | 通过条件 |
|---|---|---|
| 2.1 | 多模态消息接入 | Volc Doubao-Vision-Pro-32K 通过 `vision_complete` 接收 `{type:text, type:image_url}` 复合消息并返回推理结果 |
| 2.2 | 单图端到端 | 给定 1 张真实焊缝图 + "这张有什么缺陷？" → LLM 完成"看图→推理→回答"，全程无人工介入 |
| 2.3 | 测试集 | 至少 10 张真实焊缝图（含 ≥2 张无缺陷 + ≥2 张多缺陷）跑通，回答可读、不幻觉出图中没有的缺陷 |
| 2.4 | 不引入禁用项 | 阶段 2 代码 diff 中不出现 `mcp_*` / `agent_loop.py` / `switch_role` / `request_image_detail` 等阶段 3+ 文件 |

阶段 2 不验收：准确率（阶段 3 才有量化基准）、批量并发（阶段 4）、反馈学习（阶段 4）。

#### 阶段 3 — 实时人机反馈 + 检测/标注 MCP 工具

| # | 验收项 | 通过条件 |
|---|---|---|
| 3.1 | WebSocket 双向通信 | 前端能收到 `thinking` / `tool_call` / `tool_result`，能发 `feedback` / `interrupt` |
| 3.2 | AgentLoop 双 task | 主循环 + feedback consumer 通过 Memory 通信，主循环不阻塞等用户 |
| 3.3 | MCP detect_defects | LLM 自主决定调用 detect_defects，结果回流 ReAct 下一轮 |
| 3.4 | MCP annotate_label | LLM 调 annotate_label 创建标注，前端实时渲染 |
| 3.5 | 单图反馈闭环 | 用户改 1 张图的标注 → Memory.write(correction) → 下一轮 ReAct LLM 能感知（通过 EventLog 验证） |

阶段 4/5/6 的验收标准在该阶段开始前 1 周由当前负责人起草，走 PR review 通过后并入本节。不在阶段开始前定标准的，阶段不得开始。

#### 阶段 4 — 多角色协作 + Memory 成熟 + ReasoningMode

| # | 验收项 | 通过条件 |
|---|---|---|
| 4.1 | AgentRole 切换 | LLM 通过 `switch_role` 在 Explorer/Vision/Quality/Operator 间切换，tool_result 正确反映新 Role 的工具白名单 |
| 4.2 | Role×Persona 冲突裁决 | 构造非法组合（如 Quality+CAA），架构自动降级 Persona 到 Copilot，EventLog 记录降级原因；构造 ⚠️ 风险组合（Operator+CAA），架构降级 + 禁 CAA for session |
| 4.3 | Memory M3 晋升 | 至少 10 条 M2 条目经 3 次验证 + 人工确认晋升到 M3，`last_verified_at` 正确初始化 |
| 4.4 | ReasoningMode 三档生效 | 同一任务在 urgency=ROUTINE/URGENT/CRITICAL 下分别走 ROUTINE/ADAPTIVE/EXPLORATORY（EventLog 验证）；Memory 探针命中率 <0.9 时 fallback 信号（urgency + 任务类型）正确驱动 ReasoningMode |
| 4.5 | ROUTINE 旁路 + 过期 | Memory 探针命中 M3+ 条目且 fresh（≤90天）→ ROUTINE 直接返回，跳过 LLM；构造 stale（90-180天）条目 → 降级 ADAPTIVE；构造 expired（>180天）条目 → 降级 EXPLORATORY + 异步复审 task 入队 |
| 4.6 | LLM readiness 观测启动 | 阶段 4 末期开始连续 2 周统计 §0.1 "阶段 5 移除自动注入 readiness" 三项指标，记录到 SLO 看板 |
| 4.7 | 不引入禁用项 | 阶段 4 代码 diff 中不出现 `mcp_*` / `agent_loop.py` / `request_image_detail` / `design_workflow` 等阶段 5+ 文件 |

阶段 4 不验收：阶段 5 readiness 达标（4.6 只启动观测，达标判定在阶段 5 启动前）、L2 Temporal 集成（阶段 5）、工作流模板入 Memory（阶段 6）。

#### 11.4.1 测试方法论约束

验收标准的"通过条件"必须配合以下三类测试，否则不可验证：

| 测试类型 | 实施要点 |
|---|---|
| **对比测试** | 同一输入跑 N 次（建议 N≥5），断言结果落在合理集合（如缺陷类别正确即可，位置描述允许差异）|
| **Hook 拦截测试** | 架构约束 100% 拦得住——构造违规工具调用（如阶段 2 调 `adjust_parameter`），断言被 Hook DENY |
| **EventLog 重放测试** | 从 EventLog 重建状态——验证 3.5 的"LLM 能感知"指下一轮 ReAct 的 EventLog 含 Memory.search 命中 correction 条目 |

---

## 十二、源码验证研究 — 先进系统借鉴

以下内容基于对源码的真实阅读，不是训练数据印象。每项标注验证状态。

### 12.1 SWE-Agent — 纯 ReAct 单 Agent（非 Planner→Executor→Verifier）

**常见误读**：SWE-Agent 是 Planner→Executor→Verifier 三 Agent 架构。

**源码真相**：SWE-Agent 是**纯 ReAct 单 Agent**，没有 Planner、没有 Executor、没有 Verifier 分离。

```
源码证据 (princeton-nlp/SWE-agent):

agents.py:1265  DefaultAgent.run():
  while not step_output.done:
      step_output = self.step()     # 纯 ReAct 循环

agents.py:1006  DefaultAgent.forward():
  - 查询模型获取 thought + action
  - 解析 action
  - 执行 action
  - 返回 observation
  → 没有"规划阶段"和"执行阶段"的分离

agents.py:257  RetryAgent:
  - 包装 DefaultAgent，运行 N 次
  - Reviewer/Chooser 只在最终提交后评分
  - 不参与中间步骤

reviewer.py  Reviewer/Chooser:
  - 后置评估器，不是循环内的验证步骤
  - Chooser 比较多次完整尝试的最终结果
```

**对我们的意义**：SWE-Agent 作为最成功的代码 Agent 之一，验证了**单 Agent ReAct 循环**的有效性。我们选择单 Agent + switch_role 而非多 Agent 协作的方向是正确的。

**借鉴点**：
- **HistoryProcessor 链**（history_processors.py）：对历史消息进行压缩后再送入模型。我们可借鉴：`LastNObservations` + `RemoveRegex` 模式，在 ContextCompactor 中实现
- **RetryAgent 重试模式**：运行多次取最优。我们可借鉴：对低置信度决策，Orchestrator 内部可以多次推理取一致结果
- **ActionSampler**（action_sampler.py）：采样多个 completion 让模型比较选择。我们可借鉴：关键决策时用 `AskColleagues` 模式
- **Bundle 工具注册**：工具打包为 bundle 目录，YAML 定义 + bin/ 脚本。我们的 ToolRegistry 可参考类似模式

### 12.2 LangGraph — 编译后不可变，Send 做运行时扇出

**源码真相**：

```
源码证据 (langchain-ai/langgraph):

graph/state.py:778  add_node() 在编译后调用:
  → 记录 warning，但不影响已编译的图
  → CompiledStateGraph 是独立对象，与 builder 分离

types.py:664  Send(node, arg):
  → 运行时动态扇出：条件边可以返回 list[Send]
  → 每个 Send 指向不同节点 + 不同参数
  → 这是 LangGraph 实现"动态 DAG"的主要机制

types.py:811  interrupt(value):
  → 在节点内调用，抛出 GraphInterrupt
  → 恢复时节点从头重新执行，interrupt() 返回 resume 值
  → 需要 checkpointer 支持

types.py:759  Command(update, resume, goto):
  → 节点可以返回 Command 对象动态路由
  → 替代传统 add_edge 的"无边图"模式

prebuilt/tool_node.py:622  ToolNode:
  → 封装工具调用的执行节点
  → 支持 InjectedState/InjectedStore（工具可访问图状态）
  → tools_condition(state) 路由函数：有 tool_calls → "tools"，否则 → "__end__"

checkpoint/base/__init__.py  Checkpoint:
  → 保存 channel_values + channel_versions + versions_seen
  → 多后端：memory, postgres, sqlite, redis
  → DeltaChannel 支持 checkpoint 间的增量存储
```

**对我们的意义**：

| LangGraph 机制 | 是否采用 | 理由 |
|---------------|---------|------|
| StateGraph 编译式图 | **暂不** | 我们的 LLM 自主决定下一步，不需要预编译的状态图 |
| `Send` 动态扇出 | **借鉴** | 批量标注场景：一张图 → `Send("analyze", {img: i})` 并行处理 |
| `interrupt()` + `Command(resume=)` | **借鉴** | 替代我们的 request_confirmation 工具：更优雅的暂停/恢复模式 |
| ToolNode + tools_condition | **参考** | 我们的 ReActEngine 已经实现了类似的工具调用路由 |
| Checkpoint 系统 | **借鉴** | PostgreSQL 持久化 checkpoint，支持回滚和恢复 |
| `Command(goto=)` 动态路由 | **暂不** | LLM 自主决策替代硬编码路由 |

**Phase 2 可选**：如果需要更复杂的工作流编排（如 LLM 设计的工作流需要结构化执行），可引入 LangGraph 替代手写编排。



---

## 十三、工具三层体系 — Tool / MCP / Skill

基于源码研究，工具体系分为三层：

```
┌──────────────────────────────────────────────────────────────────────┐
│                    工具三层体系                                       │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Skill (业务流程)                                            │   │
│  │  可选。多个 Tool/MCP 的业务化组合                            │   │
│  │  LLM 可以调 Skill 跳过细节，也可以直接调 Tool                │   │
│  │                                                              │   │
│  │  例: weld_inspect_skill                                      │   │
│  │    1. detect_defects(image)     ← MCP 检测工具               │   │
│  │    2. search_standards(缺陷类型) ← 内部工具                  │   │
│  │    3. annotate_label(result)    ← MCP 标注工具               │   │
│  │    4. request_confirmation()    ← 内部工具                   │   │
│  │                                                              │   │
│  │  例: data_annotation_skill                                   │   │
│  │    1. create_project(config)    ← MCP Label Studio           │   │
│  │    2. import_tasks(images)      ← MCP Label Studio           │   │
│  │    3. 批量 detect + annotate   ← MCP 检测+标注               │   │
│  │    4. 汇总报告                 ← 内部工具                    │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                         │ 可选使用                                   │
│                         ▼                                           │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Tool (内部能力)                                             │   │
│  │  稳定、低风险、系统内置                                      │   │
│  │  每个工具一个文件，实现 BrainTool ABC                        │   │
│  │                                                              │   │
│  │  信息获取: web_search, search_standards, search_cases,       │   │
│  │           search_process, read_weldmap, explain_decision     │   │
│  │  操作执行: adjust_parameter, design_workflow,                │   │
│  │           request_confirmation, escalate, archive_memory     │   │
│  │  自我管理: switch_role, manage_plan, spawn_investigator      │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                         │ +                                          │
│                         ▼                                           │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  MCP (外部系统连接)                                          │   │
│  │  动态注册，通过 MCP 协议 (JSON-RPC 2.0) 接入                 │   │
│  │  同样实现 BrainTool ABC（MCPAdapter 适配）                   │   │
│  │                                                              │   │
│  │  标注工具 (Label Studio MCP):                                │   │
│  │    get_projects, get_task_data, create_prediction,           │   │
│  │    list_task_annotations, import_tasks, ...                  │   │
│  │                                                              │   │
│  │  检测工具 (检测模型 MCP):                                    │   │
│  │    detect_defects, segment_defect, classify_defect           │   │
│  │                                                              │   │
│  │  更多 MCP 按需接入:                                          │   │
│  │    CAD MCP, MES MCP, ERP MCP, ...                           │   │
│  │                                                              │   │
│  │  动态: MCP Server 发 tools/list_changed 通知时              │
│  │        MCPRegistry 更新 ToolRegistry                        │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  LLM 统一视角:                                                       │
│  ToolRegistry.list_tools() → 内部工具 + MCP 工具 + Skill             │
│  LLM 不区分来源，统一选择调用                                        │
│  所有工具走相同的 Hook 拦截 + ToolPolicy 审查                        │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

**分层约束 — 工具层的调用方不止 LLM**：

工具层（Tool / MCP / Skill）是 L1 LLM 和 L3 Activity **共享的调用目标**，工具本身无状态、不关心调用方。这避免了两套工具实现的重复（DRY），也避免把 MCP 误绑死在 LLM 上。

```
┌──────────────────────────────────────────────────────────────────┐
│  调用方 A: L1 ReAct Loop                                         │
│    - 决策方式: LLM function calling 自主决定调哪个工具           │
│    - 调用模式: 单次、推理驱动                                     │
│    - 返回值: tool_result 回流给 LLM 下一轮迭代                   │
│                                                                  │
│  调用方 B: L3 Temporal Activity                                  │
│    - 决策方式: Workflow code 固定编排（规则驱动）                │
│    - 调用模式: 批量、固定步骤序列                                │
│    - 返回值: Activity output 回流给 Workflow                     │
│                                                                  │
│  共享层: L4 工具层 (Tool / MCP / Skill)                          │
│    - 同一份工具实现，两种调用模式                                 │
│    - 工具无状态，不关心谁调它                                    │
│    - MCP 协议本身不绑定调用方（JSON-RPC 2.0 通用）               │
└──────────────────────────────────────────────────────────────────┘
```

关键约束：
1. **工具实现只有一份**——L1 LLM 和 L3 Activity 调同一个 `annotate_label`，不复制
2. **MCP 不是 LLM 专属协议**——MCP 定义的是工具的发现/描述/调用接口，调用方可以是 LLM、Activity、未来其它编排器
3. **区别在调用上下文，不在工具本身**——L1 走 ReAct 决策、L3 走 workflow 编排，工具不感知
4. **Hook 拦截对两种调用方都生效**——L1 和 L3 调工具都过 SafetyHook + PolicyHook，安全边界统一

反例（避免）：
- ❌ 让 L3 Activity 调标注系统原生 API，L1 LLM 调 MCP 封装——两套实现，DRY 失败
- ❌ 把 MCP 绑死在 LLM 上，L3 复刻一套工具协议——重复造轮子

**Tool 协议层 vs Tool 实现层 — 判别原则**：

| 维度 | L1 认知层 Tool（`control/tools/`）| L4 外部能力 Tool/MCP（`adapters/mcp/` 等）|
|---|---|---|
| 语义是否只在 LLM 认知循环内有意义 | 是 — 离开 ReAct Loop 这个工具就没意义 | 否 — 是外部系统的能力封装 |
| 后端依赖 | 无外部系统依赖（纯 LLM 认知行为）| 有外部系统依赖（Label Studio、检测模型 API、Web Search 等）|
| L3 Activity 是否可调 | 一般不可调（认知行为 L3 用不上）| 可调（L3 Workflow 通过同一 ToolRegistry 调用）|
| 示例 | `request_confirmation`、`request_clarification`、`spawn_investigator`、`switch_role`、`manage_plan`、`explain_decision`、`escalate`、`archive_memory`、`design_workflow` | `detect_defects`、`annotate_label`、`read_annotations`、`update_annotation` |
| 边界用例 | `web_search`：Tool 协议入口在 `control/tools/web_search.py`，Provider 实现在 `capability/web_search.py`（L1 Capability 平面内的能力封装，非 L4）| — |
| 边界用例 | `search_standards` / `read_weldmap`：Tool 协议入口在 `control/tools/`，后端 Provider 在 `knowledge/` / `gateway/`（L1 平面内 Provider，非 L4）| — |

> 判别原则：**"工具的语义是否只在 LLM 认知循环内有意义？"**
> 是 → 放 `control/tools/`（L1 认知层 Tool）。
> 否 → Tool 协议入口仍在 `control/tools/`，但实现归到具体位置：
>   - L1 平面内 Provider：`capability/`（LLM/Web Search）、`knowledge/`（standards/cases）、`gateway/`（WeldMap 读写语义）
>   - L4 外部能力 Adapter：`adapters/mcp/`（Label Studio/检测模型）、`adapters/weldmap/`（HTTP 客户端）、`adapters/retrieval/`（向量+FTS+RRF）
> 这是协议层与实现层的分离——LLM 看到的统一 ToolRegistry 全是 BrainTool，调用方不感知后端在哪。

**Skill 的实现**：

```python
class Skill(BrainTool):
    """业务流程模板 — 可选的工具组合。

    错误处理策略 (on_error):
    - STOP:        致命错误（schema 不兼容、依赖服务宕机）→ 立即停止整个 Skill
    - CONTINUE:    单项失败（某张图标注失败）→ 记录错误，继续后续步骤
    - RETRY_THEN_CONTINUE: 单项失败先重试 N 次，仍失败则记录并继续

    批量场景必须用 CONTINUE 或 RETRY_THEN_CONTINUE，不能用 STOP——
    50 张图第 3 张失败就停后面 47 张在工业场景不可接受。
    """

    def __init__(self, name: str, description: str,
                 steps: list[SkillStep], tool_registry: ToolRegistry,
                 on_error: str = "STOP",
                 max_retries: int = 2):
        self._steps = steps
        self._tools = tool_registry
        self._on_error = on_error
        self._max_retries = max_retries

    async def execute(self, **kwargs) -> ToolResult:
        """执行 Skill 的步骤序列。"""
        results = []
        errors = []
        for step in self._steps:
            tool = self._tools.get_tool(step.tool_name)
            args = step.resolve_args(kwargs, results)

            result = await self._execute_with_retry(tool, args)
            results.append(result)

            if result.error:
                if self._is_fatal(result.error):
                    # 致命错误：立即停止
                    errors.append({"step": step.tool_name, "error": result.error, "fatal": True})
                    break
                elif self._on_error == "STOP":
                    errors.append({"step": step.tool_name, "error": result.error, "fatal": False})
                    break
                else:
                    # CONTINUE / RETRY_THEN_CONTINUE: 记录错误，继续
                    errors.append({"step": step.tool_name, "error": result.error, "fatal": False})

        return ToolResult(
            output={"steps": [r.to_json() for r in results]},
            error=None if not errors else f"{len(errors)} step(s) failed",
            metadata={"errors": errors, "on_error": self._on_error},
        )

    async def _execute_with_retry(self, tool, args):
        last_result = None
        for attempt in range(self._max_retries + 1):
            result = await tool.execute(**args)
            if not result.error:
                return result
            if self._is_fatal(result.error):
                return result  # 致命错误不重试
            last_result = result
        return last_result

    @staticmethod
    def _is_fatal(error: str) -> bool:
        """致命错误：schema 不兼容、依赖服务宕机、认证失败"""
        fatal_markers = ["schema", "auth", "unavailable", "connection"]
        return any(m in error.lower() for m in fatal_markers)

    def to_function_definition(self) -> dict:
        """只暴露 Skill 级别的参数，不暴露内部步骤细节。"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters_schema,
        }
```

`data_annotation_skill` 必须配置 `on_error="CONTINUE"`，单张图失败不停整批。

**Skill vs Orchestrator (design_workflow) 对比**：

Skill 和 Orchestrator 都用来"把多个 Tool 调用串起来"，但编排来源完全不同。
**Skill 是声明式的简化版**——已知流程固化为静态步骤；
**Orchestrator 是 LLM 动态规划版**——LLM 自己看 observation 决定下一步。
两者都在底层调 `ToolRegistry.execute()`，是同一套工具的两种编排面。

| 维度 | Skill | Orchestrator (design_workflow) |
|---|---|---|
| 步骤来源 | 静态声明（YAML/配置/代码） | LLM 动态规划 |
| 编排决策 | 无 LLM 参与（按预定义顺序执行） | LLM 全程参与（每步看 observation 决定下一步） |
| 适用场景 | 已知业务流程（数据标注、报告生成、批量操作） | 创造性设计（新检测方案、未见过的诊断路径） |
| 错误恢复 | `on_error` 策略（STOP/CONTINUE/RETRY） | LLM 自己看 observation 决定（重试/换路径/放弃） |
| 参数 | Skill 级别参数（屏蔽内部步骤） | 每个 Tool 的完整参数都暴露给 LLM |
| 可观测性 | 步骤序列固定，易追踪 | EventLog 记录每次 LLM 决策 |
| 共享底层 | `ToolRegistry.execute()` | `ToolRegistry.execute()` |

> **决策原则**：流程固定且重复 → Skill；流程不确定或需要权衡 → Orchestrator。
> Skill 失败若是流程性问题，可降级到 Orchestrator 重新规划（这是 §13 fallback 的一部分）。

**Skill vs 直接调 Tool 的选择**：

```
LLM 自主决定:

场景1 (简单): "这张焊缝有什么缺陷？"
  → LLM 直接调 search_standards + 看图推理
  → 不需要 Skill，自己组合更灵活

场景2 (批量): "帮我标注这 50 张图"
  → LLM 调 data_annotation_skill(images=[...])
  → Skill 处理批量逻辑、并发编排、错误恢复
  → LLM 不需要自己写循环

场景3 (混合): "标注这些图，但标准不一样"
  → LLM 先调 search_standards 获取标准
  → 然后调 data_annotation_skill(images=[...], standard=标准)
  → Skill 的参数可由 LLM 前置工具调用的结果填充

关键: Skill 是便利的，不是强制的
LLM 永远可以直接调底层 Tool
Skill 只在 LLM 觉得"我不想自己编排这些细节"时才有价值

Skill 加载: LLM 通过 read_skill(name) 主动请求获取 markdown 指令内容
不是架构按场景自动塞，而是 LLM 决定何时需要建议
(借鉴 Claude Code Skill 的"LLM 主动请求"模式)
```

**文件结构更新**：

```
control/
├── tools/                    # L1 认知层工具 (Tool 协议层 + 认知业务实现)
│   ├── ...                   # 仅含"语义只在 LLM 认知循环内有意义"的工具
│                             # 详见 §5.3 判别原则
├── skills/                   # 业务流程 (Skill 层)
│   ├── base.py               # Skill ABC + SkillStep
│   ├── weld_inspect.py       # 焊缝检测流程
│   ├── data_annotation.py    # 数据标注流程
│   └── report_generation.py  # 报告生成流程
├── mcp_registry.py           # L4 MCP 工具发现与注册的薄桥接
│                             # 只做发现+注册，工具实现在 L4 (见下)
├── tool_registry.py          # 统一工具注册 (Tool + MCP + Skill)
└── ...

adapters/mcp/                 # L4 MCP 工具实现层（唯一的 MCP 实现位置）
├── base.py                   # MCPAdapter ABC — 适配 MCP 工具为 BrainTool
├── label_studio.py           # Label Studio MCP 客户端
└── detection_api.py          # 检测模型 MCP 客户端
```

> **重要**: `control/mcp/` **不存在**。MCP 是 L4 工具协议的实现位置在 `adapters/mcp/`。
> `control/mcp_registry.py` 只是发现+注册的薄桥接，它把 `adapters/mcp/` 下的 MCPAdapter
> 注册到 `ToolRegistry` 中供 LLM 调用。这保证 L1 LLM 和 L3 Activity 调的是同一份 MCP 实现。

---

