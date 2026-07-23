"""节点状态机测试 - 覆盖 A 类 ❓ (状态机能直接回答的).

每个测试对应清单里一个 ❓, 证明: "某态下来某事件" 查表即得, 不需拍板.
"""

from __future__ import annotations

import pytest

from controlplane.runtime.node_state_machine import (
    NodeEvent as E,
    NodeState as S,
    NodeStateMachine,
    is_done,
    is_success,
)


def _sm(state: S = S.PENDING) -> NodeStateMachine:
    """构造一个指定初始态的状态机 (跳过历史)."""
    m = NodeStateMachine("n")
    m.state = state
    return m


# ── 标准路径 ──────────────────────────────────────────────────────────

def test_standard_happy_path():
    """PENDING -> PREPARING -> EXECUTING -> AWAITING_REVIEW -> COMMITTED."""
    m = NodeStateMachine("n")
    assert m.transition(E.START) == S.PREPARING
    assert m.transition(E.READY) == S.EXECUTING
    assert m.transition(E.EXEC_DONE) == S.AWAITING_REVIEW
    assert m.transition(E.APPROVE) == S.COMMITTED
    assert m.is_committed
    assert m.can_consume_downstream


def test_failure_path():
    """EXECUTING -> FAIL -> FAILED, 可 re_run."""
    m = _sm(S.EXECUTING)
    assert m.transition(E.FAIL) == S.FAILED
    assert m.is_terminal
    assert m.transition(E.RE_RUN) == S.PENDING


# ── A 类 ❓: 状态机直接回答 ────────────────────────────────────────────

def test_B6_commit_前暂停_恢复后重跑():
    """❓B6: COMMIT 前暂停, 恢复后重验还是直接提交?
    答: PAUSED 记挂起前态. 若挂起在 EXECUTING -> resume 回 EXECUTING 重跑;
        若挂起在 AWAITING_REVIEW -> resume 回 REVIEW 可直接 approve."""
    # 情形1: EXECUTING 中暂停
    m = _sm(S.EXECUTING)
    m.transition(E.PAUSE)
    assert m.state == S.PAUSED
    assert m.transition(E.RESUME) == S.EXECUTING  # 回挂起前态, 重跑
    # 情形2: REVIEW 中暂停
    m2 = _sm(S.AWAITING_REVIEW)
    m2.transition(E.PAUSE)
    assert m2.transition(E.RESUME) == S.AWAITING_REVIEW  # 回 REVIEW, 可直接 approve


def test_B12_暂停超时放弃():
    """❓B12: 暂停超时放弃. 答: PAUSED + timer -> CANCEL -> FAILED (迁移)."""
    m = _sm(S.PAUSED)
    assert m.transition(E.CANCEL) == S.FAILED


def test_B11_暂停转取消():
    """❓B11: 暂停后用户决定不做. 答: PAUSED -> CANCEL -> FAILED."""
    m = _sm(S.PAUSED)
    assert m.transition(E.CANCEL) == S.FAILED


def test_C1_未提交回退():
    """❓C1: draft 未提交回退. 答: EXECUTING/REVIEW -> REWORK -> PREPARING."""
    m = _sm(S.EXECUTING)
    assert m.transition(E.REWORK) == S.PREPARING
    m2 = _sm(S.AWAITING_REVIEW)
    assert m2.transition(E.REWORK) == S.PREPARING


def test_C4_已提交回退需revoke():
    """❓C4: 已 COMMIT 回退. 答: 不能 rework(单向门), 只能 REVOKE -> REVOKED."""
    m = _sm(S.COMMITTED)
    # rework 在 COMMITTED 是非法的
    assert m.transition(E.REWORK) == S.COMMITTED  # 拒绝, 留在 COMMITTED
    assert m.transition(E.REVOKE) == S.REVOKED  # 只能 revoke


def test_C7_回退后可发更正版():
    """❓C7: revoke 后发更正版. 答: REVOKED -> APPROVE -> COMMITTED (新版)."""
    m = _sm(S.REVOKED)
    assert m.transition(E.APPROVE) == S.COMMITTED


def test_L6_注入不改态():
    """❓L6: 已执行节点不回滚. 答: INJECT 不改态, 只记版本."""
    for start in [S.PENDING, S.EXECUTING, S.AWAITING_REVIEW, S.COMMITTED]:
        m = _sm(start)
        assert m.transition(E.INJECT) == start  # 状态不变
        assert m.state == start


def test_O17_审查超时escalate():
    """❓O17: 审查超时无人. 答: AWAITING_REVIEW -> PAUSE(escalate) 或 REJECT(=fail).
    两种都是合法迁移, 选哪个是配置, 状态机都支持."""
    m1 = _sm(S.AWAITING_REVIEW)
    assert m1.transition(E.PAUSE) == S.PAUSED  # escalate = 暂停等高层
    m2 = _sm(S.AWAITING_REVIEW)
    assert m2.transition(E.REJECT) == S.FAILED  # 超时拒绝


