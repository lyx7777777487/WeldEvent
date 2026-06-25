# Phase 3 子项目 D — AgentLoop + WebSocket 双向通信 设计

**日期:** 2026-06-25
**状态:** 设计待 review
**范围:** Phase 3 验收 3.1 (WebSocket 双向) + 3.2 (AgentLoop 双 task) + 3.5 (单图反馈闭环 — 认知平面侧)
**不在范围:** 3.3 / 3.4 (executionplane 仓库 MCP server, 子项目 F)
**前置:** Phase 3 C 子项目 (MCP 基础设施) ✅ 完成

---

## 1. 问题陈述

### 1.1 当前状态

`cognitiveplane/interaction/api/chat.py:163-205` 的 WebSocket handler 是阻塞结构:

```python
while True:
    data = await websocket.receive_json()           # 阻塞等用户消息
    request = ChatRequest(**data)
    response = await ws_engine.run(...)              # 阻塞跑完整 ReAct
    await websocket.send_json(response...)           # 发最终结果
```

问题:
- **3.1 client→server 不通**: `await ws_engine.run()` 是一次性阻塞调用, 跑完才返回控制权给 `receive_json`。用户在 ReAct 跑的过程中发 `feedback` / `interrupt` 消息, 消息堆在 socket buffer, 等本轮 run() 结束才被读到 — 此时已无意义。
- **3.2 AgentLoop 不存在**: ReActEngine.run() 是一次性同步调用, 没有"主循环"概念。方案 §11 line 3298 要求"主循环 + feedback consumer 双 task 通过 Memory 通信, 主循环不阻塞等用户"。
- **3.5 反馈闭环缺两端**: 用户改标注 (需 3.4 工具, 不在本子项目) + 下一轮 ReAct 感知 (需 feedback → Memory.write → EventLog → §5.1 worldview injection)。本子项目只做后半段 — feedback consumer 收消息 → Memory.write(correction) → EventLog 记录 → 下一轮 ReAct system prompt 含 correction。

### 1.2 目标

- WebSocket 双向: 用户在 ReAct 跑的过程中能发 `feedback` / `interrupt`, 立即被处理
- AgentLoop 双 task: 主循环 task 跑 ReAct, feedback consumer task 收消息, 通过 asyncio.Queue + Memory 通信
- 反馈闭环 (认知平面侧): feedback → Memory.write → 下一轮 ReAct 通过 §5.1 worldview injection 读到 correction

---

## 2. 架构

### 2.1 双 task 结构

```
┌─────────────────────────────────────────────────────────────────┐
│ WebSocket Connection (per-connection)                           │
│                                                                 │
│  ┌──────────────────┐         ┌──────────────────────────┐     │
│  │ receive_task     │         │ react_task               │     │
│  │ (feedback        │ queue   │ (主循环, 跑 ReActEngine)  │     │
│  │  consumer)       │────────▶│                          │     │
│  │                  │ feedback│   step 1: thinking       │     │
│  │  receive_json()  │         │   step 2: tool_call      │     │
│  │  → parse type    │         │   step 3: tool_result    │     │
│  │  → if feedback:  │         │   ...                    │     │
│  │    Memory.write  │         │   step N: final          │     │
│  │    → queue.put   │         │                          │     │
│  │  → if interrupt: │         │   每步之间检查 queue      │     │
│  │    cancel react  │         │   - 有 feedback → 注入下一│     │
│  │  → if chat:      │         │     轮 system prompt     │     │
│  │    start new     │         │   - 有 interrupt → 取消  │     │
│  │    react_task    │         │     run                  │     │
│  └──────────────────┘         └──────────────────────────┘     │
│                                                                 │
│  send_json() 由 react_task 的 event_callback 触发               │
│  (thinking/tool_call/tool_result/final 实时推)                 │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 组件职责

| 组件 | 职责 | 不做 |
|---|---|---|
| `AgentLoop` (新建 `control/agent_loop.py`) | 管理 react_task + receive_task 生命周期, 持有 feedback_queue | 不重写 ReAct 逻辑, 委托 ReActEngine |
| `ReActEngine` (现有, 不改 run()) | 一次性 run() 跑完整 ReAct, 通过 event_callback 推中间事件 | 不感知 AgentLoop, 不改接口 |
| WebSocket handler (改 `interaction/api/chat.py`) | accept → 创建 AgentLoop → 启动双 task → 等连接结束 | 不直接调 ReActEngine |
| `Memory.write` (现有) | feedback 持久化为 correction 条目 | 不改 |
| `EventLog` (现有) | 记录 feedback 写入 + ReAct 下一轮读到 correction | 不改 |
| §5.1 `_build_system_prompt` (现有) | worldview injection 时 Memory.search 注入 correction | 不改 — 已支持, 只需验证 correction 能被搜到 |

### 2.3 关键设计选择

**Q1: ReActEngine.run() 是阻塞的, react_task 怎么在跑的过程中检查 feedback_queue?**

A: 不在 run() 内部检查。run() 跑完一次完整 ReAct (0-N 轮 LLM 调用) 后返回。feedback 在**下一轮 ReAct** 通过 §5.1 worldview injection 注入 system prompt。这样:
- 不改 ReActEngine (零侵入)
- feedback 不是即时打断, 是"下一轮感知" — 符合方案 §A.7 "决策检查点" 语义 (checkpoint 在 ReAct 轮间, 不在轮内)
- interrupt 是另一回事 — 用 asyncio.CancelledError 取消整个 run()

**Q2: 一个 WebSocket 连接内, 用户能发多条 chat 消息吗? 当前是 while True 循环收一条跑一条。**

A: 能。receive_task 收到 type=chat 消息时, 等上一个 react_task 结束 (或取消) 再启动新的。同一时刻只有一个 react_task 跑。这保持当前 chat.py 的"一问一答"语义, 但加了 feedback/interrupt 通道。

**Q3: feedback 消息 schema?**

A:
```python
class FeedbackMessage(BaseModel):
    type: Literal["feedback"] = "feedback"
    target_tool_call_id: str | None = None  # 针对哪个 tool_call 的反馈, None=整体反馈
    correction: str                          # 用户修正内容 (自然语言)
    category: Literal["wrong_result", "wrong_tool", "missing_info", "other"] = "other"
    session_id: str
