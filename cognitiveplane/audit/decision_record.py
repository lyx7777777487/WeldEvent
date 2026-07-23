"""Op 27: DecisionRecord - immutable audit trail (event sourcing).

Source: Event Sourcing (Greg Young, ~2005) + ISO 3834 compliance.
Every decision point in the workflow produces an immutable record.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DecisionType(str, Enum):
    """Types of decisions recorded in the audit trail."""
    NODE_REVIEW = "node_review"                # Human review decision
    ON_FAILURE = "on_failure"                  # Failure handling decision
    CONDITION_EVAL = "condition_eval"          # Condition branch decision
    REPLAN = "replan"                          # Stagnation replan decision
    REWORK = "rework"                          # Rework triggered
    SAGA_COMPENSATE = "saga_compensate"        # Saga compensation
    PLAN_REVISION = "plan_revision"            # Plan version change
    SIGNAL = "signal"                          # User signal (pause/resume/cancel)
    BUDGET_EXCEEDED = "budget_exceeded"        # Token budget exceeded
    REVOKE_APPROVAL = "revoke_approval"        # Op-新: 撤回已提交决策(误判)
    BATCH_HOLD = "batch_hold"                  # Op-新: 批次冻结(质量可疑隔离)
    PAUSE_SCOPE = "pause_scope"                # Op-新: 分级暂停(STATION/BATCH)
    RELABEL_REQUEST = "relabel_request"            # Op-新: 重新标注(标签专项rework)
    GROUND_TRUTH_OVERRIDE = "ground_truth_override"  # Op-新: 人工强制覆盖(绕过evaluator)
    DELEGATE_REVIEW = "delegate_review"              # Op-新: 审查权转移(reviewer重派)
    STANDARD_UPDATE = "standard_update"              # Op-新[M]: 标准库版本更新
    CASE_LIBRARY_CORRECTION = "case_library_correction"  # Op-新[M]: 案例库纠错


@dataclass(frozen=True)
class DecisionRecord:
    """Op 27: Immutable decision record.

    Frozen to enforce append-only semantics.
    Once created, a decision cannot be modified - only superseded by a new record.
    """
    record_id: str
    decision_type: DecisionType
    workflow_id: str
    node_id: str
    decision: str              # human-readable decision summary
    rationale: str = ""        # why this decision was made
    actor: str = ""            # "user" | "system" | "llm"
    context: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0  # sandbox-safe; caller (workflow) passes workflow.now()
    # Content hash for integrity verification
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not self.content_hash:
            content = json.dumps({
                "type": self.decision_type.value,
                "wf": self.workflow_id,
                "node": self.node_id,
                "decision": self.decision,
                "rationale": self.rationale,
                "actor": self.actor,
                "ts": self.timestamp,
            }, sort_keys=True)
            object.__setattr__(self, "content_hash",
                             hashlib.sha256(content.encode()).hexdigest()[:16])

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "decision_type": self.decision_type.value,
            "workflow_id": self.workflow_id,
            "node_id": self.node_id,
            "decision": self.decision,
            "rationale": self.rationale,
            "actor": self.actor,
            "context": self.context,
            "timestamp": self.timestamp,
            "content_hash": self.content_hash,
        }


class AuditTrail:
    """Op 27: Append-only audit trail for a workflow execution.

    Enforces uniqueness of record_id. Supports queries for compliance
    reporting and ISO 3834 audit requirements.
    """

    def __init__(self, workflow_id: str) -> None:
        self.workflow_id = workflow_id
        self._records: list[DecisionRecord] = []
        self._record_ids: set[str] = set()

    def record(self, decision_type: DecisionType, node_id: str,
               decision: str, rationale: str = "", actor: str = "system",
               context: dict[str, Any] | None = None,
               now_ts: float = 0.0) -> DecisionRecord:
        """Append a new decision record.

        now_ts: 调用方传入的时间戳 (workflow 用 workflow.now().timestamp(),
                activity/非 workflow 用 time.time()). 默认 0.0 - 沙箱安全.
        """
        record_id = f"dec_{len(self._records) + 1}"
        if record_id in self._record_ids:
            record_id = f"dec_{len(self._records) + 1}_{int(now_ts * 1000) % 100000}"

        record = DecisionRecord(
            record_id=record_id,
            decision_type=decision_type,
            workflow_id=self.workflow_id,
            node_id=node_id,
            decision=decision,
            rationale=rationale,
            actor=actor,
            context=context or {},
            timestamp=now_ts,
        )
        self._records.append(record)
        self._record_ids.add(record.record_id)
        return record

    def query_by_type(self, dtype: DecisionType) -> list[DecisionRecord]:
        return [r for r in self._records if r.decision_type == dtype]

    def query_by_node(self, node_id: str) -> list[DecisionRecord]:
        return [r for r in self._records if r.node_id == node_id]

    def verify_integrity(self) -> bool:
        """Verify all records have valid content hashes."""
        for r in self._records:
            expected = DecisionRecord(
                record_id=r.record_id,
                decision_type=r.decision_type,
                workflow_id=r.workflow_id,
                node_id=r.node_id,
                decision=r.decision,
                rationale=r.rationale,
                actor=r.actor,
                context=r.context,
                timestamp=r.timestamp,
            )
            if expected.content_hash != r.content_hash:
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "record_count": len(self._records),
            "records": [r.to_dict() for r in self._records],
        }

    def __len__(self) -> int:
        return len(self._records)


@dataclass
class RevokeApprovalRecord:
    """撤回已批准决策的记录 (Op-新 revoke_approval).

    当 DecisionRecord 已 COMMIT 后发现误判, 用此记录撤销.
    对应状态机: COMMITTED -> REVOKED.

    核心不变量:
      - 不删原 DecisionRecord (append-only), 只标 REVOKED
      - artifact 状态分叉: draft 走 rework, formal 发更正版 v2 + 通知下游
      - 下游已消费的 artifact 用 BFS 标记 AFFECTED_BY_REVOKE (不自动重跑, 留人工)

    Source: event sourcing (Greg Young) + Saga 补偿 + ArtifactVersion 版本化.
    责任认定: revoker_id 是当前责任主体 (原 approver 在原 DecisionRecord).
    """
    revoked_node_id: str
    revoker_id: str
    reason: str
    # artifact_status 决定撤销复杂度:
    #   draft    -> 走 rework_node 下游闭包 (回退第二级)
    #   formal    -> Saga 补偿 + 发更正版 v2 + 通知 (回退第三级, 最重)
    #   external  -> 已离系统, 只能发更正通知 (不可 undo)
    artifact_status: str  # "draft" | "formal" | "external"
    # 下游已消费该节点 artifact 的节点 (BFS 依赖图找出)
    affected_downstream: list[str] = field(default_factory=list)
    # 补偿动作记录 (formal/external 时)
    compensation_actions: list[dict[str, Any]] = field(default_factory=list)
    # 更正版 artifact 的 version (formal/external 时新建 v2)
    correction_artifact_version: str | None = None
    # 已通知的下游消费者 (formal/external 时)
    notified_consumers: list[str] = field(default_factory=list)
    # 关联的原 DecisionRecord record_id (责任链追溯)
    original_decision_id: str | None = None
    timestamp: float = 0.0  # 由调用方传入 (workflow 用 workflow.now(), activity 用 time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "revoked_node_id": self.revoked_node_id,
            "revoker_id": self.revoker_id,
            "reason": self.reason,
            "artifact_status": self.artifact_status,
            "affected_downstream": self.affected_downstream,
            "compensation_actions": self.compensation_actions,
            "correction_artifact_version": self.correction_artifact_version,
            "notified_consumers": self.notified_consumers,
            "original_decision_id": self.original_decision_id,
            "timestamp": self.timestamp,
        }


@dataclass
class BatchHoldRecord:
    """批次冻结记录 (Op-新 batch_hold).

    可疑批次扣留待查, 不阻塞其他批次. 与 pause_scope 区别:
    pause_scope 停止执行(halt); batch_hold 隔离结果(fence) - 批次可能
    已执行完, 但 artifact 冻结在 HELD 态, 不进 fan-in 聚合、不转正式.

    核心不变量:
      - HELD 是隔离非失败: 可 release 回 COMMITTED, 或 quarantine 转 FAILED
      - 不影响其他批次 (只动指定 batch_id 的节点)
      - 与 DLQ 区别: DLQ 是失败隔离(fan-in 用默认值); batch_hold 是质量可疑隔离(路由复查)

    Source: 制造质量体系扣留/隔离(quarantine) + [23] fan-out 隔离 +
            [24] DLQ 模式(语义借为质量扣留) + [26] ArtifactVersion 门控.
    """
    batch_id: str
    held_node_ids: list[str] = field(default_factory=list)
    reason: str = ""
    evidence: str = ""
    # 决议结果 (初值 None, 三分支决议后填)
    #   released | reworked | quarantined
    resolution: str | None = None
    # 冻结的 artifact 版本 (formal 转正式的 artifact, held 后不可 promote)
    held_artifact_ids: list[str] = field(default_factory=list)
    timestamp: float = 0.0  # sandbox-safe; 调用方传 workflow.now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "held_node_ids": self.held_node_ids,
            "reason": self.reason,
            "evidence": self.evidence,
            "resolution": self.resolution,
            "held_artifact_ids": self.held_artifact_ids,
            "timestamp": self.timestamp,
        }


@dataclass
class PauseScopeRecord:
    """分级暂停记录 (Op-新 pause_scope).

    现有 pause/resume/cancel 是整 Run 级; pause_scope 加 scope 维度,
    只暂停指定工位(节点)或批次(fan-out 并行组), 其余 workflow 继续.
    与 batch_hold 区别: pause_scope 停止执行(halt); batch_hold 隔离结果(fence).

    scope_type:
      WORKFLOW -> 退化为现有整 Run pause
      STATION  -> 暂停单节点(工位), 同 Run 其它节点继续推进
      BATCH    -> 暂停某批次的全部节点(按 case_id), sibling 批次继续

    核心不变量: pause 从不删历史, 只挂起调度 (resume 从 checkpoint 续跑).

    Source: [19] pause/resume/cancel 三分法(分级版) + [37] 信号批量 +
            [23] fan-out 隔离 + [8] ContinuableSnapshot + [16] 不可删历史.
    """
    scope_type: str          # "workflow" | "station" | "batch"
    scope_id: str            # WORKFLOW: workflow_id; STATION: node_id; BATCH: case_id
    reason: str = ""
    initiator: str = "user"
    paused_at_checkpoint: str = ""   # 在哪个 checkpoint 生效 (节点边界)
    # resume 后置 false; pause 时 true
    paused: bool = True
    timestamp: float = 0.0  # sandbox-safe; 调用方传 workflow.now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_type": self.scope_type,
            "scope_id": self.scope_id,
            "reason": self.reason,
            "initiator": self.initiator,
            "paused_at_checkpoint": self.paused_at_checkpoint,
            "paused": self.paused,
            "timestamp": self.timestamp,
        }



@dataclass
class RelabelRecord:
    """重新标注记录 (Op-新 relabel_request).

    标签错但执行结果对 -> 只回标注(Annot)环节, 不重跑分析(IQA/PPA).
    是 rework_node 的标签专项版, 因"只标签变、结果不变"而比普通 rework 更省.

    核心不变量:
      - 只重跑产出 label 的 Annot 节点 + 依赖 label 的下游
      - 复用既有执行结果(reused_result_artifact_id), 不重跑昂贵分析
      - 新 label = 新 ArtifactVersion, 链到同一执行结果(provenance 保留)

    Source: [20] 依赖污染 BFS(标签专项版) + [3][7] NodeAttempt rework +
            [26] ArtifactVersion+Lineage + 选择性下游(label-vs-result 独立性).
    """
    node_id: str                 # 产出错误 label 的 Annot 节点
    artifact_id: str             # 被修正的 ArtifactVersion
    original_label: str = ""
    corrected_label: str = ""
    reused_result_artifact_id: str = ""  # 复用的执行结果(不重跑)
    affected_downstream: list[str] = field(default_factory=list)
    reason: str = ""
    timestamp: float = 0.0  # sandbox-safe; 调用方传 workflow.now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "artifact_id": self.artifact_id,
            "original_label": self.original_label,
            "corrected_label": self.corrected_label,
            "reused_result_artifact_id": self.reused_result_artifact_id,
            "affected_downstream": self.affected_downstream,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }



@dataclass
class OverrideRecord:
    """人工强制覆盖记录 (Op-新 ground_truth_override).

    绕过 Evaluator-Optimizer[34], 质检员直接定 verdict. 必须单独留痕做
    责任认定: 机器被推翻, 人工对结果负责, 事后追责需可查.

    核心不变量:
      - machine_verdict 保留(供校准), ground_truth_verdict 实际生效
      - responsibility_owner = 质检员(责任认定), 非 system
      - (machine, ground_truth) 对 = 校准训练信号 -> Memory Store

    Source: [1][2] human_review(REQUIRED 分支); [34] Evaluator-Optimizer
            (绕过但记录); [27] DecisionRecord event sourcing; [30][32] Reflexion 校准.
    """
    node_id: str
    machine_verdict: str = ""         # LLM/启发式原裁决 (保留供校准)
    ground_truth_verdict: str = ""   # 质检员强制值 (实际生效)
    override_by: str = ""             # 质检员 id
    responsibility_owner: str = ""    # 责任主体 (= override_by)
    reason: str = ""
    evidence: str = ""
    calibration_pair_id: str = ""     # 校准对 id (供 Memory Store 关联)
    timestamp: float = 0.0  # sandbox-safe; 调用方传 workflow.now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "machine_verdict": self.machine_verdict,
            "ground_truth_verdict": self.ground_truth_verdict,
            "override_by": self.override_by,
            "responsibility_owner": self.responsibility_owner,
            "reason": self.reason,
            "evidence": self.evidence,
            "calibration_pair_id": self.calibration_pair_id,
            "timestamp": self.timestamp,
        }



@dataclass
class DelegateReviewRecord:
    """审查权转移记录 (Op-新 delegate_review).

    REQUIRED 分支"谁来 human_review"发生变更. 管 review 的"who"维度.
    重派后该节点后续审查用新 reviewer; pending review 重路由给新 reviewer.

    核心不变量:
      - 已 COMPLETED 决策不可追溯改(不可变[16][27]), 只影响后续审查
      - 审查人历史可追溯(谁审了什么/何时), 支持责任认定(同 override)
      - 可链式: A->B->senior (对应 escalate[2])

    Source: [1][2] human_review(REQUIRED 分支); reviewer 分配;
            [27] DecisionRecord 审查人溯源; RBAC 权限.
    """
    target: str              # node_id 或 node_type
    old_reviewer_id: str = ""
    new_reviewer_id: str = ""
    # 被 reroute 的 pending review (处于 _review_waiting 的)
    rerouted_pending_review_ids: list[str] = field(default_factory=list)
    reason: str = ""
    authority_verified: bool = True   # RBAC 校验结果 (系统无完整 RBAC, 默认通过)
    is_escalation: bool = False       # 是否升级(junior->senior)
    timestamp: float = 0.0  # sandbox-safe; 调用方传 workflow.now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "old_reviewer_id": self.old_reviewer_id,
            "new_reviewer_id": self.new_reviewer_id,
            "rerouted_pending_review_ids": self.rerouted_pending_review_ids,
            "reason": self.reason,
            "authority_verified": self.authority_verified,
            "is_escalation": self.is_escalation,
            "timestamp": self.timestamp,
        }



@dataclass
class StandardUpdateRecord:
    """标准库更新记录 (Op-新[M] standard_update).

    AWS D1.1/ISO 5817 版本升级 -> 触发历史判定重审. [M]治理类, 核心影响扫描
    跨workflow(离线). dag_runner内做机制就位: 扫描当前workflow的audit_trail
    里context含standard_version的DecisionRecord, 分类标记受影响节点.

    分类: STABLE(判定不变)/AT_RISK(更严原PASS现FAIL)/OBSOLETE(条款删除).
    旧版本标记superseded不删(provenance). 跨workflow历史扫描留TODO.

    Source: [8.1] L4 Semantic; [29] ExecutionRecord replay;
            [27] DecisionRecord provenance(standard_version).
    """
    standard_id: str
    old_version: str = ""
    new_version: str = ""
    effective_date: str = ""
    diff: str = ""
    # 受影响决策: [(record_id, node_id, classification)]
    affected_decisions: list[dict[str, Any]] = field(default_factory=list)
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "standard_id": self.standard_id,
            "old_version": self.old_version,
            "new_version": self.new_version,
            "effective_date": self.effective_date,
            "diff": self.diff,
            "affected_decisions": self.affected_decisions,
            "timestamp": self.timestamp,
        }


@dataclass
class CaseCorrectionRecord:
    """案例库纠错记录 (Op-新[M] case_library_correction).

    CBR案例本身有误 -> 剔除/修正. 影响面>rework_node: rework影响单次workflow
    下游DAG; 案例纠错影响所有引用过该案例的过去与未来workflow(引用图,跨时间).

    error_type: WRONG_OUTCOME(结果错)/WRONG_SOLUTION(方案缺陷)/MISLEADING_LABEL(标签错).
    dag_runner内: 扫描当前workflow引用该案例的决策 + 标记受影响节点. 跨workflow留TODO.

    Source: [30] CBR; [8.1] 分层记忆; [29] replay; [26] ArtifactVersion Lineage.
    """
    case_id: str
    error_type: str = ""          # WRONG_OUTCOME | WRONG_SOLUTION | MISLEADING_LABEL
    correction: str = ""         # FIX | REMOVE
    affected_workflows: list[str] = field(default_factory=list)
    affected_decisions: list[dict[str, Any]] = field(default_factory=list)
    re_derived_rules: list[str] = field(default_factory=list)
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "error_type": self.error_type,
            "correction": self.correction,
            "affected_workflows": self.affected_workflows,
            "affected_decisions": self.affected_decisions,
            "re_derived_rules": self.re_derived_rules,
            "timestamp": self.timestamp,
        }


__all__ = ["DecisionRecord", "DecisionType", "AuditTrail",
           "RevokeApprovalRecord", "BatchHoldRecord", "PauseScopeRecord",
           "RelabelRecord", "OverrideRecord", "DelegateReviewRecord",
           "StandardUpdateRecord", "CaseCorrectionRecord"]