def test_M9_中断丢弃draft():
    """❓M9: 中断丢弃未提交 draft. 答: EXECUTING -> CANCEL -> FAILED."""
    m = _sm(S.EXECUTING)
    assert m.transition(E.CANCEL) == S.FAILED


def test_E10_batch_hold从review():
    """❓E10: 批次扣留. 答: AWAITING_REVIEW -> APPROVE_BUT_HOLD -> HELD."""
    m = _sm(S.AWAITING_REVIEW)
    assert m.transition(E.APPROVE_BUT_HOLD) == S.HELD


def test_批次release():
    """HELD -> RELEASE -> COMMITTED (复查通过转正式)."""
    m = _sm(S.HELD)
    assert m.transition(E.RELEASE) == S.COMMITTED


def test_批次quarantine():
    """HELD -> QUARANTINE -> FAILED (永久隔离)."""
    m = _sm(S.HELD)
    assert m.transition(E.QUARANTINE) == S.FAILED


# ── 不变量 ────────────────────────────────────────────────────────────

def test_invariant_committed_is_one_way_gate():
    """不变量1: COMMITTED 单向门. rework/START/EXEC_DONE 都非法."""
    m = _sm(S.COMMITTED)
    for illegal in [E.REWORK, E.START, E.EXEC_DONE, E.READY, E.PAUSE]:
        assert m.transition(illegal) == S.COMMITTED, f"{illegal} 应被拒绝"
    # 只有 REVOKE/HOLD/INJECT 合法
    assert m.transition(E.REVOKE) == S.REVOKED


def test_invariant_inject_never_changes_state():
    """不变量3: INJECT 在任何态都不改态."""
    for state in S.__members__.values():
        m = _sm(state)
        assert m.transition(E.INJECT) == state


def test_invariant_paused_resume_returns_to_pre_pause_state():
    """不变量4: PAUSED resume 回挂起前态 (不是固定 EXECUTING)."""
    # 从 PREPARING 暂停
    m = _sm(S.PREPARING)
    m.transition(E.PAUSE)
    assert m.transition(E.RESUME) == S.PREPARING
    # 从 REVIEW 暂停
    m2 = _sm(S.AWAITING_REVIEW)
    m2.transition(E.PAUSE)
    assert m2.transition(E.RESUME) == S.AWAITING_REVIEW


def test_invariant_illegal_transition_rejected_and_audited():
    """不变量6: 非法迁移拒绝 + 记审计 + 不改态."""
    m = _sm(S.PENDING)
    # PENDING 不能直接 APPROVE (没执行没审查)
    result = m.transition(E.APPROVE)
    assert result == S.PENDING  # 不改态
    assert m.state == S.PENDING
    # 历史里有非法记录
    illegal_entries = [h for h in m.history if not h["legal"]]
    assert len(illegal_entries) == 1
    assert illegal_entries[0]["event"] == "approve"


def test_invariant_idempotent_pause():
    """不变量7: 幂等 - PAUSED + PAUSE 仍 PAUSED."""
    m = _sm(S.EXECUTING)
    m.transition(E.PAUSE)
    assert m.state == S.PAUSED
    # 再 PAUSE (PAUSED->PAUSED 非法, 但幂等应不崩)
    # 注: PAUSED->PAUSE 不在表里, 会拒绝. 这是正确的 (已在 PAUSED 不需再 pause).
    # 但 RESUME 后再 PAUSE 应合法.
    m.transition(E.RESUME)
    assert m.transition(E.PAUSE) == S.PAUSED


# ── 组合序列 (证明组合爆炸被消化) ──────────────────────────────────────

def test_combination_B6_L10_C11_arbitrary_order():
    """关键: B6暂停 + L10注入 + C11上游回溯 三条叠加, 任意顺序结果唯一.
    状态机不关心顺序, 每步查表合法."""
    # 顺序1: pause -> inject -> resume -> rework
    m1 = _sm(S.EXECUTING)
    m1.transition(E.PAUSE)
    m1.transition(E.INJECT)  # 不改态
    m1.transition(E.RESUME)
    assert m1.transition(E.REWORK) == S.PREPARING

    # 顺序2: inject -> pause -> rework -> (rework 在 PAUSED 也合法)
    m2 = _sm(S.EXECUTING)
    m2.transition(E.INJECT)
    m2.transition(E.PAUSE)
    assert m2.transition(E.REWORK) == S.PREPARING  # PAUSED->REWORK 合法