```

写入 Memory 时: `Memory.write(content=correction, confidence=VALIDATED, source="human_feedback", metadata={"target_tool_call_id":..., "category":...})`。下一轮 ReAct 的 §5.1 `_build_system_prompt` 调 `Memory.search(query=当前用户问题)` 时, 若 correction 相似度 ≥0.5 会注入。

**Q4: interrupt 消息 schema?**

A:
```python
class InterruptMessage(BaseModel):
    type: Literal["interrupt"] = "interrupt"
    reason: str | None = None
    session_id: str
```

处理: `react_task.cancel()` → 抛 CancelledError → AgentLoop 捕获 → send_json({type:"interrupted", reason:...})。

**Q5: chat 消息 (新对话) 与 feedback/interrupt 怎么区分?**

A: 消息加 `type` 字段:
- `type=chat` → ChatRequest (现有字段 message/operator_id/case_id/session_id)
- `type=feedback` → FeedbackMessage
- `type=interrupt` → InterruptMessage
- 缺省 (无 type) → 当 chat 处理 (向后兼容)

---

## 3. 数据流

### 3.1 正常 chat (无 feedback)

```
1. 用户 connect WebSocket
2. AgentLoop 启动 receive_task, react_task=None
3. 用户 send {type:chat, message:"这张有什么缺陷?"}
4. receive_task 收到 → 启动 react_task = asyncio.create_task(engine.run(...))
5. react_task 跑 ReAct, 每步通过 event_callback → websocket.send_json
   (thinking/tool_call/tool_result 实时推)
6. react_task 结束 → send_json(ChatResponse) → react_task=None
7. 用户 send 下一句 chat → 回到 step 4
```

### 3.2 中途 feedback

```
1-5. 同 3.1, react_task 跑到 step 3 (tool_result)
6. 用户 send {type:feedback, correction:"这个缺陷位置标错了, 应该在焊缝根部"}
7. receive_task 收到 → Memory.write(correction, VALIDATED)
   → EventLog.emit(feedback_received)
   → feedback_queue.put(correction_summary)
8. react_task 继续跑完当前轮 (不打断)
9. 若 react_task 还在跑 (多轮 ReAct), 下一轮 _build_system_prompt
   → Memory.search → 命中 correction → 注入 system prompt
   → LLM 看到反馈, 调整推理
