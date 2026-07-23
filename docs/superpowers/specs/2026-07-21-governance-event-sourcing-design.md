# 治理状态事件化设计 (Governance Event Sourcing)

> 目标: 解决回溯清单 C8-C13（回溯与暂停/并行/扇出/成本/输入漂移叠加）的根因问题。
> 不引入新框架（不换 Restate/LangGraph），把已有的 AuditTrail 事件流贯彻为"事实来源"。

## 一、根因诊断: 12 个可变字段互相不知道对方

当前 `dag_runner_workflow.py` 持有 12 个可变状态字段:

| 字段 | 类型 | 谁产出 | 性质 |
|------|------|--------|------|
| `_node_results` | dict | 执行引擎 | 投影(应从事件reduce) |
| `_completed_nodes` | list | 执行引擎 | 投影 |
| `_failed_nodes` | list | 执行引擎 | 投影 |
| `_processed_nodes` | set | 执行引擎 | 投影 |
| `_node_attempts` | dict | 执行引擎 | 投影 |
| `_paused_nodes` | set | pause_scope | **治理状态(直接mutate)** |
| `_paused_batches` | set | pause_scope | 治理状态 |
| `_held_batches` | dict | batch_hold | 治理状态 |
| `_rework_nodes` | set | rework_node/revoke | 治理状态 |
| `_review_results` | dict | human_review | 治理状态 |
| `_injected_context` | dict | inject_context | 治理状态 |
| `_pending_signals` | list | batch_signals | 调度缓冲 |

**根因**: 治理模块直接 `.add()`/`.remove()`/`.pop()` 这些字段，而 `_check_node_pause`/`_process_single_node` 只读各自关心的字段。字段之间没有"对方发生了什么"的认知。

**这就是 C8-C13 全部 ❓ 的来源**:
- C12（回溯与暂停叠加）: rework_node 把 A 从 `_completed_nodes` 移除并加 `_rework_nodes`，但 A 还在 `_paused_nodes` 里——rework 要重跑 A，A 却被 pause 挡着。两个 set 互不知道。
- C9（回溯与并行交互）: 兄弟分支 B 已用 A 的旧结果，A rework 后 B 的 `_node_results` 没标"基于旧版本"。
- C11（输入漂移）: rework A 时，A 的输入该用旧值还是当下 `_injected_context` 的新值？没有"输入版本"概念。
- C10（回溯超成本）: rework 把多个节点加 `_rework_nodes`，但没有"这次 rework 的累计成本"投影，预算门控无从触发。

**不是引擎选型问题，是状态语义问题。** 换 Restate/LangGraph 也解决不了——它们都是单状态模型，叠加同样断。

## 二、核心改造: 治理动作 = 追加事件，状态 = 事件投影

**反转因果**: 当前是"改状态 → 旁路记 AuditTrail"。改成"追加 GovernanceEvent → 状态从事件 reduce"。

### 2.1 新增 GovernanceEvent（统一治理事件类型）

```python
@dataclass
class GovernanceEvent:
    event_id: str          # 全局唯一，单调递增 (evt_001, evt_002...)
    event_type: str        # PAUSE_SCOPE / RESUME_SCOPE / BATCH_HOLD / RELEASE_HOLD /
                           # REWORK_NODE / REVOKE_APPROVAL / RELABEL_REQUEST /
                           # GROUND_TRUTH_OVERRIDE / DELEGATE_REVIEW /
                           # STANDARD_UPDATE / CASE_LIBRARY_CORRECTION /
                           # INJECT_CONTEXT / HUMAN_REVIEW
    node_id: str | None    # 作用目标 (节点级事件)
    batch_id: str | None   # 作用目标 (批次级事件)
    scope: str             # "node" | "batch" | "workflow"
    actor: str             # 责任人
    reason: str
    payload: dict          # 事件专属数据 (forced_verdict / corrected_label / ...)
    timestamp: float
    supersedes: str | None # 覆盖关系: 本事件作废了哪个前置事件
                           # (resume_scope supersedes 对应 pause_scope;
                           #  release_hold supersedes 对应 batch_hold)
```

**关键字段 `supersedes`**: 这是解决叠加的核心。`resume_scope` 不是"从 set 移除"，而是"追加一个事件，声明作废之前的 pause"。这样：
- 事件流只增不改（审计完整）
- 投影时按 supersedes 链过滤掉已作废的事件
- 回溯到任意点 = "reduce events[:N]"，自动得到那个时刻的真实状态

### 2.2 12 个字段变成 reduce 投影

