# 工业 Agent 前沿对标 (联网检索 + 代码对照)

> 检索日期: 2026-07-22 | 来源: arXiv + Landing AI 官网
> 对照对象: WeldEvent 当前实现

## 一手检索资料

| 来源 | 关键观点 | 对 WeldEvent 的意义 |
|------|---------|---------------------|
| **AgentsCAD** (arXiv 2607.02448) | 制造业 multi-agent:确定性几何检测(overhang>45° 硬阈值)+ LLM 只做 DFM 修改推理 | 确定性算法出测量值/硬判定,LLM 出决策建议。验证 MEA/RDA 应为确定性 CV 而非 LLM 顶替 |
| **DUCTILE** (arXiv 2603.10249) | Delegated, **User-supervised** Coordination of **Tool-integrated** LLM | 自适应编排(adaptive orchestration)与确定性执行(deterministic execution)分离。与 WeldEvent "LLM 决定做什么、架构决定不能做什么" 同向 |
| **Magentic-One** (arXiv 2411.04468) | Orchestrator: plan + track progress + **re-plan to recover from errors** | WeldEvent 已借鉴: Reflexion + 卡住检测 + modify_spec |
| **Landing AI / LandingLens** (landing.ai) | "Fully auditable, traceable" + "Accuracy you can prove, not guess" + confidence score | 工业质检判定必须可证明/可复现/可追溯。LLM 单轮视觉不满足,需确定性模型+置信度 |
| **制造业 LLM agent 对比** (arXiv 2605.31287) | LLM conversational agent "neither replacement nor panacea",有效性取决于任务信息处理需求 | 简单可量化检测任务该用确定性工具,LLM 适合复杂推理和交互编排 |

## 对照结论

### 对齐 (✓) 的部分
- **User-supervised 人在环**: ApprovalGate + T2 确认门 (revoke/override/case_correction)
- **Tool-integrated 编排**: 18 工具 + Temporal DAG + MCP 协议
- **Orchestrator re-plan**: Reflexion + 卡住检测 + modify_spec 热更新
- **可审计架构**: WeldMap 留痕 + DecisionRecord (actor/reason/timestamp) + 事件溯源
- **流程闭环**: rework_node / standard_update / case_library_correction 治理模块

### 偏差 (✗) 的部分 -- 与前沿共识的关键 gap
- **LLM 越界做测量判定**: analyze_image 让 MLLM 直接输出咬边深度/焊脚尺寸/质量等级。
  前沿共识 (AgentsCAD/LandingLens): 可量化指标必须用确定性算法,LLM 只做粗筛和决策建议。
  原因: LLM 每次输出不同,不满足 "audit-ready traceability" 和 "accuracy you can prove"。
- **确定性执行层断档**: MEA(几何测量)/RDA(缺陷检测)/VDA/RVA 全为 mock。
  前沿共识 (DUCTILE): adaptive orchestration 与 deterministic execution 必须分离,后者需真实算法。

### 建议优先级
1. 实现 MEA 确定性几何测量 (焊脚尺寸像素->mm 标定换算) -- 有硬数据才能判合格
2. 实现 RDA 确定性缺陷检测 (咬边/气孔 CV 算法) -- 替代 MLLM 顶替
3. analyze_image 退回辅助角色: 粗筛疑点位置,不直接出测量值和定级
4. Landing AI 式 confidence score: 每个判定附置信度,灰区进人工复核
