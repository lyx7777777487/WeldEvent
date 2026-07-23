"""治理状态事件化 (阶段1 双写) - 投影 + shadow 校验测试.

Source: docs/superpowers/specs/2026-07-21-governance-event-sourcing-design.md
验证: GovernanceEventLog.project() 与治理模块改的字段一致 (双写 shadow).
阶段1 投影只校验不决策, 这里测投影逻辑正确 + supersedes 链 + C12 叠加.
"""

import pytest

from cognitiveplane.audit.governance_event import (
    GovernanceEvent,
    GovernanceEventLog,
    GovernanceProjection,
)


class TestGovernanceEventLog:
    def test_append_returns_event_with_incrementing_id(self):
        log = GovernanceEventLog()
        ev1 = log.append("PAUSE_SCOPE", "station", "u", "r", 1.0, node_id="A")
        ev2 = log.append("RESUME_SCOPE", "station", "u", "r", 2.0, node_id="A")
        assert ev1.event_id == "evt_0001"
        assert ev2.event_id == "evt_0002"

    def test_project_empty_log(self):
        proj = GovernanceEventLog().project()
        assert proj.paused_nodes == set()
        assert proj.held_batches == {}


class TestSupersedesChain:
    """supersedes 链: resume 作废 pause, 投影应过滤掉 pause."""

    def test_resume_supersedes_pause(self):
        log = GovernanceEventLog()
        p = log.append("PAUSE_SCOPE", "station", "u", "暂停", 1.0, node_id="A")
        log.append("RESUME_SCOPE", "station", "u", "恢复", 2.0,
                   node_id="A", supersedes=p.event_id)
        proj = log.project()
        assert proj.paused_nodes == set(), "resume 后 pause 应被过滤"

    def test_pause_without_resume_stays_active(self):
        log = GovernanceEventLog()
        log.append("PAUSE_SCOPE", "station", "u", "暂停", 1.0, node_id="A")
        proj = log.project()
        assert proj.paused_nodes == {"A"}

    def test_release_supersedes_hold(self):
        log = GovernanceEventLog()
        h = log.append("BATCH_HOLD", "batch", "u", "扣留", 1.0, batch_id="B1",
                       payload={"reason": "可疑"})
        log.append("RELEASE_HOLD", "batch", "u", "释放", 2.0,
                   batch_id="B1", supersedes=h.event_id)
        proj = log.project()
        assert proj.held_batches == {}


class TestTimeTravel:
    """回溯: project(up_to=N) 得到历史某点的状态."""

    def test_project_up_to_historical_point(self):
        log = GovernanceEventLog()
        log.append("PAUSE_SCOPE", "station", "u", "暂停", 1.0, node_id="A")
        log.append("RESUME_SCOPE", "station", "u", "恢复", 2.0, node_id="A")
        # 回溯到 evt_0001 (只有 pause, 没 resume)
        proj_hist = log.project(up_to=1)
        assert proj_hist.paused_nodes == {"A"}, "回溯应看到 pause 仍 active"


class TestC12Stacking:
    """C12 回溯与暂停叠加: pause A + rework A -> A 有两个 active 事件."""

    def test_pause_and_rework_both_active(self):
        log = GovernanceEventLog()
        log.append("PAUSE_SCOPE", "station", "u", "暂停", 1.0, node_id="A")
        log.append("REWORK_NODE", "node", "u", "重做", 2.0, node_id="A")
        proj = log.project()
        # A 既被暂停又待重做 - 两个状态都 active
        assert proj.paused_nodes == {"A"}
        assert proj.rework_nodes == {"A"}
        # node_interventions 显示 A 受两个事件影响 (叠加可见)
        assert len(proj.node_interventions["A"]) == 2

    def test_revoke_after_rework_both_recorded(self):
        """rework A 后 revoke A: 两个事件都在 (不同类型, 都 active)."""
        log = GovernanceEventLog()
        log.append("REWORK_NODE", "node", "u", "重做", 1.0, node_id="A")
        log.append("REVOKE_APPROVAL", "node", "u", "撤回", 2.0, node_id="A")
        proj = log.project()
        assert "A" in proj.rework_nodes
        assert len(proj.node_interventions["A"]) == 2


class TestInjectContext:
    def test_inject_context_projected(self):
        log = GovernanceEventLog()
        log.append("INJECT_CONTEXT", "workflow", "u", "注入", 1.0,
                   payload={"key": "param", "value": "X=10"})
        log.append("INJECT_CONTEXT", "workflow", "u", "注入", 2.0,
                   payload={"key": "std", "value": "v2"})
        proj = log.project()
        assert proj.injected_context["param"] == "X=10"
        assert proj.injected_context["std"] == "v2"


class TestHumanReview:
    def test_human_review_projected(self):
        log = GovernanceEventLog()
        log.append("HUMAN_REVIEW", "node", "u", "审核", 1.0,
                   node_id="review_node", payload={"decision": "approve"})
        proj = log.project()
        assert proj.review_results["review_node"]["decision"] == "approve"
        assert len(proj.node_interventions["review_node"]) == 1