10. 若 react_task 已结束, 下一句 chat 时 step 9 生效
```

### 3.3 中途 interrupt

```
1-5. 同 3.1, react_task 跑到 step 3
6. 用户 send {type:interrupt, reason:"停, 我看错了"}
7. receive_task 收到 → react_task.cancel()
8. react_task 抛 CancelledError → AgentLoop 捕获
9. send_json({type:"interrupted", reason:"停, 我看错了"})
10. react_task=None, 等下一句 chat
```

---

## 4. 错误处理

| 情况 | 处理 |
|---|---|
| WebSocket 断开 | receive_task 抛 WebSocketDisconnect → AgentLoop 取消 react_task → 清理 |
| react_task 抛异常 (非 cancel) | AgentLoop 捕获 → send_json({type:"error", error:str}) → react_task=None, 等下一句 |
| Memory.write 失败 | receive_task 记 EventLog(failed) → send_json({type:"feedback_rejected", reason:...}) → 不影响 react_task |
| feedback_queue 满 (>10) | 丢弃最旧 + EventLog 记 overflow (防内存爆) |
| 收到未知 type | send_json({type:"error", error:"unknown message type"}) → 忽略消息 |

---

## 5. 文件结构

| 文件 | 改动 | 职责 |
|---|---|---|
| `cognitiveplane/control/agent_loop.py` | 新建 | AgentLoop 类 + FeedbackMessage / InterruptMessage schema |
| `cognitiveplane/interaction/api/chat.py` | 改 websocket_chat | 改调 AgentLoop, 不直接调 ReActEngine; 加消息 type 分发 |
| `cognitiveplane/tests/test_control/test_agent_loop.py` | 新建 | AgentLoop 单元测试 |
| `cognitiveplane/tests/test_interaction/test_websocket_feedback.py` | 新建 | WebSocket 双向 E2E 测试 |

不改: ReActEngine / Memory / EventLog / §5.1 worldview injection (零侵入)

---

## 6. 验收标准

### 6.1 3.1 WebSocket 双向

- [ ] 前端 send {type:chat, message:"..."} → 收到 thinking/tool_call/tool_result/final 流式事件
- [ ] react_task 跑的过程中, 前端 send {type:feedback, correction:"..."} → 收到 {type:"feedback_received"}
- [ ] react_task 跑的过程中, 前端 send {type:interrupt} → 收到 {type:"interrupted"} → react_task 停止

### 6.2 3.2 AgentLoop 双 task

- [ ] 单元测试: AgentLoop 启动后 receive_task + react_task 并发存在 (用 asyncio.all_tasks 验证)
- [ ] 单元测试: feedback_queue 通信 — receive_task put, react_task 的下一轮 _build_system_prompt 能读到
- [ ] 单元测试: react_task 被 cancel 后 AgentLoop 不崩, 能启动新 react_task

### 6.3 3.5 反馈闭环 (认知平面侧)

- [ ] E2E: 用户发 feedback → Memory.write 被调用 (mock Memory 验证)
- [ ] E2E: 下一轮 ReAct 的 EventLog 含 Memory.search 命中 correction
- [ ] E2E: 下一轮 ReAct 的 system prompt 含 correction 内容 (断言 prompt 字符串)

### 6.4 不引入禁用项

- [ ] `scripts/check_phase_discipline.py` PASS (无 Phase 4+ 文件: switch_role / request_image_detail / design_workflow 等)
- [ ] 全量测试无回归

---

## 7. 偏离方案

### 7.1 §11 line 3298 "通过 Memory 通信" 的解读

方案说"主循环 + feedback consumer 双 task 通过 Memory 通信"。本设计用 **asyncio.Queue (即时信号) + Memory (持久化)** 双通道:
- Queue: 即时唤醒 react_task 检查 feedback (不阻塞 receive)
- Memory: 持久化 + 下一轮 ReAct 通过 §5.1 worldview injection 读到

理由: 纯 Memory 通信要求 react_task 每轮主动查 Memory, 但 react_task 跑 ReActEngine.run() 时不知道何时该查。Queue 提供"有新 feedback"的信号, react_task 在 run() 结束后或下一轮开始前检查。Memory 仍是 feedback 的持久化载体, 符合方案本意。

### 7.2 3.5 验收条件弱化

方案 §11.4 3.5 要求"用户改 1 张图的标注 → ... → 下一轮 ReAct LLM 能感知"。本子项目只做认知平面侧 — feedback 是文本 (correction: str), 不是真实标注修改 (需 3.4 annotate_label 工具, 子项目 F)。验收条件改为"用户发文本 feedback → Memory.write → 下一轮 ReAct 感知"。真实标注闭环在 F 子项目完成后补充验收。

---

## 8. 不做的事

- 不改 ReActEngine.run() 接口 (零侵入 — 子项目 B 的成果不破坏)
- 不实现 StdioClient / SSEClient (子项目 E)
- 不实现 detect_defects / annotate_label MCP server (子项目 F)
- 不做前端 UI 改动 (后端 WebSocket 协议改好即可, 前端 chat.html 适配是后续工作)
- 不做 WebSocket 心跳/重连 (生产化工作, Phase 4+)

---

## 9. 下一子项目预告

- **E: StdioClient transport** — 跨进程 MCP 通信, 认知平面通过 subprocess + stdio 调 executionplane MCP server
- **F: executionplane MCP server** — detect_defects (包装 IQA/PPA capabilities) + annotate_label (Label Studio 集成)