```python
class GovernanceEventLog:
    """治理事件流 - 事实来源。AuditTrail 仍是审计旁路(给合规看)，这个是执行依据。"""
    def __init__(self):
        self._events: list[GovernanceEvent] = []

    def append(self, ev: GovernanceEvent) -> None:
        self._events.append(ev)

    def project(self, up_to: int | None = None) -> "GovernanceProjection":
        """reduce 事件流得到当前(或历史某点)的治理状态投影。"""
        events = self._events if up_to is None else self._events[:up_to]
        # 1. 按 supersedes 链过滤: 被 supersede 的事件标记为 inactive
        active = self._filter_superseded(events)
        # 2. reduce 成投影
        return GovernanceProjection.reduce(active)
```

```python
@dataclass
class GovernanceProjection:
    """从事件流 reduce 出的治理状态快照 - 只读视图。"""
    paused_nodes: set[str]        # 仍 active 的 PAUSE_SCOPE(node)
    paused_batches: set[str]      # 仍 active 的 PAUSE_SCOPE(batch)
    held_batches: dict[str, dict] # 仍 active 的 BATCH_HOLD
    rework_nodes: set[str]        # 仍待重跑 (REWORK_NODE 未被执行引擎消费)
    review_results: dict[str, dict]
    injected_context: dict[str, Any]
    # 派生: 每个节点当前受哪些 active 事件影响 (解决叠加)
    node_interventions: dict[str, list[str]]  # node_id -> [active event_ids]
    # 派生: 当前 rework 批次的累计成本 (解决 C10)
    rework_cost_accumulated: float
```

### 2.3 治理模块改成"追加事件"而非"改字段"

**改造前** (`_pause_scope`):
```python
def _pause_scope(self, scope_type, scope_id, reason, initiator):
    for nid in nodes:
        self._paused_nodes.add(nid)   # 直接改字段
```

**改造后**:
```python
def _pause_scope(self, scope_type, scope_id, reason, initiator):
    ev = GovernanceEvent(
        event_id=self._next_event_id(),
        event_type="PAUSE_SCOPE",
        node_id=scope_id if scope_type=="station" else None,
        batch_id=scope_id if scope_type=="batch" else None,
        scope=scope_type, actor=initiator, reason=reason,
        payload={}, timestamp=workflow.now().timestamp(),
        supersedes=None,
    )
    self._governance_log.append(ev)
    # 不直接改 _paused_nodes - 它从投影读
```

**`_check_node_pause` 改成读投影**:
```python
async def _check_node_pause(self, node_id, case_id):
    proj = self._governance_log.project()  # 读当前投影
    while node_id in proj.paused_nodes or (case_id and case_id in proj.paused_batches):
        await workflow.wait_condition(lambda: not self._is_node_paused(node_id, case_id), ...)
```

## 三、这套设计如何消解 C8-C13

### C12 回溯与暂停叠加（当前是真 bug）
- **事件化后**: rework_node 追加 `REWORK_NODE` 事件，pause 的 `PAUSE_SCOPE` 事件仍在。
- 投影 `node_interventions[node_A] = [PAUSE_SCOPE#evt_003, REWORK_NODE#evt_007]`。
- 执行引擎看到 A 有两个 active 事件——**先解决 pause（等 resume 或强制取消 pause），再 rework**。事件流天然给出叠加语义，不再"两 set 互不知道"。
- 回溯到 evt_007 之前：投影里 A 只有 PAUSE_SCOPE，rework 消失，状态一致。

### C9 回溯与并行交互（兄弟分支用旧结果）
- **事件化后**: A rework 后，A 产出 `NODE_RESULT_VERSIONED` 事件（带 version）。
- 兄弟分支 B 的结果事件 `RESULT_PRODUCED` 记录 `based_on_input_version=v1`。
- 投影派生 `stale_results`: B 依赖 v1 但 A 已是 v2 → B 标记"基于旧版本"。
- 不需要"兄弟分支也重跑"的硬规则——投影给出"谁用了旧版本"，由人或策略决定是否级联。

### C11 输入漂移（rework 用新输入还是旧输入）
- **事件化后**: `INJECT_CONTEXT` 是事件，有 timestamp。
- rework A 时，A 的输入 = reduce(INJECT_CONTEXT events up to rework 时刻)。
- 要用旧输入: `project(up_to=原执行时刻).injected_context`。
- 要用新输入: `project().injected_context`（当前）。
- **"用新还是旧"从"待定❓"变成"投影参数选择"**——事件流两种都能算出来。

### C10 回溯超成本（预算门控）
- **事件化后**: 每个 `REWORK_NODE` 事件带 `estimated_cost`。
- 投影 `rework_cost_accumulated` = sum(active REWORK_NODE.cost)。
- 预算门控 = `if proj.rework_cost_accumulated > budget: block_new_rework()`。
- 当前没有这个投影，所以门控无从触发。事件化后自然就有。