class TestPhase3WritePathSwitch:
    """阶段3: 写路径切换后, 字段从投影刷新, supersedes 真正驱动状态."""

    def test_pause_then_resume_via_supersedes_only(self):
        """pause+resume 后, 字段应通过投影刷新 (不再直接 add/discard)."""
        from unittest.mock import MagicMock
        log = GovernanceEventLog()
        # 模拟 workflow: append 事件后 refresh
        p = log.append("PAUSE_SCOPE", "station", "u", "暂停", 1.0, node_id="A")
        proj1 = log.project()
        assert proj1.paused_nodes == {"A"}, "pause 后投影应有 A"

        r = log.append("RESUME_SCOPE", "station", "u", "恢复", 2.0,
                       node_id="A", supersedes=p.event_id)
        proj2 = log.project()
        assert proj2.paused_nodes == set(), "resume (supersedes) 后投影应空"

    def test_time_travel_shows_paused_at_historical_point(self):
        """回溯: pause+resume 后, project(up_to=1) 仍显示 paused."""
        log = GovernanceEventLog()
        log.append("PAUSE_SCOPE", "station", "u", "暂停", 1.0, node_id="A")
        log.append("RESUME_SCOPE", "station", "u", "恢复", 2.0,
                   node_id="A", supersedes="evt_0001")
        # 回溯到只有 pause 的时刻
        hist = log.project(up_to=1)
        assert hist.paused_nodes == {"A"}, "回溯应看到 A 仍暂停"
        # 当前 (pause 被 resume supersede)
        curr = log.project()
        assert curr.paused_nodes == set(), "当前 A 应已恢复"

    def test_multiple_pauses_supersede_chain(self):
        """多次 pause+resume: 每次 resume 只 supersede 对应的 pause."""
        log = GovernanceEventLog()
        p1 = log.append("PAUSE_SCOPE", "station", "u", "p1", 1.0, node_id="A")
        r1 = log.append("RESUME_SCOPE", "station", "u", "r1", 2.0,
                        node_id="A", supersedes=p1.event_id)
        p2 = log.append("PAUSE_SCOPE", "station", "u", "p2", 3.0, node_id="A")
        r2 = log.append("RESUME_SCOPE", "station", "u", "r2", 4.0,
                        node_id="A", supersedes=p2.event_id)
        # 当前: 两个 pause 都被 supersede
        curr = log.project()
        assert curr.paused_nodes == set()
        # 回溯到 p2 (只有 p1 被 supersede, p2 还 active)
        hist = log.project(up_to=3)
        assert hist.paused_nodes == {"A"}, "p2 时刻 A 应暂停"


class TestSpecRevision:
    """SPEC_REVISION: 工作流 spec 版本更迭事件."""

    def test_initial_spec_revision_recorded(self):
        """初始 spec 版本应记入 spec_history."""
        log = GovernanceEventLog()
        log.append("SPEC_REVISION", "workflow", "system", "initial spec",
                   1.0, payload={"spec_hash": "abc123", "reason": "initial"})
        proj = log.project()
        assert proj.active_spec_hash == "abc123"
        assert len(proj.spec_history) == 1
        assert proj.spec_history[0][0] == "abc123"

    def test_multiple_spec_revisions_chain(self):
        """多次 spec 版本更迭: active_spec_hash 是最后一个."""
        log = GovernanceEventLog()
        log.append("SPEC_REVISION", "workflow", "system", "v1", 1.0,
                   payload={"spec_hash": "hash_v1"})
        log.append("SPEC_REVISION", "workflow", "user", "v2 modify_spec", 2.0,
                   payload={"spec_hash": "hash_v2"})
        proj = log.project()
        assert proj.active_spec_hash == "hash_v2"
        assert len(proj.spec_history) == 2
        # 回溯到 v1
        hist = log.project(up_to=1)
        assert hist.active_spec_hash == "hash_v1"

    def test_spec_revision_time_travel(self):
        """回溯: project(up_to=N) 还原那个时刻的 spec 版本."""
        log = GovernanceEventLog()
        log.append("SPEC_REVISION", "workflow", "system", "initial", 1.0,
                   payload={"spec_hash": "v1"})
        log.append("PAUSE_SCOPE", "station", "u", "pause", 2.0, node_id="A")
        log.append("SPEC_REVISION", "workflow", "user", "modify", 3.0,
                   payload={"spec_hash": "v2"})
        # 回溯到 modify 之前: spec 是 v1, pause 仍 active
        hist = log.project(up_to=2)
        assert hist.active_spec_hash == "v1"
        assert hist.paused_nodes == {"A"}
        # 当前: spec 是 v2, pause 仍 active
        curr = log.project()
        assert curr.active_spec_hash == "v2"
        assert curr.paused_nodes == {"A"}
