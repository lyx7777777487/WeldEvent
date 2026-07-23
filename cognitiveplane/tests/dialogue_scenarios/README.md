# 对话场景评估 (Dialogue Scenario Evaluation)

用真实 L1 ReAct 引擎 (DeepSeek) + 真实 Temporal 跑多轮对话, 测 agent 的:
- skill/工具命中率 (选对工具)
- 主动追问 (信息不足时 request_confirmation 弹窗)
- 执行中打断/改节点 (control_workflow + L2 signal)
- 回溯重做 (rework_node signal)
- 人工审查 gate (approval_store 阻塞)
- 危险拦截 (guardrail)

## 文件
- `harness.py` - 驱动器: build_engine (真实 LLM+13 工具+7 skill+bridge 连 Temporal) + run_turn (多轮+断言)
- `scenario_1_diagnosis.py` - 追问式诊断 (L1->L3)
- `scenario_6_guardrail.py` - 危险拦截
- (待写) scenario_2_full_chain.py - 全链路 L1->L2->L3
- (待写) scenario_3_interrupt.py - 执行中打断改节点
- (待写) scenario_4_rollback.py - 执行中回溯重做
- (待写) scenario_5_revoke.py - 已提交撤销发更正版
- (待写) scenario_7_review.py - 人工审查 gate

## 跑法
需要: .env 配 DEEPSEEK_API_KEY + docker weldevent-temporal 跑着 (7233)
```
PYTHONPATH=. python3 -m cognitiveplane.tests.dialogue_scenarios.scenario_1_diagnosis
PYTHONPATH=. python3 -m cognitiveplane.tests.dialogue_scenarios.scenario_6_guardrail
```

## 已发现的问题 (评估价值)
1. stub knowledge 返回空 -> agent 反复 search + fallback web_search (命中率受知识库影响)
2. 信息不足时 agent 不一定追问领域参数 (板厚/材质), 可能先 read_weldmap 或让用户上传图
3. 危险请求靠 LLM 自觉拒绝, 不一定触发架构层 output_guardrail
4. 违规参数 agent 倾向"分析"而非"拦截" (需明确 guardrail 规则)