### C8/C13 回溯引发连锁 / 根因在上游
- **事件化后**: rework A 触发下游 B/C 重跑，B 重跑又触发新 rework。
- 每次连锁追加 `REWORK_NODE` 事件，`supersedes` 记录"这次 rework 取代了上次的"。
- 投影按拓扑序 reduce，避免无限循环（同一节点的 rework 事件有幂等键，重复追加被去重）。
- 根因在上游: `REWORK_NODE(A)` 事件让投影自动级联标记下游——不需要单独写 BFS，reduce 自然传播。

### C15 批量回溯 / C16 全链重跑
- 批量回溯 = 一次追加多个 `REWORK_NODE` 事件。
- 投影 reduce 时按拓扑序处理，自动决定并行/串行（同层可并行，跨层串行）。
- 全链重跑 = 追加一个 `REWORK_NODE(root)` 事件，reduce 时下游全部被标记。
- **排序/并行/串行从"待定❓"变成"reduce 时的拓扑序策略"**。

## 四、为什么这比引入框架更好

| 方案 | 解决 C8-C13 | 成本 | 破坏现有 |
|------|------------|------|---------|
| 引入 Restate Awakeable | 部分解决持久化，**不解决叠加语义** | 换引擎，2周+ | 重写 L2 |
| 引入 LangGraph Time Travel | 解决"回到过去"，**但只针对单状态** | 换执行模型 | 失去 durability |
| 引入 Temporal Reset | 解决回退，**但和治理状态冲突**（reset 丢治理状态） | 中 | 需解决对账 |
| **治理状态事件化** | **直接解决叠加语义**（事件按时间序累加） | 中（改 8 模块+投影） | 不破坏（投影兼容现有字段） |

**关键区别**: 别人的方案都是"换个更强的引擎"，但 C8-C13 的本质是**多治理状态叠加的语义**，不是引擎能力。事件化让叠加变成"事件累加 + 投影"，这是语义层解决，不依赖任何引擎特性。

## 五、落地路线（不破坏现有架构）

### 阶段 1: 引入 GovernanceEventLog + GovernanceProjection（只加不改）
- 新增 `GovernanceEvent` / `GovernanceEventLog` / `GovernanceProjection` 三个类。
- `dag_runner_workflow` 新增 `self._governance_log`，但**现有 12 字段不动**。
- 每个治理模块在改字段后，**同时**追加一个 GovernanceEvent（双写）。
- 投影跑起来但只用于**校验**：`assert proj.paused_nodes == self._paused_nodes`。
- **风险零**：双写，投影错了不影响执行。

### 阶段 2: 读路径切到投影（治理模块仍双写）
- `_check_node_pause` / `_process_single_node` 改读 `proj.paused_nodes` 等。
- 治理模块仍双写，但执行引擎以投影为准。
- 跑 shadow 对比（你已有 `_assert_sm_consistency` 模式可复用）。
- 验证投影和字段一致后，字段降级为投影的缓存。

### 阶段 3: 写路径切到事件（字段变只读投影）
- 治理模块不再直接改字段，只 append 事件。
- 12 字段全部变成 `@property` 从投影派生。
- 这时 C8-C13 自动解决（叠加 = 事件累加 + supersedes 链）。

### 阶段 4: 回溯能力（吃事件流红利）
- 回溯到任意点 = `project(up_to=N)`，天然支持（不需 Temporal Reset）。
- A/B 对比 = 两个投影快照 diff。
- 历史重放 = reduce events，审计自动完整。

## 六、与 Temporal Reset 的关系（不冲突，互补）

- **事件化解决治理状态叠加**（L2 workflow 内部语义）。
- **Temporal Reset 解决执行历史回退**（L2 event history 层）。
- 两者正交: Reset 时把 `_governance_log` 序列化进 workflow state（Temporal 支持 query/signal 持久化 dict），reset 后从事件流重建投影。
- 比"治理状态散在内存字段"更利于 Reset——一个 list 比一堆 set/dict 更容易序列化。

## 七、不解决的（诚实边界）

- **跨工作流影响**（C5 已离系统 / standard_update 跨 workflow 扫描）: 仍需 L4 持久化层。事件化是单 workflow 内的，跨 workflow 要把事件流持久化到 DB。
- **artifact 真正回滚**（external 档已转正式）: 事件化记录"撤回意图"，但外部系统的实际回滚仍需对接外部 API。
- **记忆持久化**: 事件流本身是内存的，进程重启丢。要跨进程需把 `_governance_log` 落盘（但这比 12 个字段散落各处更易持久化）。

这三个是 L4 层 / 外部对接问题，不在本设计范围。本设计解决的是"单 workflow 内多治理状态叠加"这个最核心的语义难题。
