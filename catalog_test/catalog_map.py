"""清单条目 -> 测试场景映射 (source of truth).

把 WeldEvent_执行期情况流穷举清单.md 的每一条映射到:
  - L1 工具级测试 (WorkflowControlTool + FakeConnector, 验证信号/payload)
  - L2 API 端到端测试 (真实 HTTP 对话, 验证 NL->LLM->工具->signal 链路)

标注规则:
  ✅ 现有覆盖  |  🆕 本次新增  |  ❓ 待补充 (未实现, 不测)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CatalogItem:
    """清单条目 -> 测试场景定义."""
    item_id: str                     # "A1", "B2", "C4"
    category: str                    # "A".."V"
    situation: str                   # 情况描述
    mechanism: str                   # 当前倾向 (处理机制)
    coverage: str                    # "✅" | "🆕" | "❓"
    # ── L1 工具级测试 ──
    l1_action: str | None = None     # WorkflowControlTool action
    l1_params: dict[str, Any] = field(default_factory=dict)
    expected_signal: str = ""        # Temporal signal name
    expected_sig_type: str = ""      # batch_signals payload["type"]
    expected_payload_keys: list[str] = field(default_factory=list)
    tier2: bool = False              # 需确认门 (revoke/override/case_correction)
    # ── 测试分层 ──
    testable: bool = True            # False = ❓ 未实现
    layer: str = "L1"                # "L1" | "L2" | "L1+L2" | "unit"


# ══════════════════════════════════════════════════════════════
# A. 节点级执行结果情况 (workflow 内部行为, L2/unit 测)
# ══════════════════════════════════════════════════════════════
A_ITEMS = [
    CatalogItem("A1", "A", "正常成功", "直接提交", "✅", layer="L2"),
    CatalogItem("A2", "A", "成功带警告", "CONDITIONAL 触发审查", "✅", layer="L2"),
    CatalogItem("A3", "A", "参数错失败", "rework 改参数重做", "✅",
                l1_action="rework_node", l1_params={"node_id": "iqa_check"},
                expected_signal="rework_node", layer="L1+L2"),
    CatalogItem("A4", "A", "能力不足失败", "fallback 换能力", "✅", layer="L2"),
    CatalogItem("A5", "A", "外部依赖挂失败", "retry / escalate", "✅", layer="L2"),
    CatalogItem("A6", "A", "超时失败", "unknown 判定", "✅", layer="L2"),
    CatalogItem("A7", "A", "崩溃失败", "unknown_after_crash", "✅", layer="L2"),
    CatalogItem("A8", "A", "部分成功", "待定", "❓", testable=False),
    CatalogItem("A9", "A", "输出无效-schema", "RETRY 带指令", "✅", layer="L2"),
    CatalogItem("A10", "A", "输出无效-语义", "RETRY / rework", "✅", layer="L2"),
    CatalogItem("A11", "A", "空结果", "待定", "❓", testable=False),
    CatalogItem("A12", "A", "非确定性波动", "待定", "❓", testable=False),
]

# ══════════════════════════════════════════════════════════════
# B. 节点暂停情况 (重点)
# ══════════════════════════════════════════════════════════════
B_ITEMS = [
    CatalogItem("B1", "B", "暂停整 Run", "现有 pause", "✅",
                l1_action="pause", expected_signal="pause"),
    CatalogItem("B2", "B", "暂停单工位(节点)", "pause_scope", "🆕",
                l1_action="pause_scope",
                l1_params={"scope_type": "station", "scope_id": "iqa_check", "reason": "检查"},
                expected_signal="batch_signals", expected_sig_type="pause_scope",
                expected_payload_keys=["scope_type", "scope_id", "reason"]),
    CatalogItem("B3", "B", "暂停批次", "pause_scope", "🆕",
                l1_action="pause_scope",
                l1_params={"scope_type": "batch", "scope_id": "CASE-001", "reason": "批次检查"},
                expected_signal="batch_signals", expected_sig_type="pause_scope",
                expected_payload_keys=["scope_type", "scope_id", "reason"]),
    CatalogItem("B4", "B", "暂停在 mid-activity", "延迟到安全点", "✅",
                l1_action="pause", expected_signal="pause", layer="L1+L2"),
    CatalogItem("B5", "B", "暂停在 PREPARE 前", "标 PAUSED 不入队", "❓", testable=False),
    CatalogItem("B6", "B", "暂停在 COMMIT 前", "待定", "❓", testable=False),
    CatalogItem("B7", "B", "暂停期间信号堆积", "checkpoint 批量", "✅",
                l1_action="pause", expected_signal="pause", layer="L1+L2"),
    CatalogItem("B8", "B", "暂停期间上游完成", "正常", "❓", testable=False),
    CatalogItem("B9", "B", "暂停期间上游回溯", "下游需重跑", "❓", testable=False),
    CatalogItem("B10", "B", "暂停期间依赖过期", "待定", "❓", testable=False),
    CatalogItem("B11", "B", "暂停转取消", "cancel", "❓",
                l1_action="cancel", expected_signal="cancel_by_user", testable=True),
    CatalogItem("B12", "B", "暂停超时放弃", "待定", "❓", testable=False),
    CatalogItem("B13", "B", "暂停与超时叠加", "待定", "❓", testable=False),
    CatalogItem("B14", "B", "分级暂停组合", "待定", "❓", testable=False),
    # resume_scope (恢复分级暂停)
    CatalogItem("B-RES", "B", "恢复分级暂停", "resume_scope", "🆕",
                l1_action="resume_scope",
                l1_params={"scope_type": "station", "scope_id": "iqa_check"},
                expected_signal="batch_signals", expected_sig_type="resume_scope",
                expected_payload_keys=["scope_type", "scope_id"]),
]

# ══════════════════════════════════════════════════════════════
# C. 回溯/回退情况 (重点)
# ══════════════════════════════════════════════════════════════
C_ITEMS = [
    CatalogItem("C1", "C", "未提交回退(draft)", "rework 同 Run 内", "✅",
                l1_action="rework_node", l1_params={"node_id": "iqa_check"},
                expected_signal="rework_node"),
    CatalogItem("C2", "C", "已提交未消费回退", "rework_node 下游闭包", "✅",
                l1_action="rework_node", l1_params={"node_id": "ppa_check"},
                expected_signal="rework_node"),
    CatalogItem("C3", "C", "已提交已被下游消费", "下游闭包重跑", "✅",
                l1_action="rework_node", l1_params={"node_id": "mea_check"},
                expected_signal="rework_node"),
    CatalogItem("C4", "C", "已提交转正式回退", "revoke_approval", "🆕",
                l1_action="revoke_approval",
                l1_params={"node_id": "rda_check", "revoker_id": "qc-001", "reason": "误判", "artifact_status": "formal"},
                expected_signal="batch_signals", expected_sig_type="revoke_approval",
                expected_payload_keys=["node_id", "revoker_id", "reason"],
                tier2=True),
    CatalogItem("C5", "C", "已离系统回退", "发更正+通知", "🆕", testable=False, layer="L2"),
    CatalogItem("C6", "C", "部分回退", "relabel 专项", "🆕",
                l1_action="relabel_request",
                l1_params={"node_id": "vda_check", "original_label": "OK", "corrected_label": "NG", "reason": "标签错"},
                expected_signal="batch_signals", expected_sig_type="relabel_request",
                expected_payload_keys=["node_id", "original_label", "corrected_label"]),
    CatalogItem("C7", "C", "回溯发现新方案更差", "待定", "❓", testable=False),
    CatalogItem("C8", "C", "回溯引发连锁", "下游闭包递归", "❓", testable=False),
    CatalogItem("C9", "C", "回溯与并行交互", "待定", "❓", testable=False),
    CatalogItem("C10", "C", "回溯超成本", "预算门控", "❓", testable=False),
    CatalogItem("C11", "C", "回溯时上游也变了", "待定", "❓", testable=False),
    CatalogItem("C12", "C", "回溯与暂停叠加", "待定", "❓", testable=False),
    CatalogItem("C13", "C", "回溯与扇出叠加", "待定", "❓", testable=False),
    CatalogItem("C14", "C", "回溯后审计一致性", "event sourcing", "✅", layer="L1+L2",
                l1_action="rework_node", l1_params={"node_id": "iqa_check"},
                expected_signal="rework_node"),
    CatalogItem("C15", "C", "批量回溯", "待定", "❓", testable=False),
    CatalogItem("C16", "C", "回溯发现根因在上游根", "待定", "❓", testable=False),
]

# ══════════════════════════════════════════════════════════════
# D. 依赖与拓扑情况
# ══════════════════════════════════════════════════════════════
D_ITEMS = [
    CatalogItem("D1", "D", "依赖未就绪", "等待/调度", "✅", layer="L2"),
    CatalogItem("D2", "D", "依赖结果无效", "上游先修", "➖", testable=False),
    CatalogItem("D3", "D", "依赖被回溯", "下游闭包", "✅",
                l1_action="rework_node", l1_params={"node_id": "iqa_check"},
                expected_signal="rework_node"),
    CatalogItem("D4", "D", "条件分支路由变", "待定", "❓", testable=False),
    CatalogItem("D5", "D", "循环依赖检测", "拒绝/报错", "❓", testable=False),
    CatalogItem("D6", "D", "依赖图动态变化", "modify_spec", "✅",
                l1_action="modify",
                l1_params={"new_spec": {"nodes": [{"node_id": "iqa_check"}, {"node_id": "new_node"}]}},
                expected_signal="modify_spec", layer="L1+L2"),
    CatalogItem("D7", "D", "扇出部分失败", "DLQ 默认值", "✅", layer="L2"),
    CatalogItem("D8", "D", "扇出全部失败", "abort/escalate", "✅", layer="L2"),
    CatalogItem("D9", "D", "扇出超时部分返回", "待定", "❓", testable=False),
    CatalogItem("D10", "D", "依赖过期", "待定", "❓", testable=False),
    CatalogItem("D11", "D", "多重依赖部分就绪", "待定", "❓", testable=False),
    CatalogItem("D12", "D", "依赖版本冲突", "ArtifactVersion 选择", "❓", testable=False),
]

# ══════════════════════════════════════════════════════════════
# E. 数据与质量情况
# ══════════════════════════════════════════════════════════════
E_ITEMS = [
    CatalogItem("E1", "E", "输入数据缺失", "注入/中止", "✅", layer="L2"),
    CatalogItem("E2", "E", "输入数据损坏", "待定", "❓", testable=False),
    CatalogItem("E3", "E", "输入格式不符", "待定", "❓", testable=False),
    CatalogItem("E4", "E", "输入数据超大", "安全压缩", "✅", layer="L2"),
    CatalogItem("E5", "E", "中间结果膨胀", "待定", "❓", testable=False),
    CatalogItem("E6", "E", "标签错结果对", "relabel", "🆕",
                l1_action="relabel_request",
                l1_params={"node_id": "mea_check", "original_label": "pass", "corrected_label": "fail", "reason": "标签错误但结果无误"},
                expected_signal="batch_signals", expected_sig_type="relabel_request",
                expected_payload_keys=["node_id", "original_label", "corrected_label"]),
    CatalogItem("E7", "E", "结果错标签对", "rework", "❓", testable=False),
    CatalogItem("E8", "E", "结果模棱两可", "CONDITIONAL 审查", "✅",
                l1_action="human_review",
                l1_params={"node_id": "ppa_check", "decision": "approve"},
                expected_signal="batch_signals", expected_sig_type="human_review",
                expected_payload_keys=["node_id", "decision"], layer="L1+L2"),
    CatalogItem("E9", "E", "结果与历史不一致", "待定", "❓", testable=False),
    CatalogItem("E10", "E", "批次整体偏低", "batch_hold", "🆕",
                l1_action="batch_hold",
                l1_params={"batch_id": "CASE-001", "reason": "系统性偏低", "evidence": "quality_score < 0.6"},
                expected_signal="batch_signals", expected_sig_type="batch_hold",
                expected_payload_keys=["batch_id", "reason"]),
    CatalogItem("E11", "E", "抽检发现个别问题", "待定", "❓", testable=False),
    CatalogItem("E12", "E", "结果矛盾(多源)", "Critic 裁决", "✅", layer="L2"),
    CatalogItem("E13", "E", "结果不可复现", "待定", "❓", testable=False),
    # batch_hold 后续三档: release_hold / rework_batch / quarantine_batch
    CatalogItem("E-REL", "E", "释放冻结批次", "release_hold", "🆕",
                l1_action="release_hold",
                l1_params={"batch_id": "CASE-001"},
                expected_signal="batch_signals", expected_sig_type="release_hold",
                expected_payload_keys=["batch_id"]),
    CatalogItem("E-RWK", "E", "重做冻结批次", "rework_batch", "🆕",
                l1_action="rework_batch",
                l1_params={"batch_id": "CASE-001", "reason": "需返工"},
                expected_signal="batch_signals", expected_sig_type="rework_batch",
                expected_payload_keys=["batch_id"]),
    CatalogItem("E-QTN", "E", "隔离冻结批次", "quarantine_batch", "🆕",
                l1_action="quarantine_batch",
                l1_params={"batch_id": "CASE-001", "reason": "永久隔离"},
                expected_signal="batch_signals", expected_sig_type="quarantine_batch",
                expected_payload_keys=["batch_id"], tier2=True),
]

# ══════════════════════════════════════════════════════════════
# F. 并发与并行情况
# ══════════════════════════════════════════════════════════════
F_ITEMS = [
    CatalogItem("F1", "F", "资源竞争", "待定", "❓", testable=False),
    CatalogItem("F2", "F", "结果冲突", "Critic", "✅", layer="L2"),
    CatalogItem("F3", "F", "部分先完成等 fan-in", "fan-in 同步", "✅", layer="L2"),
    CatalogItem("F4", "F", "重启后状态不一致", "replay", "✅", layer="L2"),
    CatalogItem("F5", "F", "批次隔离", "child 隔离", "✅",
                l1_action="batch_hold",
                l1_params={"batch_id": "CASE-002", "reason": "批次隔离"},
                expected_signal="batch_signals", expected_sig_type="batch_hold",
                expected_payload_keys=["batch_id"], layer="L1+L2"),
    CatalogItem("F6", "F", "批次间依赖", "依赖检查", "❓", testable=False),
    CatalogItem("F7", "F", "并行回溯冲突", "待定", "❓", testable=False),
    CatalogItem("F8", "F", "并行重试放大", "待定", "❓", testable=False),
    CatalogItem("F9", "F", "fan-out 数量动态", "待定", "❓", testable=False),
    CatalogItem("F10", "F", "死锁等待", "超时打破", "❓", testable=False),
]

# ══════════════════════════════════════════════════════════════
# G. 时间与超时情况
# ══════════════════════════════════════════════════════════════
G_ITEMS = [
    CatalogItem("G1", "G", "节点硬超时", "unknown 判定", "✅", layer="L2"),
    CatalogItem("G2", "G", "软超时预警", "heartbeat 预警", "➖", testable=False),
    CatalogItem("G3", "G", "工作流整体超时", "abort", "❓", testable=False),
    CatalogItem("G4", "G", "长时间无进展", "stagnation 检测", "✅", layer="L2"),
    CatalogItem("G5", "G", "暂停过长", "待定", "❓", testable=False),
    CatalogItem("G6", "G", "心跳丢失", "heartbeat 保活", "✅", layer="L2"),
    CatalogItem("G7", "G", "调度延迟", "待定", "❓", testable=False),
    CatalogItem("G8", "G", "重试间隔过长", "待定", "❓", testable=False),
    CatalogItem("G9", "G", "截止时间临近", "待定", "❓", testable=False),
    CatalogItem("G10", "G", "时钟漂移", "待定", "❓", testable=False),
    CatalogItem("G11", "G", "历史回放超时", "待定", "❓", testable=False),
]

# ══════════════════════════════════════════════════════════════
# H. 外部依赖情况
# ══════════════════════════════════════════════════════════════
H_ITEMS = [
    CatalogItem("H1", "H", "LLM 限流", "retry backoff", "✅", layer="L2"),
    CatalogItem("H2", "H", "LLM 模型下线/变更", "ContextManifest 漂移", "❓", testable=False),
    CatalogItem("H3", "H", "LLM 降级响应", "待定", "❓", testable=False),
    CatalogItem("H4", "H", "外部存储不可用", "retry", "➖", testable=False),
    CatalogItem("H5", "H", "网络中断", "待定", "❓", testable=False),
    CatalogItem("H6", "H", "下游消费系统不可用", "待定", "❓", testable=False),
    CatalogItem("H7", "H", "MCP 工具不可用", "降级", "❓", testable=False),
    CatalogItem("H8", "H", "Label Studio 不可达", "降级", "❓", testable=False),
    CatalogItem("H9", "H", "Temporal Server 不可达", "重连", "❓", testable=False),
    CatalogItem("H10", "H", "Temporal Worker 崩溃", "重调度", "❓", testable=False),
]

# ══════════════════════════════════════════════════════════════
# I. 状态与一致性情况
# ══════════════════════════════════════════════════════════════
I_ITEMS = [
    CatalogItem("I1", "I", "crash 后状态未知", "ContinuableSnapshot", "✅", layer="L2"),
    CatalogItem("I2", "I", "replay 结果不一致", "幂等键/快照", "✅", layer="L2"),
    CatalogItem("I3", "I", "幂等键冲突", "幂等保护", "✅", layer="L2"),
    CatalogItem("I4", "I", "上下文漂移", "ContextManifest 冻结", "✅", layer="L2"),
    CatalogItem("I5", "I", "预算跟踪偏差", "待定", "❓", testable=False),
    CatalogItem("I6", "I", "审计记录缺失", "event sourcing", "✅", layer="L1+L2",
                l1_action="rework_node", l1_params={"node_id": "iqa_check"},
                expected_signal="rework_node"),
    CatalogItem("I7", "I", "双写不一致", "待定", "❓", testable=False),
    CatalogItem("I8", "I", "状态机非法迁移", "状态机校验", "❓", testable=False),
    CatalogItem("I9", "I", "信号乱序", "checkpoint 排序", "➖", testable=False),
    CatalogItem("I10", "I", "信号丢失", "Temporal signal 持久", "✅", layer="L2"),
]

# ══════════════════════════════════════════════════════════════
# J. 资源与成本情况
# ══════════════════════════════════════════════════════════════
J_ITEMS = [
    CatalogItem("J1", "J", "预算耗尽", "降级/中止", "✅", layer="L2"),
    CatalogItem("J2", "J", "配额耗尽", "等待/换模型", "➖", testable=False),
    CatalogItem("J3", "J", "存储耗尽", "清理/扩容", "❓", testable=False),
    CatalogItem("J4", "J", "计算资源不足", "排队/降级", "❓", testable=False),
    CatalogItem("J5", "J", "成本超预期", "预算预警", "✅", layer="L2"),
    CatalogItem("J6", "J", "长尾成本", "待定", "❓", testable=False),
    CatalogItem("J7", "J", "重试成本爆炸", "retry 上限", "✅", layer="L2"),
    CatalogItem("J8", "J", "资源泄漏", "待定", "❓", testable=False),
]

# ══════════════════════════════════════════════════════════════
# K. 治理触发情况 (执行中发现)
# ══════════════════════════════════════════════════════════════
K_ITEMS = [
    CatalogItem("K1", "K", "执行中发现标准过时", "standard_update", "🆕",
                l1_action="standard_update",
                l1_params={"standard_id": "AWS_D1.1", "old_version": "2020", "new_version": "2025"},
                expected_signal="batch_signals", expected_sig_type="standard_update",
                expected_payload_keys=["standard_id", "old_version", "new_version"]),
    CatalogItem("K2", "K", "执行中发现案例误导", "case_library_correction", "🆕",
                l1_action="case_library_correction",
                l1_params={"case_id": "CBR-042", "error_type": "wrong_conclusion", "correction": "结论应为NG"},
                expected_signal="batch_signals", expected_sig_type="case_library_correction",
                expected_payload_keys=["case_id", "error_type", "correction"], tier2=True),
    CatalogItem("K3", "K", "执行中发现规则有误", "待定", "❓", testable=False),
    CatalogItem("K4", "K", "执行中发现审查人配置错", "delegate_review", "🆕",
                l1_action="delegate_review",
                l1_params={"target": "node:ppa_check", "new_reviewer_id": "qc-002", "reason": "分到错的人"},
                expected_signal="batch_signals", expected_sig_type="delegate_review",
                expected_payload_keys=["target", "new_reviewer_id"]),
    CatalogItem("K5", "K", "执行中发现已提交决策误判", "revoke_approval", "🆕",
                l1_action="revoke_approval",
                l1_params={"node_id": "rda_check", "revoker_id": "qc-001", "reason": "事后发现误判", "artifact_status": "formal"},
                expected_signal="batch_signals", expected_sig_type="revoke_approval",
                expected_payload_keys=["node_id", "revoker_id"], tier2=True),
    CatalogItem("K6", "K", "执行中发现批次系统性误判", "revoke + batch", "🆕",
                l1_action="batch_hold",
                l1_params={"batch_id": "CASE-003", "reason": "系统性误判", "evidence": "整批判错"},
                expected_signal="batch_signals", expected_sig_type="batch_hold",
                expected_payload_keys=["batch_id", "reason"]),
    CatalogItem("K7", "K", "执行中发现上游根因误判", "revoke 连锁", "🆕",
                l1_action="revoke_approval",
                l1_params={"node_id": "iqa_check", "revoker_id": "qc-001", "reason": "根因误判", "artifact_status": "formal"},
                expected_signal="batch_signals", expected_sig_type="revoke_approval",
                expected_payload_keys=["node_id", "revoker_id"], tier2=True),
]

# ══════════════════════════════════════════════════════════════
# L. 上下文补充与版本更迭
# ══════════════════════════════════════════════════════════════
L_ITEMS = [
    CatalogItem("L1", "L", "注入事实修正", "inject_context", "✅",
                l1_action="inject_context",
                l1_params={"key": "parameter_adjustment", "value": "焊缝宽度阈值改为3.5mm"},
                expected_signal="batch_signals", expected_sig_type="inject_context",
                expected_payload_keys=["key", "value"]),
    CatalogItem("L2", "L", "注入约束变更", "待定", "❓", testable=False),
    CatalogItem("L3", "L", "注入临时注记", "notes", "✅",
                l1_action="inject_context",
                l1_params={"key": "session_note", "value": "此批次由客户特别关注"},
                expected_signal="batch_signals", expected_sig_type="inject_context",
                expected_payload_keys=["key", "value"]),
    CatalogItem("L4", "L", "注入上游结果修正", "下游感知", "❓", testable=False),
    CatalogItem("L5", "L", "ContextManifest 版本更迭", "待定", "❓", testable=False),
    CatalogItem("L6", "L", "已执行节点不回滚", "inject_context 已执行不回滚", "✅",
                l1_action="inject_context",
                l1_params={"key": "late_fact", "value": "补充信息"},
                expected_signal="batch_signals", expected_sig_type="inject_context",
                expected_payload_keys=["key", "value"]),
    CatalogItem("L7", "L", "未执行节点用新上下文", "待定", "✅",
                l1_action="inject_context",
                l1_params={"key": "early_fact", "value": "补充信息"},
                expected_signal="batch_signals", expected_sig_type="inject_context",
                expected_payload_keys=["key", "value"]),
    CatalogItem("L8", "L", "进行中节点如何处理", "批量应用", "✅",
                l1_action="inject_context",
                l1_params={"key": "mid_fact", "value": "补充信息"},
                expected_signal="batch_signals", expected_sig_type="inject_context",
                expected_payload_keys=["key", "value"]),
    CatalogItem("L9", "L", "注入与暂停叠加", "待定", "❓", testable=False),
    CatalogItem("L10", "L", "注入与回溯叠加", "待定", "❓", testable=False),
    CatalogItem("L11", "L", "多次注入冲突", "待定", "❓", testable=False),
    CatalogItem("L12", "L", "注入过时失效", "待定", "❓", testable=False),
    CatalogItem("L13", "L", "注入撤销", "待定", "❓", testable=False),
    CatalogItem("L14", "L", "注入致超预算", "安全压缩", "✅", layer="L2"),
    CatalogItem("L15", "L", "注入改变下游路由", "待定", "❓", testable=False),
    CatalogItem("L16", "L", "注入影响 Specialist 选择", "待定", "❓", testable=False),
]

# ══════════════════════════════════════════════════════════════
# M. 中断与节点重做
# ══════════════════════════════════════════════════════════════
M_ITEMS = [
    CatalogItem("M1", "M", "用户要求中断重做", "待定", "❓", testable=False),
    CatalogItem("M2", "M", "发现参数错中断", "rework", "✅",
                l1_action="rework_node", l1_params={"node_id": "iqa_check"},
                expected_signal="rework_node"),
    CatalogItem("M3", "M", "发现上下文过期中断", "重做取新输入", "❓", testable=False),
    CatalogItem("M4", "M", "中断在 PREPARE", "直接重来", "❓", testable=False),
    CatalogItem("M5", "M", "中断在 EXECUTE", "部分副作用处理", "❓", testable=False),
    CatalogItem("M6", "M", "中断在 VALIDATE", "draft 是否保留", "❓", testable=False),
    CatalogItem("M7", "M", "重做保留旧结果参考", "TaskBranch 对比", "➖", testable=False),
    CatalogItem("M8", "M", "重做用新参数 vs 旧参数", "待定", "❓", testable=False),
    CatalogItem("M9", "M", "中断丢弃未提交 draft", "直接弃", "❓", testable=False),
    CatalogItem("M10", "M", "中断副作用补偿", "Saga", "✅", layer="L2"),
    CatalogItem("M11", "M", "中断单节点 vs 子树", "待定", "❓", testable=False),
    CatalogItem("M12", "M", "中断与并行兄弟", "待定", "❓", testable=False),
    CatalogItem("M13", "M", "中断后重做 vs 回溯重做", "待定", "❓", testable=False),
    CatalogItem("M14", "M", "中断与 checkpoint", "heartbeat cancel", "✅",
                l1_action="cancel", expected_signal="cancel_by_user"),
]

# ══════════════════════════════════════════════════════════════
# N. 工作流更改 (modify_spec 细分)
# ══════════════════════════════════════════════════════════════
_BASE_NODES = [
    {"node_id": "iqa_check", "depends_on": []},
    {"node_id": "ppa_check", "depends_on": ["iqa_check"]},
    {"node_id": "mea_check", "depends_on": ["ppa_check"]},
]
_N_ITEMS = [
    CatalogItem("N1", "N", "改参数不改拓扑", "Update API (热更新)", "✅",
                l1_action="modify",
                l1_params={"new_spec": {"nodes": [
                    {"node_id": "iqa_check", "depends_on": [], "input": {"threshold": 0.85}},
                    {"node_id": "ppa_check", "depends_on": ["iqa_check"]},
                    {"node_id": "mea_check", "depends_on": ["ppa_check"]},
                ]}},
                expected_signal="", layer="L1+L2"),  # param change: no cancel
    CatalogItem("N2", "N", "改拓扑加节点", "Cancel+Relaunch", "✅",
                l1_action="modify",
                l1_params={"new_spec": {"nodes": _BASE_NODES + [{"node_id": "new_node", "depends_on": ["mea_check"]}]}},
                expected_signal="modify_spec"),
    CatalogItem("N3", "N", "改拓扑删节点", "Cancel+Relaunch", "✅",
                l1_action="modify",
                l1_params={"new_spec": {"nodes": _BASE_NODES[:2]}},
                expected_signal="modify_spec"),
    CatalogItem("N4", "N", "改节点 capability", "待定", "❓", testable=False),
    CatalogItem("N5", "N", "改 Prompt/参数模板", "Update", "➖", testable=False),
    CatalogItem("N6", "N", "改 review_policy", "待定", "❓", testable=False),
    CatalogItem("N7", "N", "改依赖关系", "Cancel+Relaunch", "✅",
                l1_action="modify",
                l1_params={"new_spec": {"nodes": [
                    {"node_id": "iqa_check", "depends_on": []},
                    {"node_id": "mea_check", "depends_on": ["iqa_check"]},
                    {"node_id": "ppa_check", "depends_on": ["mea_check"]},
                ]}},
                expected_signal="modify_spec"),
    CatalogItem("N8", "N", "改条件分支逻辑", "Cancel+Relaunch", "✅",
                l1_action="modify",
                l1_params={"new_spec": {"nodes": _BASE_NODES}},
                expected_signal="modify_spec"),
    CatalogItem("N9", "N", "改预算", "待定", "❓", testable=False),
    CatalogItem("N10", "N", "改执行顺序", "Cancel+Relaunch", "✅",
                l1_action="modify",
                l1_params={"new_spec": {"nodes": [
                    {"node_id": "ppa_check", "depends_on": []},
                    {"node_id": "iqa_check", "depends_on": ["ppa_check"]},
                    {"node_id": "mea_check", "depends_on": ["iqa_check"]},
                ]}},
                expected_signal="modify_spec"),
    CatalogItem("N11", "N", "已完成节点保留", "Cancel 保留已完成", "✅", layer="L2"),
    CatalogItem("N12", "N", "进行中节点如何处理", "待定", "❓", testable=False),
    CatalogItem("N13", "N", "下游感知新 spec", "新 PlanVersion", "✅", layer="L2"),
    CatalogItem("N14", "N", "新 PlanVersion 版本管理", "待定", "❓", testable=False),
    CatalogItem("N15", "N", "部分改 vs 全改", "影响分析", "✅", layer="L1+L2"),
    CatalogItem("N16", "N", "改的审批流程", "PlanRevisionProposal", "✅", layer="L2"),
    CatalogItem("N17", "N", "改后 replay 一致性", "不可变+版本", "✅", layer="L2"),
]

# ══════════════════════════════════════════════════════════════
# O. 人工审查
# ══════════════════════════════════════════════════════════════
O_ITEMS = [
    CatalogItem("O1", "O", "被动·REQUIRED 强制审查", "ReviewPolicy REQUIRED", "✅",
                l1_action="human_review",
                l1_params={"node_id": "rda_check", "decision": "approve"},
                expected_signal="batch_signals", expected_sig_type="human_review",
                expected_payload_keys=["node_id", "decision"]),
    CatalogItem("O2", "O", "被动·CONDITIONAL 触发", "MARGINAL", "✅",
                l1_action="human_review",
                l1_params={"node_id": "ppa_check", "decision": "rework"},
                expected_signal="batch_signals", expected_sig_type="human_review",
                expected_payload_keys=["node_id", "decision"]),
    CatalogItem("O3", "O", "被动·失败 escalate", "escalate", "✅",
                l1_action="human_review",
                l1_params={"node_id": "mea_check", "decision": "escalate"},
                expected_signal="batch_signals", expected_sig_type="human_review",
                expected_payload_keys=["node_id", "decision"]),
    CatalogItem("O4", "O", "主动·人发起审查某节点", "待定", "❓", testable=False),
    CatalogItem("O5", "O", "主动·人介入审查整批", "待定", "❓", testable=False),
    CatalogItem("O6", "O", "审查时机·执行前", "待定", "❓", testable=False),
    CatalogItem("O7", "O", "审查时机·执行中", "待定", "❓", testable=False),
    CatalogItem("O8", "O", "审查时机·提交后", "revoke_approval", "🆕",
                l1_action="revoke_approval",
                l1_params={"node_id": "rda_check", "revoker_id": "qc-001", "reason": "事后审查发现误判", "artifact_status": "formal"},
                expected_signal="batch_signals", expected_sig_type="revoke_approval",
                expected_payload_keys=["node_id", "revoker_id"], tier2=True),
    CatalogItem("O9", "O", "审查范围·单节点", "human_review", "✅",
                l1_action="human_review",
                l1_params={"node_id": "vda_check", "decision": "modify_downstream"},
                expected_signal="batch_signals", expected_sig_type="human_review",
                expected_payload_keys=["node_id", "decision"]),
    CatalogItem("O10", "O", "审查范围·子树", "待定", "❓", testable=False),
    CatalogItem("O11", "O", "审查结论·五决策", "human_review", "✅",
                l1_action="human_review",
                l1_params={"node_id": "rva_check", "decision": "reject"},
                expected_signal="batch_signals", expected_sig_type="human_review",
                expected_payload_keys=["node_id", "decision"]),
    CatalogItem("O12", "O", "审查结论·override 强制", "ground_truth_override", "🆕",
                l1_action="ground_truth_override",
                l1_params={"node_id": "rda_check", "inspector_id": "qc-003", "forced_verdict": "NG", "reason": "质检员判定"},
                expected_signal="batch_signals", expected_sig_type="ground_truth_override",
                expected_payload_keys=["node_id", "inspector_id", "forced_verdict"], tier2=True),
    CatalogItem("O13", "O", "审查结论·delegate 转交", "delegate_review", "🆕",
                l1_action="delegate_review",
                l1_params={"target": "node:rda_check", "new_reviewer_id": "qc-005", "reason": "转交审查"},
                expected_signal="batch_signals", expected_sig_type="delegate_review",
                expected_payload_keys=["target", "new_reviewer_id"]),
    CatalogItem("O14", "O", "审查人变更", "delegate_review", "🆕",
                l1_action="delegate_review",
                l1_params={"target": "node:ppa_check", "new_reviewer_id": "qc-006", "reason": "重派审查人"},
                expected_signal="batch_signals", expected_sig_type="delegate_review",
                expected_payload_keys=["target", "new_reviewer_id"]),
    CatalogItem("O15", "O", "审查与暂停叠加", "待定", "❓", testable=False),
    CatalogItem("O16", "O", "多人会签审查", "待定", "❓", testable=False),
    CatalogItem("O17", "O", "审查超时无人响应", "待定", "❓", testable=False),
    CatalogItem("O18", "O", "多审查人意见冲突", "待定", "❓", testable=False),
    CatalogItem("O19", "O", "审查追溯留痕", "DecisionRecord", "✅",
                l1_action="human_review",
                l1_params={"node_id": "rda_check", "decision": "approve"},
                expected_signal="batch_signals", expected_sig_type="human_review",
                expected_payload_keys=["node_id", "decision"], layer="L1+L2"),
    CatalogItem("O20", "O", "审查责任归属", "responsibility_owner", "🆕",
                l1_action="ground_truth_override",
                l1_params={"node_id": "rda_check", "inspector_id": "qc-003", "forced_verdict": "OK", "reason": "责任归属"},
                expected_signal="batch_signals", expected_sig_type="ground_truth_override",
                expected_payload_keys=["node_id", "inspector_id", "forced_verdict"], tier2=True),
]

# ══════════════════════════════════════════════════════════════
# P. 工作流重新评估
# ══════════════════════════════════════════════════════════════
P_ITEMS = [
    CatalogItem("P1", "P", "质量趋势下降触发重评", "趋势预警+重评", "✅", layer="L2"),
    CatalogItem("P2", "P", "标准更新触发重评", "replay 重审", "🆕",
                l1_action="standard_update",
                l1_params={"standard_id": "ISO_5817", "old_version": "2017", "new_version": "2023"},
                expected_signal="batch_signals", expected_sig_type="standard_update",
                expected_payload_keys=["standard_id", "old_version", "new_version"]),
    CatalogItem("P3", "P", "案例纠错触发重评", "引用图重评", "🆕",
                l1_action="case_library_correction",
                l1_params={"case_id": "CBR-077", "error_type": "mislabel", "correction": "标签应为NG"},
                expected_signal="batch_signals", expected_sig_type="case_library_correction",
                expected_payload_keys=["case_id", "error_type", "correction"], tier2=True),
    CatalogItem("P4", "P", "批量异常触发重评", "batch_hold", "🆕",
                l1_action="batch_hold",
                l1_params={"batch_id": "CASE-005", "reason": "批量异常"},
                expected_signal="batch_signals", expected_sig_type="batch_hold",
                expected_payload_keys=["batch_id"]),
    CatalogItem("P5", "P", "根因追溯触发重评", "revoke 连锁", "🆕",
                l1_action="revoke_approval",
                l1_params={"node_id": "iqa_check", "revoker_id": "qc-001", "reason": "根因追溯", "artifact_status": "formal"},
                expected_signal="batch_signals", expected_sig_type="revoke_approval",
                expected_payload_keys=["node_id"], tier2=True),
    CatalogItem("P6", "P", "模型漂移触发重评", "ContextManifest 冻结", "✅", layer="L2"),
    CatalogItem("P7", "P", "累计偏差触发重评", "待定", "❓", testable=False),
    CatalogItem("P8", "P", "外部环境变更触发重评", "待定", "❓", testable=False),
    CatalogItem("P9", "P", "周期性审计触发重评", "离线重评", "❓", testable=False),
    CatalogItem("P10", "P", "抽检发现触发重评", "待定", "❓", testable=False),
    CatalogItem("P11", "P", "用户投诉触发重评", "待定", "❓", testable=False),
    CatalogItem("P12", "P", "版本回归触发重评", "t-test 检测", "✅", layer="L2"),
    CatalogItem("P13", "P", "重评范围·单节点", "-", "✅", layer="L2"),
    CatalogItem("P14", "P", "重评范围·子树", "待定", "❓", testable=False),
    CatalogItem("P15", "P", "重评范围·整工作流", "待定", "❓", testable=False),
    CatalogItem("P16", "P", "重评范围·跨工作流", "引用图", "✅", layer="L2"),
    CatalogItem("P17", "P", "重评方法·重放重评", "Temporal replay", "✅", layer="L2"),
    CatalogItem("P18", "P", "重评方法·基准对比", "待定", "❓", testable=False),
    CatalogItem("P19", "P", "重评方法·回归测试", "DSPy eval", "➖", testable=False),
    CatalogItem("P20", "P", "重评方法·影响面分析", "BFS", "✅", layer="L2"),
    CatalogItem("P21", "P", "重评时间窗口·全历史", "离线", "❓", testable=False),
    CatalogItem("P22", "P", "重评时间窗口·最近N次", "待定", "❓", testable=False),
    CatalogItem("P23", "P", "重评时间窗口·特定时段", "待定", "❓", testable=False),
    CatalogItem("P24", "P", "重评版本·旧标准评历史", "provenance", "✅", layer="L2"),
    CatalogItem("P25", "P", "重评版本·新标准评历史", "replay 新标", "🆕",
                l1_action="standard_update",
                l1_params={"standard_id": "AWS_D1.1", "old_version": "2020", "new_version": "2025"},
                expected_signal="batch_signals", expected_sig_type="standard_update",
                expected_payload_keys=["standard_id"], layer="L1+L2"),
    CatalogItem("P26", "P", "重评结论·维持", "-", "✅", layer="L2"),
    CatalogItem("P27", "P", "重评结论·局部修", "revoke 部分", "🆕",
                l1_action="revoke_approval",
                l1_params={"node_id": "rda_check", "revoker_id": "qc-001", "reason": "局部失效", "artifact_status": "formal"},
                expected_signal="batch_signals", expected_sig_type="revoke_approval",
                expected_payload_keys=["node_id"], tier2=True),
    CatalogItem("P28", "P", "重评结论·全重审", "批量 revoke", "🆕",
                l1_action="revoke_approval",
                l1_params={"node_id": "iqa_check", "revoker_id": "qc-001", "reason": "全失效", "artifact_status": "formal"},
                expected_signal="batch_signals", expected_sig_type="revoke_approval",
                expected_payload_keys=["node_id"], tier2=True),
    CatalogItem("P29", "P", "重评结论·废弃", "待定", "❓", testable=False),
    CatalogItem("P30", "P", "重评成本控制", "待定", "❓", testable=False),
    CatalogItem("P31", "P", "重评优先级排序", "待定", "❓", testable=False),
]

# ══════════════════════════════════════════════════════════════
# Q. 经验回流
# ══════════════════════════════════════════════════════════════
Q_ITEMS = [
    CatalogItem("Q1", "Q", "单次经验回流", "Reflexion note", "✅", layer="L2"),
    CatalogItem("Q2", "Q", "批量经验回流", "Pattern 提取", "✅", layer="L2"),
    CatalogItem("Q3", "Q", "成功经验固化", "Pattern 固化", "✅", layer="L2"),
    CatalogItem("Q4", "Q", "失败教训回流", "Reflexion", "✅", layer="L2"),
    CatalogItem("Q5", "Q", "回流注入时机", "Task Ledger facts", "✅", layer="L2"),
    CatalogItem("Q6", "Q", "回流注入哪个账本", "Task Ledger", "✅", layer="L2"),
    CatalogItem("Q7", "Q", "经验置信度", "置信度计算", "✅", layer="L2"),
    CatalogItem("Q8", "Q", "经验置信度阈值", "待定", "❓", testable=False),
    CatalogItem("Q9", "Q", "新旧经验冲突", "待定", "❓", testable=False),
    CatalogItem("Q10", "Q", "经验过时淘汰", "待定", "❓", testable=False),
    CatalogItem("Q11", "Q", "经验分级·通用", "Semantic", "✅", layer="L2"),
    CatalogItem("Q12", "Q", "经验分级·特定", "Episodic", "✅", layer="L2"),
    CatalogItem("Q13", "Q", "经验副作用·污染", "case 纠错", "🆕",
                l1_action="case_library_correction",
                l1_params={"case_id": "CBR-099", "error_type": "wrong_conclusion", "correction": "污染经验纠正"},
                expected_signal="batch_signals", expected_sig_type="case_library_correction",
                expected_payload_keys=["case_id"], tier2=True),
    CatalogItem("Q14", "Q", "经验回流审批", "待定", "❓", testable=False),
    CatalogItem("Q15", "Q", "跨工作流共享经验", "L3/L4 Memory", "✅", layer="L2"),
    CatalogItem("Q16", "Q", "经验与标准更新联动", "standard 联动", "🆕",
                l1_action="standard_update",
                l1_params={"standard_id": "ISO_5817", "old_version": "2017", "new_version": "2023"},
                expected_signal="batch_signals", expected_sig_type="standard_update",
                expected_payload_keys=["standard_id"]),
    CatalogItem("Q17", "Q", "经验与案例纠错联动", "case 联动", "🆕",
                l1_action="case_library_correction",
                l1_params={"case_id": "CBR-100", "error_type": "mislabel", "correction": "联动纠正"},
                expected_signal="batch_signals", expected_sig_type="case_library_correction",
                expected_payload_keys=["case_id"], tier2=True),
    CatalogItem("Q18", "Q", "override 作为经验回流", "校准对回流", "🆕",
                l1_action="ground_truth_override",
                l1_params={"node_id": "rda_check", "inspector_id": "qc-003", "forced_verdict": "OK", "reason": "校准信号"},
                expected_signal="batch_signals", expected_sig_type="ground_truth_override",
                expected_payload_keys=["node_id", "inspector_id", "forced_verdict"], tier2=True),
    CatalogItem("Q19", "Q", "经验影响 Specialist 选择", "动态匹配", "✅", layer="L2"),
    CatalogItem("Q20", "Q", "经验影响模型路由", "路由调整", "✅", layer="L2"),
    CatalogItem("Q21", "Q", "经验影响工具组合", "工具调整", "✅", layer="L2"),
    CatalogItem("Q22", "Q", "经验自评估质量", "趋势追踪", "✅", layer="L2"),
    CatalogItem("Q23", "Q", "经验持久化层级", "分层记忆", "✅", layer="L2"),
    CatalogItem("Q24", "Q", "经验检索召回", "CBR+RAG", "✅", layer="L2"),
    CatalogItem("Q25", "Q", "经验冷启动", "通用规则兜底", "❓", testable=False),
]

# ══════════════════════════════════════════════════════════════
# S. 跨工作流编排与长跑
# ══════════════════════════════════════════════════════════════
S_ITEMS = [
    CatalogItem("S1", "S", "子工作流嵌套", "待定", "❓", testable=False),
    CatalogItem("S2", "S", "子工作流错误上抛", "待定", "❓", testable=False),
    CatalogItem("S3", "S", "子工作流取消波及父", "待定", "❓", testable=False),
    CatalogItem("S4", "S", "父取消波及子", "cascade cancel", "❓", testable=False),
    CatalogItem("S5", "S", "子工作流结果回传", "待定", "❓", testable=False),
    CatalogItem("S6", "S", "子工作流超预算", "待定", "❓", testable=False),
    CatalogItem("S7", "S", "长跑工作流跨重启", "replay+snapshot", "✅", layer="L2"),
    CatalogItem("S8", "S", "长跑状态归档", "归档/裁剪", "❓", testable=False),
    CatalogItem("S9", "S", "定时巡检型工作流", "ContinueAsNew", "❓", testable=False),
    CatalogItem("S10", "S", "工作流间数据依赖", "artifact 共享", "❓", testable=False),
    CatalogItem("S11", "S", "工作流间状态共享", "待定", "❓", testable=False),
    CatalogItem("S12", "S", "多工作流并发竞争", "排队/背压", "❓", testable=False),
    CatalogItem("S13", "S", "工作流版本升级时旧 Run 在跑", "待定", "❓", testable=False),
    CatalogItem("S14", "S", "跨工作流取消联动", "待定", "❓", testable=False),
    CatalogItem("S15", "S", "父子信号传递", "待定", "❓", testable=False),
]

# ══════════════════════════════════════════════════════════════
# T. 安全合规与可观测
# ══════════════════════════════════════════════════════════════
T_ITEMS = [
    CatalogItem("T1", "T", "敏感数据进 LLM", "脱敏/过滤", "❓", testable=False),
    CatalogItem("T2", "T", "越权访问 artifact", "RBAC", "❓", testable=False),
    CatalogItem("T3", "T", "节点越权执行", "能力白名单", "❓", testable=False),
    CatalogItem("T4", "T", "审计事件缺失", "全链审计", "✅", layer="L1+L2",
                l1_action="rework_node", l1_params={"node_id": "iqa_check"},
                expected_signal="rework_node"),
    CatalogItem("T5", "T", "审计防篡改", "append-only+签名", "❓", testable=False),
    CatalogItem("T6", "T", "执行 trace 丢失", "全程 trace", "❓", testable=False),
    CatalogItem("T7", "T", "日志缺失/截断", "持久化日志", "❓", testable=False),
    CatalogItem("T8", "T", "replay 调试", "event history", "✅", layer="L2"),
    CatalogItem("T9", "T", "为什么得出此结论", "记录推理链", "❓", testable=False),
    CatalogItem("T10", "T", "敏感结果泄露", "输出脱敏", "❓", testable=False),
    CatalogItem("T11", "T", "审计合规报表", "审计导出", "✅", layer="L2"),
    CatalogItem("T12", "T", "数据驻留合规", "待定", "❓", testable=False),
    CatalogItem("T13", "T", "指标采集缺失", "指标埋点", "❓", testable=False),
    CatalogItem("T14", "T", "链路追踪断点", "trace 链路", "❓", testable=False),
]

# ══════════════════════════════════════════════════════════════
# U. 启动前边界态
# ══════════════════════════════════════════════════════════════
U_ITEMS = [
    CatalogItem("U1", "U", "spec 为空", "拒绝", "❓", testable=False),
    CatalogItem("U2", "U", "权限不足", "RBAC", "❓", testable=False),
    CatalogItem("U3", "U", "依赖能力未注册", "预检", "❓", testable=False),
    CatalogItem("U4", "U", "预算未配/不足", "预算门控", "✅", layer="L2"),
    CatalogItem("U5", "U", "输入数据校验不过", "拒绝", "❓", testable=False),
    CatalogItem("U6", "U", "spec 与代码版本不匹配", "版本校验", "❓", testable=False),
    CatalogItem("U7", "U", "资源预检失败", "预检", "❓", testable=False),
    CatalogItem("U8", "U", "冻结版本冲突", "待定", "❓", testable=False),
]

# ══════════════════════════════════════════════════════════════
# V. 交付后与终态边界态
# ══════════════════════════════════════════════════════════════
V_ITEMS = [
    CatalogItem("V1", "V", "结果交付失败", "retry/queue", "❓", testable=False),
    CatalogItem("V2", "V", "交付格式转换", "转换层", "❓", testable=False),
    CatalogItem("V3", "V", "下游拒收结果", "待定", "❓", testable=False),
    CatalogItem("V4", "V", "交付回执缺失", "确认机制", "❓", testable=False),
    CatalogItem("V5", "V", "终态清理", "清理", "❓", testable=False),
    CatalogItem("V6", "V", "临时 artifact 归档", "保留期策略", "❓", testable=False),
    CatalogItem("V7", "V", "终态导出报告", "报告生成", "✅", layer="L2"),
    CatalogItem("V8", "V", "终态经验回流触发", "经验回流", "✅", layer="L2"),
    CatalogItem("V9", "V", "终态成功但质量存疑", "待定", "❓", testable=False),
    CatalogItem("V10", "V", "部分完成即交付", "待定", "❓", testable=False),
]

# ══════════════════════════════════════════════════════════════
# 汇总
# ══════════════════════════════════════════════════════════════
ALL_ITEMS: list[CatalogItem] = (
    A_ITEMS + B_ITEMS + C_ITEMS + D_ITEMS + E_ITEMS + F_ITEMS
    + G_ITEMS + H_ITEMS + I_ITEMS + J_ITEMS + K_ITEMS + L_ITEMS
    + _N_ITEMS + O_ITEMS + P_ITEMS + Q_ITEMS + S_ITEMS + T_ITEMS
    + U_ITEMS + V_ITEMS
)

# 按 category 分组
BY_CATEGORY: dict[str, list[CatalogItem]] = {}
for _item in ALL_ITEMS:
    BY_CATEGORY.setdefault(_item.category, []).append(_item)

# 仅可测 (✅ + 🆕)
TESTABLE_ITEMS = [i for i in ALL_ITEMS if i.testable]
# 仅 L1 可测 (有 l1_action)
L1_ITEMS = [i for i in TESTABLE_ITEMS if i.l1_action]
# 仅 L2 可测 (layer 含 L2)
L2_ITEMS = [i for i in TESTABLE_ITEMS if "L2" in i.layer]


def stats() -> dict[str, int]:
    total = len(ALL_ITEMS)
    testable = len(TESTABLE_ITEMS)
    l1 = len(L1_ITEMS)
    l2 = len(L2_ITEMS)
    by_cov = {}
    for item in ALL_ITEMS:
        by_cov[item.coverage] = by_cov.get(item.coverage, 0) + 1
    return {
        "total": total,
        "testable": testable,
        "l1_testable": l1,
        "l2_testable": l2,
        "by_coverage": by_cov,
        "categories": len(BY_CATEGORY),
    }


if __name__ == "__main__":
    s = stats()
    print(f"清单总计: {s['total']} 条 / {s['categories']} 大类")
    print(f"可测: {s['testable']} 条 (L1={s['l1_testable']}, L2={s['l2_testable']})")
    print(f"覆盖度分布: {s['by_coverage']}")
