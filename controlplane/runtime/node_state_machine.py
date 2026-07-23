"""节点状态机 - 把节点状态从隐式集合成员推断收敛成显式状态+迁移。

设计原则 (对应状态机文档 §五 不变量):
  1. COMMITTED 是单向门 - 只能经 REVOKED 出, 不能 rework 回 PREPARING
  2. 不删历史 - 每次迁移 append 到 DecisionRecord, 回退=开新迁移
  3. inject 不改态 - 注入只更新上下文版本
  4. PAUSED 是挂起非终止 - resume 回挂起前态
  5. HELD 是隔离非失败 - release 可回 COMMITTED
  6. 非法迁移拒绝+报警 - 表格外的 (态,事件) 来了拒绝并记审计, 绝不静默改态
  7. 幂等 - 同事件重复到同态, 结果一致

核心思想: 情况 = 一次迁移, 组合 = 迁移序列.
状态机只看 "当前态 × 事件 -> 新态", 任意顺序组合都被同一张表消化.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class NodeState(str, Enum):
    """节点在生命周期中的互斥状态 (状态机文档 §一)."""
    PENDING = "pending"              # 未启动, 等依赖就绪
    PREPARING = "preparing"          # PREPARE 阶段, 填上下文/幂等检查
    EXECUTING = "executing"          # EXECUTE 阶段, L3 Activity 跑, draft-only
    PAUSED = "paused"                # 被挂起(任意 scope), 停在安全 checkpoint
    AWAITING_REVIEW = "awaiting_review"  # VALIDATE 完, 等人审/等条件
    COMMITTED = "committed"          # 已提交, artifact 转正式, 下游可消费
    FAILED = "failed"                # 终态失败(重试耗尽/abort/continue 标记)
    HELD = "held"                    # 结果扣留待查(batch_hold/quarantine)
    REVOKED = "revoked"              # 已提交后被撤回(误判)


class NodeEvent(str, Enum):
    """触发状态迁移的事件."""
    START = "start"                  # 开始 PREPARE
    READY = "ready"                  # PREPARE 完, 进入 EXECUTE
    EXEC_DONE = "exec_done"          # EXECUTE 完, 进 VALIDATE(AWAITING_REVIEW)
    PAUSE = "pause"                  # 暂停信号(任意 scope)
    RESUME = "resume"                # 恢复
    CANCEL = "cancel"                # 取消(转终态)
    APPROVE = "approve"              # 审查通过
    APPROVE_BUT_HOLD = "approve_but_hold"  # 通过但扣留(batch_hold)
    REWORK = "rework"                # 改参数重做 -> 回 PREPARING
    OVERRIDE = "override"           # 人工强制覆盖(绕过 evaluator, 直接提交)
    REJECT = "reject"               # 审查拒绝 -> FAILED
    FAIL = "fail"                   # 执行失败 -> FAILED
    HOLD = "hold"                    # 扣留(从 COMMITTED 或 REVIEW)
    RELEASE = "release"              # 扣留释放 -> COMMITTED
    REVOKE = "revoke"                # 已提交误判撤回 -> REVOKED
    INJECT = "inject"                # 注入上下文(不改态, 只记版本)
    RE_RUN = "re_run"                # FAILED 重跑 -> PENDING
    QUARANTINE = "quarantine"        # HELD 永久隔离 -> FAILED


# 迁移表: (当前态, 事件) -> 新态. None = 非法迁移.
# 对应状态机文档 §三.
_TRANSITIONS: dict[tuple[NodeState, NodeEvent], NodeState] = {
    # PENDING
    (NodeState.PENDING, NodeEvent.START): NodeState.PREPARING,
    (NodeState.PENDING, NodeEvent.CANCEL): NodeState.FAILED,
    (NodeState.PENDING, NodeEvent.FAIL): NodeState.FAILED,
    (NodeState.PENDING, NodeEvent.INJECT): NodeState.PENDING,  # 不改态
    # PREPARING
    (NodeState.PREPARING, NodeEvent.READY): NodeState.EXECUTING,
    (NodeState.PREPARING, NodeEvent.PAUSE): NodeState.PAUSED,
    (NodeState.PREPARING, NodeEvent.CANCEL): NodeState.FAILED,
    (NodeState.PREPARING, NodeEvent.FAIL): NodeState.FAILED,
    (NodeState.PREPARING, NodeEvent.INJECT): NodeState.PREPARING,
    # EXECUTING
    (NodeState.EXECUTING, NodeEvent.EXEC_DONE): NodeState.AWAITING_REVIEW,
    (NodeState.EXECUTING, NodeEvent.PAUSE): NodeState.PAUSED,
    (NodeState.EXECUTING, NodeEvent.CANCEL): NodeState.FAILED,
    (NodeState.EXECUTING, NodeEvent.REWORK): NodeState.PREPARING,
    (NodeState.EXECUTING, NodeEvent.FAIL): NodeState.FAILED,
    (NodeState.EXECUTING, NodeEvent.INJECT): NodeState.EXECUTING,
    # PAUSED (记挂起前态, resume 回原态)
    (NodeState.PAUSED, NodeEvent.RESUME): NodeState.EXECUTING,  # 默认回 EXECUTING, 实际由 _resume_to 覆盖
    (NodeState.PAUSED, NodeEvent.CANCEL): NodeState.FAILED,
    (NodeState.PAUSED, NodeEvent.REWORK): NodeState.PREPARING,
    (NodeState.PAUSED, NodeEvent.INJECT): NodeState.PAUSED,
    # AWAITING_REVIEW
    (NodeState.AWAITING_REVIEW, NodeEvent.APPROVE): NodeState.COMMITTED,
    (NodeState.AWAITING_REVIEW, NodeEvent.APPROVE_BUT_HOLD): NodeState.HELD,
    (NodeState.AWAITING_REVIEW, NodeEvent.REWORK): NodeState.PREPARING,
    (NodeState.AWAITING_REVIEW, NodeEvent.OVERRIDE): NodeState.COMMITTED,
    (NodeState.AWAITING_REVIEW, NodeEvent.REJECT): NodeState.FAILED,
    (NodeState.AWAITING_REVIEW, NodeEvent.PAUSE): NodeState.PAUSED,
    (NodeState.AWAITING_REVIEW, NodeEvent.CANCEL): NodeState.FAILED,
    (NodeState.AWAITING_REVIEW, NodeEvent.HOLD): NodeState.HELD,
    (NodeState.AWAITING_REVIEW, NodeEvent.INJECT): NodeState.AWAITING_REVIEW,
    # COMMITTED (单向门)
    (NodeState.COMMITTED, NodeEvent.REVOKE): NodeState.REVOKED,
    (NodeState.COMMITTED, NodeEvent.HOLD): NodeState.HELD,
    (NodeState.COMMITTED, NodeEvent.INJECT): NodeState.COMMITTED,  # 已提交不回滚
    # HELD (隔离非失败)
    (NodeState.HELD, NodeEvent.RELEASE): NodeState.COMMITTED,
    (NodeState.HELD, NodeEvent.REWORK): NodeState.PREPARING,
    (NodeState.HELD, NodeEvent.QUARANTINE): NodeState.FAILED,
    (NodeState.HELD, NodeEvent.CANCEL): NodeState.FAILED,
    # FAILED
    (NodeState.FAILED, NodeEvent.RE_RUN): NodeState.PENDING,
    (NodeState.FAILED, NodeEvent.REVOKE): NodeState.REVOKED,
    # REVOKED (终态, 可触发更正版 -> COMMITTED)
    (NodeState.REVOKED, NodeEvent.APPROVE): NodeState.COMMITTED,
}


@dataclass
class NodeStateMachine:
    """单个节点的状态机实例.

    持有节点当前态 + 挂起前态(PAUSED resume 用) + 迁移历史(审计).
    所有迁移经 transition(), 非法迁移拒绝并记日志.
    """
    node_id: str
    state: NodeState = NodeState.PENDING
    _paused_from: NodeState | None = None  # PAUSED 前的态, resume 回这
    history: list[dict[str, Any]] = field(default_factory=list)

    def can_transition(self, event: NodeEvent) -> bool:
        """查询某事件在当前态是否合法."""
        return (self.state, event) in _TRANSITIONS

    def transition(self, event: NodeEvent, **context: Any) -> NodeState:
        """执行迁移. 非法迁移拒绝并记审计, 不改态.

        Returns: 迁移后的新态 (非法时返回原态).
        Raises: 不抛异常 (非法迁移是 bug 信号, 但不该崩 workflow).
        """
        key = (self.state, event)
        # INJECT 永远不改态 (不变量3)
        if event == NodeEvent.INJECT:
            self._record(event, self.state, self.state, context)
            return self.state

        new_state = _TRANSITIONS.get(key)
        if new_state is None:
            # 非法迁移: 拒绝 + 报警 + 记审计 (不变量6)
            logger.error(
                "ILLEGAL TRANSITION: node=%s state=%s event=%s - rejected (not in transition table)",
                self.node_id, self.state.value, event.value,
            )
            self._record(event, self.state, self.state, context, legal=False)
            return self.state

        # PAUSED 记挂起前态, resume 回它 (不变量4)
        if event == NodeEvent.PAUSE and self.state != NodeState.PAUSED:
            self._paused_from = self.state
        if event == NodeEvent.RESUME and self._paused_from is not None:
            new_state = self._paused_from
            self._paused_from = None

        old = self.state
        self.state = new_state
        self._record(event, old, new_state, context)
        return new_state

    def _record(
        self, event: NodeEvent, old: NodeState, new: NodeState,
        context: dict[str, Any], legal: bool = True,
    ) -> None:
        self.history.append({
            "event": event.value,
            "from": old.value,
            "to": new.value,
            "legal": legal,
            "context": context,
        })

    @property
    def is_terminal(self) -> bool:
        """是否终态(COMMITTED 除外, COMMITTED 可 revoke)."""
        return self.state in (NodeState.FAILED, NodeState.REVOKED)

    @property
    def is_committed(self) -> bool:
        """是否已提交(过了单向门)."""
        return self.state == NodeState.COMMITTED

    @property
    def can_consume_downstream(self) -> bool:
        """下游是否可消费此节点结果."""
        return self.state == NodeState.COMMITTED


# ── 便捷查询 (替代 dag_runner 里"查多个列表"的判断) ────────────────────

def is_done(state: NodeState) -> bool:
    """节点是否已处理(成功或失败, 不再 pending)."""
    return state in (NodeState.COMMITTED, NodeState.FAILED, NodeState.REVOKED)

def is_success(state: NodeState) -> bool:
    """节点是否成功完成(下游可消费)."""
    return state == NodeState.COMMITTED

def is_active(state: NodeState) -> bool:
    """节点是否在执行中(非终态非pending)."""
    return state in (NodeState.PREPARING, NodeState.EXECUTING, NodeState.AWAITING_REVIEW, NodeState.PAUSED, NodeState.HELD)