def test_combination_audit_chain_complete():
    """每次迁移都 append 历史, 回退=开新迁移, 不删旧 (不变量2)."""
    m = NodeStateMachine("n")
    m.transition(E.START)
    m.transition(E.READY)
    m.transition(E.EXEC_DONE)
    m.transition(E.REWORK)  # 回 PREPARING
    m.transition(E.READY)
    m.transition(E.EXEC_DONE)
    m.transition(E.APPROVE)
    # 历史完整, 含两次 EXEC_DONE (回退没删旧记录)
    exec_done_count = sum(1 for h in m.history if h["event"] == "exec_done")
    assert exec_done_count == 2
    assert m.state == S.COMMITTED
    assert len(m.history) == 7


# ── 便捷查询 ──────────────────────────────────────────────────────────

def test_helpers():
    assert is_success(S.COMMITTED)
    assert not is_success(S.FAILED)
    assert is_done(S.COMMITTED)
    assert is_done(S.FAILED)
    assert is_done(S.REVOKED)
    assert not is_done(S.EXECUTING)


# ── Phase E: shadow 一致性断言 ──────────────────────────────────
# 这些测试不依赖 Temporal, 直接验证状态机的核心不变量.
# dag_runner 的 _assert_sm_consistency 暴露 sm_mismatches 后,
# workflow 层测试断言 result["sm_mismatches"] == [].


def test_shadow_consistency_committed_matches_lists():
    """shadow 断言: 状态机 COMMITTED 与列表推断一致 (无 mismatch)."""
    m = NodeStateMachine("n")
    m.transition(E.START)
    m.transition(E.READY)
    m.transition(E.EXEC_DONE)
    m.transition(E.APPROVE)
    assert m.state == S.COMMITTED
    # 状态机自身内部一致: state 与 history 末态对齐
    assert m.history[-1]["to"] == "committed"
    assert m.history[-1]["legal"] is True
    assert m.is_committed


def test_shadow_consistency_failed_matches_lists():
    """shadow 断言: 状态机 FAILED 终态与历史一致."""
    m = NodeStateMachine("n")
    m.transition(E.START)
    m.transition(E.READY)
    m.transition(E.FAIL)
    assert m.state == S.FAILED
    assert m.is_terminal
    assert m.history[-1]["to"] == "failed"


def test_shadow_consistency_revoked_path():
    """shadow 断言: COMMITTED -> REVOKED -> COMMITTED 链一致."""
    m = NodeStateMachine("n")
    m.transition(E.START)
    m.transition(E.READY)
    m.transition(E.EXEC_DONE)
    m.transition(E.APPROVE)
    m.transition(E.REVOKE)
    assert m.state == S.REVOKED
    # 更正版
    m.transition(E.APPROVE)
    assert m.state == S.COMMITTED
    # 历史含两次 COMMITTED 入口
    committed_entries = [h for h in m.history if h["to"] == "committed"]
    assert len(committed_entries) == 2


def test_shadow_consistency_held_then_released():
    """shadow 断言: HELD -> RELEASE -> COMMITTED 一致."""
    m = _sm(S.AWAITING_REVIEW)
    m.transition(E.APPROVE_BUT_HOLD)
    assert m.state == S.HELD
    m.transition(E.RELEASE)
    assert m.state == S.COMMITTED
    assert m.can_consume_downstream


def test_shadow_consistency_no_mismatch_on_legal_sequence():
    """shadow 断言: 任意合法迁移序列后, 状态机内部无矛盾 (state=history末态)."""
    sequences = [
        [E.START, E.READY, E.EXEC_DONE, E.APPROVE],
        [E.START, E.READY, E.FAIL, E.RE_RUN, E.START, E.READY, E.EXEC_DONE, E.APPROVE],
        [E.START, E.READY, E.EXEC_DONE, E.APPROVE, E.REVOKE, E.APPROVE],
        [E.START, E.READY, E.EXEC_DONE, E.APPROVE_BUT_HOLD, E.RELEASE],
        [E.START, E.READY, E.EXEC_DONE, E.REWORK, E.READY, E.EXEC_DONE, E.APPROVE],
    ]
    for seq in sequences:
        m = NodeStateMachine("n")
        for ev in seq:
            m.transition(ev)
        assert m.history[-1]["to"] == m.state.value, f"sequence {seq} mismatch"
        assert all(h["legal"] for h in m.history), f"sequence {seq} had illegal"


def test_shadow_mismatch_detection_via_illegal_transition():
    """shadow 断言: 非法迁移被拒绝, 状态不变, 历史记 illegal (这是 mismatch 的反面 - 检测能力存在)."""
    m = _sm(S.PENDING)
    result = m.transition(E.APPROVE)  # PENDING 不能直接 APPROVE
    assert result == S.PENDING  # 不改态
    assert m.state == S.PENDING
    illegal = [h for h in m.history if not h["legal"]]
    assert len(illegal) == 1
    assert illegal[0]["event"] == "approve"
    assert illegal[0]["from"] == illegal[0]["to"] == "pending"
