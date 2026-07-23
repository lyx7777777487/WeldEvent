"""治理状态事件化 (Governance Event Sourcing) - 阶段1 双写基建.

Source: docs/superpowers/specs/2026-07-21-governance-event-sourcing-design.md

解决清单 C8-C13 (回溯与暂停/并行/扇出/成本/输入漂移叠加) 的根因:
当前 12 个可变状态字段 (_paused_nodes / _rework_nodes / _held_batches ...)
被治理模块直接 mutate, 字段之间互不知道对方发生了什么 (C12: pause+rework 同一节点,
两个 set 互不知道). 事件化反转因果: 治理动作 -> 追加事件, 状态从事件 reduce.

阶段1 (本文件): 只加不改. GovernanceEvent/GovernanceEventLog/GovernanceProjection
三个类建好, 治理模块双写 (改字段 + 追加事件), 投影只做 shadow 校验 (不参与决策).
零风险: 投影错了不影响执行, 现有 12 字段仍是执行依据.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GovernanceEvent:
    """治理事件 - 不可变, 追加到事件流, 是治理状态的事实来源.

    与 DecisionRecord (审计旁路, 给合规看) 的区别:
    DecisionRecord 记 "做了什么决策"; GovernanceEvent 记 "对状态产生了什么影响".
    本阶段两者并存: 治理模块改字段后, 同时追加 GovernanceEvent (双写).
    后续阶段反转: 事件成为执行依据, 字段降为投影缓存.

    supersedes: 本事件作废了哪个前置事件.
        - resume_scope supersedes 对应的 pause_scope
        - release_hold / rework_batch / quarantine_batch supersedes 对应 batch_hold
        - rework_node 重跑完成 supersedes 上一次 rework_node
      这是解决叠加的核心: 不删旧事件, 而是追加新事件声明作废旧事件.
      事件流只增不改 (审计完整), 投影时按 supersedes 链过滤.
    """

    event_id: str               # 单调递增 (evt_001, evt_002...)
    event_type: str             # PAUSE_SCOPE / RESUME_SCOPE / BATCH_HOLD /
                                # RELEASE_HOLD / REWORK_BATCH / QUARANTINE_BATCH /
                                # REWORK_NODE / REVOKE_APPROVAL / RELABEL_REQUEST /
                                # GROUND_TRUTH_OVERRIDE / DELEGATE_REVIEW /
                                # STANDARD_UPDATE / CASE_LIBRARY_CORRECTION /
                                # INJECT_CONTEXT / HUMAN_REVIEW /
                                # REWORK_NODE_COMPLETED / SPEC_REVISION
    scope: str                  # "node" | "batch" | "workflow"
    actor: str                  # 责任人 (发起者)
    reason: str
    timestamp: float
    node_id: str | None = None  # 节点级事件的目标
    batch_id: str | None = None  # 批次级事件的目标
    payload: dict[str, Any] = field(default_factory=dict)  # 事件专属数据
    supersedes: str | None = None  # 作废的前置 event_id (None=新增)


class GovernanceEventLog:
    """治理事件流 - 事实来源 (阶段1 仅双写, 不参与决策).

    阶段1: 治理模块改字段后追加事件, project() 跑 shadow 校验.
    阶段3: 反转, 事件成为执行依据, 12 字段降为 project() 的缓存.
    """

    def __init__(self) -> None:
        self._events: list[GovernanceEvent] = []
        self._counter: int = 0

    def append(self, event_type: str, scope: str, actor: str, reason: str,
               timestamp: float, node_id: str | None = None,
               batch_id: str | None = None,
               payload: dict[str, Any] | None = None,
               supersedes: str | None = None) -> GovernanceEvent:
        """追加治理事件, 返回事件对象 (供后续 supersedes 引用)."""
        self._counter += 1
        ev = GovernanceEvent(
            event_id=f"evt_{self._counter:04d}",
            event_type=event_type,
            scope=scope,
            actor=actor,
            reason=reason,
            timestamp=timestamp,
            node_id=node_id,
            batch_id=batch_id,
            payload=payload or {},
            supersedes=supersedes,
        )
        self._events.append(ev)
        return ev

    @property
    def events(self) -> list[GovernanceEvent]:
        return list(self._events)

    def project(self, up_to: int | None = None) -> GovernanceProjection:
        """reduce 事件流得到治理状态投影 (或历史某点).

        up_to: 投影到第 N 个事件 (含). None=当前全部. 用于回溯/A-B对比.
        """
        events = self._events if up_to is None else self._events[:up_to]
        return GovernanceProjection.reduce(events)


@dataclass
class GovernanceProjection:
    """治理状态投影 - 从事件流 reduce 出的只读快照.

    设计: 阶段1 与现有 12 字段双写, project() 结果做 shadow 校验.
    阶段3 起字段变成本投影的派生缓存.

    node_interventions 是解决 C8-C13 叠加的关键:
    每个节点当前受哪些 active 事件影响, 一目了然 (不再两 set 互不知道).
    """

    paused_nodes: set[str] = field(default_factory=set)
    paused_batches: set[str] = field(default_factory=set)
    held_batches: dict[str, dict[str, Any]] = field(default_factory=dict)
    rework_nodes: set[str] = field(default_factory=set)
    review_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    injected_context: dict[str, Any] = field(default_factory=dict)
    # 派生: 每个节点当前受哪些 active 事件影响 (解决叠加)
    node_interventions: dict[str, list[str]] = field(default_factory=dict)
    # 工作流 spec 版本链: [(spec_hash, event_id, timestamp)]
    spec_history: list[tuple[str, str, float]] = field(default_factory=list)
    # 当前生效的 spec hash (spec_history 最后一个, 或 None)
    active_spec_hash: str | None = None

    @classmethod
    def reduce(cls, events: list[GovernanceEvent]) -> GovernanceProjection:
        """reduce 事件流 -> 投影. 按 supersedes 链过滤已作废事件."""
        # 1. 收集所有被 supersede 的 event_id (作废集)
        superseded: set[str] = set()
        for ev in events:
            if ev.supersedes is not None:
                superseded.add(ev.supersedes)

        # 2. 只 reduce active 事件 (未被作废的)
        active = [ev for ev in events if ev.event_id not in superseded]

        proj = cls()
        for ev in active:
            proj._apply(ev)
        return proj

    def _apply(self, ev: GovernanceEvent) -> None:
        """把单个 active 事件 reduce 进投影."""
        if ev.event_type == "PAUSE_SCOPE":
            if ev.scope == "station" and ev.node_id:
                self.paused_nodes.add(ev.node_id)
                self._add_intervention(ev.node_id, ev.event_id)
            elif ev.scope == "batch" and ev.batch_id:
                self.paused_batches.add(ev.batch_id)
        elif ev.event_type == "RESUME_SCOPE":
            # resume supersedes pause, 已在 reduce 时过滤掉对应 pause
            # 这里 handle: 若 resume 无 supersedes (强制恢复), 主动移除
            if ev.scope == "station" and ev.node_id:
                self.paused_nodes.discard(ev.node_id)
            elif ev.scope == "batch" and ev.batch_id:
                self.paused_batches.discard(ev.batch_id)
        elif ev.event_type == "BATCH_HOLD":
            if ev.batch_id:
                # deep-copy payload: release/rework/quarantine 会写 info["resolution"],
                # 不能修改不可变事件的 payload
                self.held_batches[ev.batch_id] = dict(ev.payload)
        elif ev.event_type in ("RELEASE_HOLD", "REWORK_BATCH", "QUARANTINE_BATCH"):
            # 这三个都终结 batch_hold (supersedes), active 里 hold 已被过滤
            # 但若无 supersedes (强制操作), 主动移除
            if ev.batch_id:
                self.held_batches.pop(ev.batch_id, None)
        elif ev.event_type == "REWORK_NODE":
            if ev.node_id:
                self.rework_nodes.add(ev.node_id)
                self._add_intervention(ev.node_id, ev.event_id)
        elif ev.event_type == "REWORK_NODE_COMPLETED":
            # rework 完成: 节点已重跑, 从 rework_nodes 移除
            # (supersedes 链已过滤对应 REWORK_NODE, 这里处理无 supersedes 的情况)
            if ev.node_id:
                self.rework_nodes.discard(ev.node_id)
        elif ev.event_type == "INJECT_CONTEXT":
            # payload 带 key/value
            key = ev.payload.get("key")
            value = ev.payload.get("value")
            if key is not None:
                self.injected_context[key] = value
        elif ev.event_type == "HUMAN_REVIEW":
            if ev.node_id:
                self.review_results[ev.node_id] = dict(ev.payload)
                self._add_intervention(ev.node_id, ev.event_id)
        elif ev.event_type == "SPEC_REVISION":
            # spec 版本更迭: 记录新 spec hash 到版本链
            spec_hash = ev.payload.get("spec_hash", "")
            if spec_hash:
                self.spec_history.append((spec_hash, ev.event_id, ev.timestamp))
                self.active_spec_hash = spec_hash
        # override/relabel/delegate/standard/case_correction: 不直接改 12 字段
        # (它们走 AuditTrail + 专属字段), 但记入 interventions 供叠加查询
        elif ev.node_id:
            self._add_intervention(ev.node_id, ev.event_id)

    def _add_intervention(self, node_id: str, event_id: str) -> None:
        """记录节点当前受哪些 active 事件影响 (叠加可见性)."""
        if node_id not in self.node_interventions:
            self.node_interventions[node_id] = []
        self.node_interventions[node_id].append(event_id)


__all__ = ["GovernanceEvent", "GovernanceEventLog", "GovernanceProjection"]
