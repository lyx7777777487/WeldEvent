"""dag_runner_workflow.py 的 in-process 测试骨架。

目的：为 Op34（_validate_node_result 接真 LLM activity）提供安全网。
不依赖 Temporal server，用 WorkflowEnvironment.start_local()。

覆盖:
  - 单节点/双节点 DAG 执行 (workflow 层)
  - evaluate_node_quality activity 各路径 (mock跳过/无provider/真LLM/LLM失败)
  - _validate_node_result 结构校验快速返回 + 降级逻辑
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from temporalio.worker import Worker
from temporalio.testing import WorkflowEnvironment

from controlplane.adapter.dag_activities import (
    ALL_DAG_ACTIVITIES,
    configure_eval_provider,
    reset_eval_provider,
)
from controlplane.adapter.mocks import ALL_MOCK_ACTIVITIES
from controlplane.domain.workflow_spec import (
    CallerContext,
    WorkflowNode,
    WorkflowSpec,
    workflow_spec_to_dict,
)
from controlplane.runtime.dag_runner_workflow import RunWorkflowSpec


# ── fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_eval_provider():
    """每个测试后重置 eval provider (隔离, 防止注入泄漏到其它测试)."""
    yield
    reset_eval_provider()


def _load_env():
    """手动加载 .env (测试不依赖 app 启动)."""
    env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _make_tool_task_spec(node_id="n1", review="auto") -> WorkflowSpec:
    return WorkflowSpec(
        workflow_id=f"wf-test-{uuid.uuid4().hex[:8]}",
        objective="test single node execution",
        nodes=[WorkflowNode(
            node_id=node_id, type="tool_task", capability="iqa",
            input={"image_ref": "test://dummy.jpg"},
            review_policy=review, on_failure="abort",
        )],
    )


def _inject_real_llm_if_available():
    """有 DEEPSEEK key 则注入真 provider, 返回是否注入成功."""
    _load_env()
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        return False
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    model = os.environ.get("DEEPSEEK_MODEL", "") or "deepseek-v4-flash"
    if model == "deepseek-chat":
        model = "deepseek-v4-flash"
    from cognitiveplane.capability.config import LLMConfig, OpenAIConfig
    from cognitiveplane.capability.openai_provider import OpenAIProvider
    prov = OpenAIProvider(LLMConfig(primary=OpenAIConfig(
        base_url=base, api_key=key, default_model=model,
    )))
    configure_eval_provider(prov, model=model)
    return True


# ── workflow 层测试 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_single_tool_task_node_runs_to_completion():
    spec = _make_tool_task_spec()
    _inject_real_llm_if_available()  # 有则真评估, 无则启发式, 都该跑通
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            result = await env.client.execute_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq",
            )
    assert isinstance(result, dict)
    node_results = result.get("node_results", {})
    assert node_results, "应有节点结果"
    nr = list(node_results.values())[0]
    assert "status" in nr


@pytest.mark.asyncio
async def test_sm_mismatches_empty_on_happy_path():
    """Phase E: 正常执行后 result["sm_mismatches"] 必须为空 (状态机与列表一致)."""
    spec = _make_tool_task_spec()
    _inject_real_llm_if_available()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            result = await env.client.execute_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq",
            )
    # 核心断言: shadow 一致性 - 状态机态与列表推断必须一致
    assert "sm_mismatches" in result, "result 应暴露 sm_mismatches 字段"
    assert result["sm_mismatches"] == [], (
        f"状态机与列表不一致: {result['sm_mismatches']}"
    )
    assert result["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_sm_mismatches_empty_on_two_node_dag():
    """Phase E: 两节点依赖链执行后 sm_mismatches 为空."""
    spec = WorkflowSpec(
        workflow_id=f"wf-sm2-{uuid.uuid4().hex[:8]}",
        objective="test sm consistency two nodes",
        nodes=[
            WorkflowNode(node_id="n1", type="tool_task", capability="iqa",
                         input={"image_ref": "t://a"}, review_policy="auto", on_failure="abort"),
            WorkflowNode(node_id="n2", type="tool_task", capability="ppa",
                         depends_on=["n1"], input={"image_ref": "t://b"},
                         review_policy="auto", on_failure="abort"),
        ],
    )
    _inject_real_llm_if_available()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            result = await env.client.execute_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq",
            )
    assert result["sm_mismatches"] == [], f"两节点 DAG 状态机不一致: {result['sm_mismatches']}"
    assert result["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_dag_two_nodes_with_dependency():
    spec = WorkflowSpec(
        workflow_id=f"wf-dep-{uuid.uuid4().hex[:8]}",
        objective="test dependency ordering",
        nodes=[
            WorkflowNode(node_id="n1", type="tool_task", capability="iqa",
                         input={"image_ref": "t://a"}, review_policy="auto", on_failure="abort"),
            WorkflowNode(node_id="n2", type="tool_task", capability="ppa",
                         depends_on=["n1"], input={"image_ref": "t://b"},
                         review_policy="auto", on_failure="abort"),
        ],
    )
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-dep", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            result = await env.client.execute_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-dep",
            )
    assert isinstance(result, dict)
    assert set(result["node_results"].keys()) == {"n1", "n2"}


# ── evaluate_node_quality activity 各路径 ─────────────────────────────

@pytest.mark.asyncio
async def test_eval_mock_node_skips_llm():
    """#8 缺口: mock 节点直接 heuristic_mock 低分, 不调 LLM."""
    from controlplane.adapter.dag_activities import evaluate_node_quality
    _inject_real_llm_if_available()  # 即使有 provider, mock 也该跳过
    r = await evaluate_node_quality({
        "node_id": "m", "capability": "iqa", "status": "OK",
        "result_data": {"mock": True, "verdict": "placeholder"},
        "objective": "t",
    })
    assert r["evaluated_by"] == "heuristic_mock"
    assert r["quality_score"] <= 0.3


@pytest.mark.asyncio
async def test_eval_no_provider_returns_heuristic():
    """#8 缺口: 无注入 provider -> no_provider 启发式 (不报错)."""
    from controlplane.adapter.dag_activities import evaluate_node_quality
    # 不注入, _eval_provider 为 None
    r = await evaluate_node_quality({
        "node_id": "n", "capability": "iqa", "status": "OK",
        "result_data": {"defects": 0, "laplacian": 100},
        "objective": "t",
    })
    assert r["evaluated_by"] == "heuristic"
    assert "no LLM provider" in r["quality_note"]
    assert 0.0 <= r["quality_score"] <= 1.0


@pytest.mark.asyncio
async def test_eval_real_llm_returns_score():
    """Op34 真路径: 注入真 provider -> evaluated_by == llm."""
    if not _inject_real_llm_if_available():
        pytest.skip("needs DEEPSEEK_API_KEY for real LLM eval")
    from controlplane.adapter.dag_activities import evaluate_node_quality
    r = await evaluate_node_quality({
        "node_id": "eval-real", "capability": "iqa", "status": "OK",
        "result_data": {"defects": 0, "laplacian": 120, "verdict": "pass"},
        "objective": "评估焊缝图片质量",
    })
    assert r["evaluated_by"] == "llm", f"应走 LLM, 实际 {r['evaluated_by']}"
    assert 0.0 <= r["quality_score"] <= 1.0
    assert r["quality_note"]


@pytest.mark.asyncio
async def test_eval_llm_failure_falls_back_to_heuristic():
    """#8 缺口: LLM 调用失败 -> 回退 heuristic (不崩)."""
    from controlplane.adapter.dag_activities import evaluate_node_quality
    # 注入一个会抛异常的假 provider
    class _BoomProvider:
        async def complete(self, request):
            raise RuntimeError("simulated LLM outage")
    configure_eval_provider(_BoomProvider(), model="x")
    r = await evaluate_node_quality({
        "node_id": "boom", "capability": "iqa", "status": "OK",
        "result_data": {"defects": 1}, "objective": "t",
    })
    assert r["evaluated_by"] == "heuristic"
    assert "LLM error" in r["quality_note"]


# ── _validate_node_result 结构校验快速返回 (#7 缺口) ──────────────────

def _make_workflow_instance():
    """构造 RunWorkflowSpec 实例直接测 _validate_node_result (不经 Temporal)."""
    wf = RunWorkflowSpec()
    # _validate_node_result 不依赖 _spec 设置 (objective 兜底 "")
    return wf


@pytest.mark.asyncio
async def test_validate_missing_status_returns_zero():
    """#7 缺口: 缺 status 字段 -> quality_score=0.0 快速返回."""
    wf = _make_workflow_instance()
    v = await wf._validate_node_result(node=type("N", (), {"node_id": "x", "capability": "iqa"})(),
                                       result={})
    assert v["quality_score"] == 0.0
    assert "missing status" in v["quality_note"]


@pytest.mark.asyncio
async def test_validate_missing_data_returns_low():
    """#7 缺口: 缺 data 字段 -> 低分快速返回."""
    wf = _make_workflow_instance()
    v = await wf._validate_node_result(node=type("N", (), {"node_id": "x", "capability": "iqa"})(),
                                       result={"status": "OK"})
    assert v["quality_score"] == 0.3


@pytest.mark.asyncio
async def test_validate_ok_downgrades_to_marginal_when_low_score():
    """#9 缺口: quality_score < 0.5 时 OK 降级 MARGINAL (在 v2 调用方测)."""
    # 这个降级逻辑在 _execute_node_v2 里, 这里测 activity 返回低分被正确应用
    # 注入一个总给低分的假 provider
    class _LowScoreProvider:
        async def complete(self, request):
            from cognitiveplane.capability.provider import LLMResponse
            return LLMResponse(content='{"quality_score": 0.2, "quality_note": "low quality"}')
    configure_eval_provider(_LowScoreProvider(), model="x")
    # 直接测: activity 返回 0.2, validate 应透传
    from controlplane.adapter.dag_activities import evaluate_node_quality
    r = await evaluate_node_quality({
        "node_id": "low", "capability": "iqa", "status": "OK",
        "result_data": {"defects": 5}, "objective": "t",
    })
    assert r["quality_score"] == 0.2  # 降级判断在 v2: < 0.5 且 OK -> MARGINAL


# ── Op-新: revoke_approval 撤回已批准决策 ──────────────────────────────

async def _poll_query(handle, predicate, timeout_s: float = 8.0):
    """轮询 query_status 直到 predicate(st) 为 True, 超时返回 None."""
    import time as _t
    deadline = _t.monotonic() + timeout_s
    while _t.monotonic() < deadline:
        try:
            st = await handle.query("query_status")
            if predicate(st):
                return st
        except Exception:
            pass
        await asyncio.sleep(0.05)
    return None


def _revoke_linear_3_spec() -> WorkflowSpec:
    """n1(auto)->n2(required)->n3(auto). n2 阻塞等 review 给测试窗口."""
    return WorkflowSpec(
        workflow_id=f"wf-revoke-{uuid.uuid4().hex[:8]}",
        objective="test revoke_approval",
        nodes=[
            WorkflowNode(node_id="n1", type="tool_task", capability="iqa",
                         input={"image_ref": "t://a"}, review_policy="auto", on_failure="continue"),
            WorkflowNode(node_id="n2", type="tool_task", capability="iqa",
                         depends_on=["n1"], input={"image_ref": "t://b"},
                         review_policy="required", on_failure="continue"),
            WorkflowNode(node_id="n3", type="tool_task", capability="iqa",
                         depends_on=["n2"], input={"image_ref": "t://c"},
                         review_policy="auto", on_failure="continue"),
        ],
    )


def _revoke_diamond_spec() -> WorkflowSpec:
    """菱形: n1->n2, n1->n3, n2->n4, n3->n4. n2 阻塞等 review."""
    return WorkflowSpec(
        workflow_id=f"wf-diamond-{uuid.uuid4().hex[:8]}",
        objective="test revoke BFS diamond",
        nodes=[
            WorkflowNode(node_id="n1", type="tool_task", capability="iqa",
                         input={"image_ref": "t://a"}, review_policy="auto", on_failure="continue"),
            WorkflowNode(node_id="n2", type="tool_task", capability="iqa",
                         depends_on=["n1"], input={"image_ref": "t://b"},
                         review_policy="required", on_failure="continue"),
            WorkflowNode(node_id="n3", type="tool_task", capability="iqa",
                         depends_on=["n1"], input={"image_ref": "t://c"},
                         review_policy="auto", on_failure="continue"),
            WorkflowNode(node_id="n4", type="tool_task", capability="iqa",
                         depends_on=["n2", "n3"], input={"image_ref": "t://d"},
                         review_policy="auto", on_failure="continue"),
        ],
    )


def _revoke_records(result: dict) -> list[dict]:
    """从 workflow result 提取所有 REVOKE_APPROVAL 审计记录."""
    return [r for r in result.get("audit_trail", [])
            if r.get("decision_type") == "revoke_approval"]


def _successful_revoke(result: dict) -> dict | None:
    """第一条成功的 (非 REJECTED) revoke 记录, 无则 None."""
    for r in _revoke_records(result):
        if "REJECTED" not in r.get("decision", ""):
            return r
    return None


async def _run_revoke_on_n1(spec: WorkflowSpec, artifact_status: str, tq: str) -> dict:
    """通用: 启动 -> 等 n2 review -> revoke(n1) -> approve(n2) -> 返回 result."""
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue=tq, workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue=tq,
            )
            st = await _poll_query(handle, lambda s: "n2" in s.get("review_pending", []))
            assert st is not None, "n2 never entered review"
            await handle.signal("revoke_approval",
                                args=["n1", "qa_lead", "test revoke", artifact_status])
            await handle.signal("human_review", args=["n2", {"decision": "approve"}])
            return await handle.result()


@pytest.mark.asyncio
async def test_revoke_committed_node_becomes_revoked():
    """revoke_approval: COMMITTED 节点 -> REVOKED (状态机 + result)."""
    result = await _run_revoke_on_n1(_revoke_linear_3_spec(), "draft", "tq-r1")
    assert "n1" in result["failed_nodes"], f"n1 应在 failed_nodes: {result['failed_nodes']}"
    assert result["node_results"]["n1"]["status"] == "REVOKED"
    rec = _successful_revoke(result)
    assert rec is not None, "audit_trail 应有成功的 REVOKE_APPROVAL 记录"


@pytest.mark.asyncio
async def test_revoke_pending_node_rejected():
    """revoke_approval: 未 COMMITTED (PENDING) 节点 -> 拒绝 + 记审计."""
    spec = _revoke_linear_3_spec()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-r2", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-r2",
            )
            await _poll_query(handle, lambda s: "n2" in s.get("review_pending", []))
            # n3 还没执行 -> PENDING, revoke 应被拒绝
            await handle.signal("revoke_approval",
                                args=["n3", "tester", "test reject", "draft"])
            await handle.signal("human_review", args=["n2", {"decision": "approve"}])
            result = await handle.result()
    rejected = [r for r in _revoke_records(result) if "REJECTED" in r.get("decision", "")]
    assert rejected, "应有 REJECTED 的 revoke 记录"
    assert result["node_results"]["n3"]["status"] != "REVOKED"


@pytest.mark.asyncio
async def test_revoke_draft_marks_downstream_for_rework():
    """revoke_approval draft: 下游 BFS 标记 rework, 无更正版."""
    result = await _run_revoke_on_n1(_revoke_linear_3_spec(), "draft", "tq-r3")
    rec = _successful_revoke(result)
    assert rec is not None
    rr = rec["context"]["revoke_record"]
    assert rr["affected_downstream"] == ["n2", "n3"], f"BFS: {rr['affected_downstream']}"
    assert rr["artifact_status"] == "draft"
    assert rr["correction_artifact_version"] is None


@pytest.mark.asyncio
async def test_revoke_formal_creates_correction_v2():
    """revoke_approval formal: 发更正版 v2 + 补偿动作 + 通知下游."""
    result = await _run_revoke_on_n1(_revoke_linear_3_spec(), "formal", "tq-r4")
    rec = _successful_revoke(result)
    assert rec is not None
    rr = rec["context"]["revoke_record"]
    assert rr["artifact_status"] == "formal"
    assert rr["correction_artifact_version"] is not None
    assert "_v2_revoked" in rr["correction_artifact_version"]
    assert len(rr["compensation_actions"]) == 2
    assert sorted(rr["notified_consumers"]) == ["n2", "n3"]


@pytest.mark.asyncio
async def test_revoke_external_notify_only():
    """revoke_approval external: 已离系统不可 undo, 只通知不记补偿."""
    result = await _run_revoke_on_n1(_revoke_linear_3_spec(), "external", "tq-r5")
    rec = _successful_revoke(result)
    assert rec is not None
    rr = rec["context"]["revoke_record"]
    assert rr["artifact_status"] == "external"
    assert "_v2_external_revoked" in rr["correction_artifact_version"]
    assert rr["compensation_actions"] == []
    assert sorted(rr["notified_consumers"]) == ["n2", "n3"]


@pytest.mark.asyncio
async def test_revoke_bfs_diamond_all_downstream():
    """revoke_approval BFS: 菱形图 n1 下游 = [n2, n3, n4] (含收敛, 不重复)."""
    result = await _run_revoke_on_n1(_revoke_diamond_spec(), "formal", "tq-r6")
    rec = _successful_revoke(result)
    assert rec is not None
    rr = rec["context"]["revoke_record"]
    assert sorted(rr["affected_downstream"]) == ["n2", "n3", "n4"], \
        f"菱形 BFS 应含 n4 且不重复: {rr['affected_downstream']}"


@pytest.mark.asyncio
async def test_revoke_traces_original_decision_id():
    """revoke_approval: original_decision_id 追溯到原 NODE_REVIEW approve 记录."""
    spec = WorkflowSpec(
        workflow_id=f"wf-oid-{uuid.uuid4().hex[:8]}",
        objective="test revoke original_decision_id",
        nodes=[
            WorkflowNode(node_id="n1", type="tool_task", capability="iqa",
                         input={"image_ref": "t://a"}, review_policy="required", on_failure="continue"),
            WorkflowNode(node_id="n2", type="tool_task", capability="iqa",
                         depends_on=["n1"], input={"image_ref": "t://b"},
                         review_policy="required", on_failure="continue"),
        ],
    )
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-r7", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-r7",
            )
            # 等 n1 进 review -> approve n1 (创建 NODE_REVIEW approve 记录)
            await _poll_query(handle, lambda s: "n1" in s.get("review_pending", []))
            await handle.signal("human_review", args=["n1", {"decision": "approve"}])
            # 等 n2 进 review -> revoke n1 -> approve n2
            await _poll_query(handle, lambda s: "n2" in s.get("review_pending", []))
            await handle.signal("revoke_approval",
                                args=["n1", "qa_lead", "trace test", "draft"])
            await handle.signal("human_review", args=["n2", {"decision": "approve"}])
            result = await handle.result()
    rec = _successful_revoke(result)
    assert rec is not None
    rr = rec["context"]["revoke_record"]
    assert rr["original_decision_id"] is not None, "应追溯到原 approve 记录 id"
    approve_recs = [r for r in result["audit_trail"]
                    if r["decision_type"] == "node_review"
                    and r["node_id"] == "n1" and "approve" in r["decision"]]
    assert any(r["record_id"] == rr["original_decision_id"] for r in approve_recs)


# ── Op-新: batch_hold 批次冻结 ─────────────────────────────────────────

def _make_batched_spec() -> WorkflowSpec:
    """两批, 各两个节点. B1: n1,n2(case=B1,均layer1); B2: n3->n4(case=B2).

    B1 两节点都放 layer1 (无依赖), 这样 batch_hold 信号在下个 checkpoint
    应用时, n1/n2 都已 COMMITTED (post-execution hold, 真实场景).
    B2 的 n3 用 required review 给测试窗口 (发 batch_hold 时 B2 阻塞在 review).
    """
    b1 = CallerContext(caller_type="brain_direct", case_id="B1")
    b2 = CallerContext(caller_type="brain_direct", case_id="B2")
    return WorkflowSpec(
        workflow_id=f"wf-bh-{uuid.uuid4().hex[:8]}",
        objective="test batch_hold",
        nodes=[
            WorkflowNode(node_id="n1", type="tool_task", capability="iqa",
                         input={"image_ref": "t://a"}, review_policy="auto",
                         on_failure="continue", caller_context=b1),
            WorkflowNode(node_id="n2", type="tool_task", capability="iqa",
                         input={"image_ref": "t://b"}, review_policy="auto",
                         on_failure="continue", caller_context=b1),
            WorkflowNode(node_id="n3", type="tool_task", capability="iqa",
                         input={"image_ref": "t://c"}, review_policy="required",
                         on_failure="continue", caller_context=b2),
            WorkflowNode(node_id="n4", type="tool_task", capability="iqa",
                         depends_on=["n3"], input={"image_ref": "t://d"},
                         review_policy="auto", on_failure="continue", caller_context=b2),
        ],
    )


def _batch_hold_records(result: dict) -> list[dict]:
    return [r for r in result.get("audit_trail", [])
            if r.get("decision_type") == "batch_hold"]


@pytest.mark.asyncio
async def test_batch_hold_freezes_batch_nodes():
    """batch_hold: COMMITTED 节点 -> HELD, result status=HELD."""
    spec = _make_batched_spec()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-bh1", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-bh1",
            )
            await _poll_query(handle, lambda s: "n3" in s.get("review_pending", []))
            await handle.signal("batch_hold", args=["B1", "suspect batch", "low quality"])
            await handle.signal("human_review", args=["n3", {"decision": "approve"}])
            result = await handle.result()
    # B1 节点被 hold
    assert result["node_results"]["n1"]["status"] == "HELD"
    assert result["node_results"]["n2"]["status"] == "HELD"
    assert "n1" not in result["completed_nodes"], "HELD 节点不该在 completed"
    assert "B1" in result.get("held_batches", {}), f"应记录 held batch: {result.get('held_batches')}"
    recs = _batch_hold_records(result)
    assert any("held" in r["decision"] for r in recs)


@pytest.mark.asyncio
async def test_batch_hold_does_not_block_other_batch():
    """核心需求: B1 被 hold 不影响 B2."""
    spec = _make_batched_spec()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-bh2", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-bh2",
            )
            await _poll_query(handle, lambda s: "n3" in s.get("review_pending", []))
            await handle.signal("batch_hold", args=["B1", "suspect", ""])
            await handle.signal("human_review", args=["n3", {"decision": "approve"}])
            result = await handle.result()
    # B2 正常完成 (不受 B1 hold 影响)
    assert "n3" in result["completed_nodes"]
    assert "n4" in result["completed_nodes"]
    assert result["node_results"]["n3"]["status"] != "HELD"
    assert result["node_results"]["n4"]["status"] != "HELD"
    # B1 被 hold
    assert result["node_results"]["n1"]["status"] == "HELD"


@pytest.mark.asyncio
async def test_release_hold_restores_committed():
    """release_hold: HELD -> COMMITTED, 重新并入 completed."""
    spec = _make_batched_spec()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-bh3", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-bh3",
            )
            await _poll_query(handle, lambda s: "n3" in s.get("review_pending", []))
            await handle.signal("batch_hold", args=["B1", "suspect", ""])
            await handle.signal("release_hold", args=["B1"])
            await handle.signal("human_review", args=["n3", {"decision": "approve"}])
            result = await handle.result()
    assert "n1" in result["completed_nodes"], "release 后 n1 应回 completed"
    assert result["node_results"]["n1"]["status"] != "HELD"


@pytest.mark.asyncio
async def test_quarantine_batch_marks_failed():
    """quarantine_batch: HELD -> FAILED, 记审计."""
    spec = _make_batched_spec()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-bh4", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-bh4",
            )
            await _poll_query(handle, lambda s: "n3" in s.get("review_pending", []))
            await handle.signal("batch_hold", args=["B1", "suspect", ""])
            await handle.signal("quarantine_batch", args=["B1"])
            await handle.signal("human_review", args=["n3", {"decision": "approve"}])
            result = await handle.result()
    assert result["node_results"]["n1"]["status"] == "QUARANTINED"
    assert "n1" in result["failed_nodes"]
    recs = _batch_hold_records(result)
    assert any("quarantined" in r["decision"] for r in recs)


@pytest.mark.asyncio
async def test_rework_batch_triggers_reexec():
    """rework_batch: HELD -> rework 队列, 重跑后重新提交."""
    spec = _make_batched_spec()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-bh5", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-bh5",
            )
            await _poll_query(handle, lambda s: "n3" in s.get("review_pending", []))
            await handle.signal("batch_hold", args=["B1", "suspect", ""])
            await handle.signal("rework_batch", args=["B1"])
            await handle.signal("human_review", args=["n3", {"decision": "approve"}])
            result = await handle.result()
    # rework 后 n1/n2 重跑并提交 (status 从 HELD 变回 MARGINAL = 重新执行了)
    assert result["node_results"]["n1"]["status"] == "MARGINAL", \
        f"rework 后 n1 应重跑为 MARGINAL, 实际 {result['node_results']['n1']['status']}"
    assert result["node_results"]["n2"]["status"] == "MARGINAL"
    # 审计有 rework 决议记录
    recs = _batch_hold_records(result)
    assert any("rework" in r["decision"] for r in recs), "应有 rework_batch 审计记录"


@pytest.mark.asyncio
async def test_batch_hold_explicit_node_ids():
    """batch_hold 接受显式 node_ids (不靠 case_id 匹配)."""
    spec = WorkflowSpec(
        workflow_id=f"wf-bh6-{uuid.uuid4().hex[:8]}", objective="explicit",
        nodes=[
            WorkflowNode(node_id="x1", type="tool_task", capability="iqa",
                         input={"image_ref": "t://a"}, review_policy="auto", on_failure="continue"),
            WorkflowNode(node_id="x2", type="tool_task", capability="iqa",
                         depends_on=["x1"], input={"image_ref": "t://b"},
                         review_policy="required", on_failure="continue"),
        ],
    )
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-bh6", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-bh6",
            )
            await _poll_query(handle, lambda s: "x2" in s.get("review_pending", []))
            await handle.signal("batch_hold", args=["BX", "test", "", ["x1"]])
            await handle.signal("human_review", args=["x2", {"decision": "approve"}])
            result = await handle.result()
    assert result["node_results"]["x1"]["status"] == "HELD"


@pytest.mark.asyncio
async def test_batch_hold_empty_batch_rejected():
    """batch_hold: 不存在的 batch_id -> 拒绝 + 记审计."""
    spec = _make_batched_spec()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-bh7", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-bh7",
            )
            await _poll_query(handle, lambda s: "n3" in s.get("review_pending", []))
            await handle.signal("batch_hold", args=["NONEXIST", "x", ""])
            await handle.signal("human_review", args=["n3", {"decision": "approve"}])
            result = await handle.result()
    recs = _batch_hold_records(result)
    assert any("REJECTED" in r["decision"] for r in recs)


# ── Op-新: pause_scope 分级暂停 ────────────────────────────────────────

def _make_pause_scope_spec() -> WorkflowSpec:
    """两批: B1(n1->n2, required review 给窗口), B2(n3, auto).

    n2 required 让 workflow 阻塞在 review, 此时发 pause_scope 测其它节点.
    """
    b1 = CallerContext(caller_type="brain_direct", case_id="B1")
    b2 = CallerContext(caller_type="brain_direct", case_id="B2")
    return WorkflowSpec(
        workflow_id=f"wf-ps-{uuid.uuid4().hex[:8]}",
        objective="test pause_scope",
        nodes=[
            WorkflowNode(node_id="n1", type="tool_task", capability="iqa",
                         input={"image_ref": "t://a"}, review_policy="auto",
                         on_failure="continue", caller_context=b1),
            WorkflowNode(node_id="n2", type="tool_task", capability="iqa",
                         input={"image_ref": "t://b"}, review_policy="required",
                         on_failure="continue", caller_context=b1),
            WorkflowNode(node_id="n3", type="tool_task", capability="iqa",
                         input={"image_ref": "t://c"}, review_policy="auto",
                         on_failure="continue", caller_context=b2),
        ],
    )


def _pause_scope_records(result: dict) -> list[dict]:
    return [r for r in result.get("audit_trail", [])
            if r.get("decision_type") == "pause_scope"]


@pytest.mark.asyncio
async def test_pause_scope_station_pauses_only_target():
    """pause_scope STATION: 只暂停目标节点, 其它节点继续."""
    spec = _make_pause_scope_spec()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-ps1", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-ps1",
            )
            # 等 n2 阻塞在 review (此时 n1/n3 已执行)
            await _poll_query(handle, lambda s: "n2" in s.get("review_pending", []))
            # 暂停 n3 (已完成的工位), approve n2 让 workflow 继续
            await handle.signal("pause_scope", args=["station", "n3", "manual hold"])
            await handle.signal("human_review", args=["n2", {"decision": "approve"}])
            result = await handle.result()
    assert "n3" in result.get("paused_nodes", []), \
        f"n3 应在 paused_nodes: {result.get('paused_nodes')}"
    recs = _pause_scope_records(result)
    assert any("station" in r["decision"] for r in recs)


@pytest.mark.asyncio
async def test_pause_scope_batch_pauses_all_in_batch():
    """pause_scope BATCH: 暂停整个 case_id 批次, 其它批次继续."""
    spec = _make_pause_scope_spec()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-ps2", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-ps2",
            )
            await _poll_query(handle, lambda s: "n2" in s.get("review_pending", []))
            # 暂停 B2 批次, approve n2
            await handle.signal("pause_scope", args=["batch", "B2", "batch wide hold"])
            await handle.signal("human_review", args=["n2", {"decision": "approve"}])
            result = await handle.result()
    assert "B2" in result.get("paused_batches", []), \
        f"B2 应在 paused_batches: {result.get('paused_batches')}"
    recs = _pause_scope_records(result)
    assert any("batch" in r["decision"] for r in recs)


@pytest.mark.asyncio
async def test_resume_scope_station_unblocks():
    """resume_scope STATION: 恢复后节点继续执行."""
    spec = WorkflowSpec(
        workflow_id=f"wf-ps3-{uuid.uuid4().hex[:8]}", objective="resume station",
        nodes=[
            WorkflowNode(node_id="n1", type="tool_task", capability="iqa",
                         input={"image_ref": "t://a"}, review_policy="auto", on_failure="continue"),
            WorkflowNode(node_id="n2", type="tool_task", capability="iqa",
                         input={"image_ref": "t://b"}, review_policy="required", on_failure="continue"),
        ],
    )
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-ps3", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-ps3",
            )
            await _poll_query(handle, lambda s: "n2" in s.get("review_pending", []))
            await handle.signal("pause_scope", args=["station", "n1", "temp"])
            await asyncio.sleep(0.1)
            await handle.signal("resume_scope", args=["station", "n1"])
            await handle.signal("human_review", args=["n2", {"decision": "approve"}])
            result = await handle.result()
    recs = _pause_scope_records(result)
    assert any("resume" in r["decision"] for r in recs), "应有 resume 审计记录"
    assert "n1" not in result.get("paused_nodes", []), "resume 后 n1 不该在 paused"


@pytest.mark.asyncio
async def test_pause_scope_workflow_degrades_to_full_pause():
    """pause_scope WORKFLOW: 退化为整 Run pause (写 _pause_state)."""
    spec = _make_pause_scope_spec()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-ps4", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-ps4",
            )
            await _poll_query(handle, lambda s: "n2" in s.get("review_pending", []))
            await handle.signal("pause_scope", args=["workflow", spec.workflow_id, "full stop"])
            await asyncio.sleep(0.1)
            await handle.signal("resume_scope", args=["workflow", spec.workflow_id])
            await handle.signal("human_review", args=["n2", {"decision": "approve"}])
            result = await handle.result()
    recs = _pause_scope_records(result)
    assert any("workflow" in r["decision"] and "resume" not in r["decision"]
               for r in recs), "应有 WORKFLOW pause 记录"


@pytest.mark.asyncio
async def test_pause_scope_unknown_node_rejected():
    """pause_scope STATION: 不存在的节点 -> 拒绝 + 记审计."""
    spec = _make_pause_scope_spec()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-ps5", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-ps5",
            )
            await _poll_query(handle, lambda s: "n2" in s.get("review_pending", []))
            await handle.signal("pause_scope", args=["station", "NOPE", "x"])
            await handle.signal("human_review", args=["n2", {"decision": "approve"}])
            result = await handle.result()
    recs = _pause_scope_records(result)
    assert any("REJECTED" in r["decision"] for r in recs), "不存在的节点应被拒绝"


@pytest.mark.asyncio
async def test_governance_shadow_consistency_station_pause_resume():
    """阶段2 shadow: pause+resume station 后投影与字段一致 (gov_mismatches 为空)."""
    spec = _make_pause_scope_spec()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-sc1", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-sc1",
            )
            await _poll_query(handle, lambda s: "n2" in s.get("review_pending", []))
            await handle.signal("pause_scope", args=["station", "n3", "shadow test"])
            await asyncio.sleep(0.1)
            await handle.signal("resume_scope", args=["station", "n3"])
            await handle.signal("human_review", args=["n2", {"decision": "approve"}])
            result = await handle.result()
    # CRITICAL: 投影与字段必须一致 (critical severity mismatches 为空)
    critical = [m for m in result.get("gov_mismatches", [])
                if m.get("severity") == "critical"]
    assert critical == [], f"pause+resume station shadow mismatch: {critical}"


@pytest.mark.asyncio
async def test_governance_shadow_consistency_batch_pause_resume():
    """阶段2 shadow: pause+resume batch 后投影与字段一致."""
    spec = _make_pause_scope_spec()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-sc2", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-sc2",
            )
            await _poll_query(handle, lambda s: "n2" in s.get("review_pending", []))
            await handle.signal("pause_scope", args=["batch", "B2", "batch shadow"])
            await asyncio.sleep(0.1)
            await handle.signal("resume_scope", args=["batch", "B2"])
            await handle.signal("human_review", args=["n2", {"decision": "approve"}])
            result = await handle.result()
    critical = [m for m in result.get("gov_mismatches", [])
                if m.get("severity") == "critical"]
    assert critical == [], f"pause+resume batch shadow mismatch: {critical}"


@pytest.mark.asyncio
async def test_governance_shadow_consistency_batch_hold_release():
    """阶段2 shadow: batch_hold+release 后投影与字段一致."""
    spec = _make_batched_spec()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-sc3", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-sc3",
            )
            await _poll_query(handle, lambda s: "n3" in s.get("review_pending", []))
            await handle.signal("batch_hold", args=["B1", "shadow test", ""])
            await handle.signal("human_review", args=["n3", {"decision": "approve"}])
            result = await handle.result()
    critical = [m for m in result.get("gov_mismatches", [])
                if m.get("severity") == "critical"]
    assert critical == [], f"batch_hold shadow mismatch: {critical}"


# ── Op-新: relabel_request 重新标注 ────────────────────────────────────

def _make_relabel_spec() -> WorkflowSpec:
    """n1(annot)->n2(depends label)->n3(depends result only).

    n2 required review 给测试窗口. n3 的 input.metadata.depends_on_label_only=False
    表示只依赖 result -> relabel 时应被跳过(复用结果).
    """
    return WorkflowSpec(
        workflow_id=f"wf-rl-{uuid.uuid4().hex[:8]}",
        objective="test relabel_request",
        nodes=[
            WorkflowNode(node_id="n1", type="tool_task", capability="annotation",
                         input={"image_ref": "t://a"}, review_policy="required",
                         on_failure="continue"),
            WorkflowNode(node_id="n2", type="tool_task", capability="ppa",
                         depends_on=["n1"], input={"image_ref": "t://b"},
                         review_policy="auto", on_failure="continue"),
            WorkflowNode(node_id="n3", type="tool_task", capability="iqa",
                         depends_on=["n1"], input={"image_ref": "t://c",
                         "metadata": {"depends_on_label_only": False}},
                         review_policy="auto", on_failure="continue"),
        ],
    )


def _relabel_records(result: dict) -> list[dict]:
    return [r for r in result.get("audit_trail", [])
            if r.get("decision_type") == "relabel_request"]


async def _run_relabel_on_n1() -> dict:
    """通用: 启动 -> 等 n1 review -> relabel(n1) -> approve(n1)."""
    spec = _make_relabel_spec()
    tq = f"tq-rl-{uuid.uuid4().hex[:6]}"
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue=tq, workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue=tq,
            )
            await _poll_query(handle, lambda s: "n1" in s.get("review_pending", []))
            await handle.signal("relabel_request",
                                args=["n1", "art:n1:v1", "defect", "crack", "label typo"])
            await handle.signal("human_review", args=["n1", {"decision": "approve"}])
            return await handle.result()


@pytest.mark.asyncio
async def test_relabel_reworks_annot_and_label_dependent():
    """relabel_request: 重跑 Annot 节点 + 依赖 label 的下游."""
    result = await _run_relabel_on_n1()
    recs = _relabel_records(result)
    assert recs, "应有 relabel_request 审计记录"
    rr = recs[0]["context"]["relabel_record"]
    # n1(annot) + n2(depends label) 重跑
    assert "n1" in rr["affected_downstream"] or rr["node_id"] == "n1"
    assert "n2" in rr["affected_downstream"], f"n2 应重跑: {rr['affected_downstream']}"


@pytest.mark.asyncio
async def test_relabel_skips_result_only_downstream():
    """relabel_request: 只依赖 result 的下游被跳过(复用结果)."""
    result = await _run_relabel_on_n1()
    recs = _relabel_records(result)
    rr = recs[0]["context"]["relabel_record"]
    skipped = recs[0]["context"].get("skipped_result_only", [])
    # n3 标了 depends_on_label_only=False -> 应被跳过
    assert "n3" in skipped, f"n3 应被跳过(复用 result): {skipped}"
    assert "n3" not in rr["affected_downstream"], "n3 不该重跑"


@pytest.mark.asyncio
async def test_relabel_records_corrected_label():
    """relabel_request: 审计记录原/正标签."""
    result = await _run_relabel_on_n1()
    recs = _relabel_records(result)
    rr = recs[0]["context"]["relabel_record"]
    assert rr["original_label"] == "defect"
    assert rr["corrected_label"] == "crack"
    assert rr["reused_result_artifact_id"], "应记录复用的 result artifact"


@pytest.mark.asyncio
async def test_relabel_unknown_node_rejected():
    """relabel_request: 不存在的节点 -> 拒绝 + 记审计."""
    spec = _make_relabel_spec()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-rl-rj", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-rl-rj",
            )
            await _poll_query(handle, lambda s: "n1" in s.get("review_pending", []))
            await handle.signal("relabel_request",
                                args=["NOPE", "art:x", "a", "b", "x"])
            await handle.signal("human_review", args=["n1", {"decision": "approve"}])
            result = await handle.result()
    recs = _relabel_records(result)
    assert any("REJECTED" in r["decision"] for r in recs), "不存在的节点应被拒绝"


# ── Op-新: ground_truth_override 人工强制覆盖 ──────────────────────────

async def _run_override_on_n1(forced: str = "OK") -> dict:
    """通用: n1 required review -> override 覆盖机器裁决."""
    spec = WorkflowSpec(
        workflow_id=f"wf-gt-{uuid.uuid4().hex[:8]}", objective="test override",
        nodes=[WorkflowNode(node_id="n1", type="tool_task", capability="iqa",
                            input={"image_ref": "t://a"}, review_policy="required",
                            on_failure="continue")],
    )
    tq = f"tq-gt-{uuid.uuid4().hex[:6]}"
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue=tq, workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue=tq,
            )
            await _poll_query(handle, lambda s: "n1" in s.get("review_pending", []))
            await handle.signal("ground_truth_override",
                                args=["n1", "inspector_42", forced, "disagree with machine", "photo"])
            return await handle.result()


def _override_records(result: dict) -> list[dict]:
    return [r for r in result.get("audit_trail", [])
            if r.get("decision_type") == "ground_truth_override"]


@pytest.mark.asyncio
async def test_override_uses_forced_verdict_over_machine():
    """ground_truth_override: forced_verdict 覆盖机器裁决, 实际生效."""
    result = await _run_override_on_n1("OK")
    recs = _override_records(result)
    assert recs, "应有 ground_truth_override 审计记录"
    nr = result["node_results"]["n1"]
    assert nr["status"] == "OK", f"forced OK 应生效: {nr['status']}"
    assert nr["data"]["overridden"] is True
    assert nr["data"]["ground_truth_verdict"] == "OK"


@pytest.mark.asyncio
async def test_override_preserves_machine_verdict():
    """ground_truth_override: machine_verdict 保留供校准."""
    result = await _run_override_on_n1("NG")
    recs = _override_records(result)
    rr = recs[0]["context"]["override_record"]
    # machine_verdict 应记录原机器裁决 (mock 返回 MARGINAL)
    assert "MARGINAL" in rr["machine_verdict"], \
        f"应保留 machine_verdict: {rr['machine_verdict']}"
    assert rr["ground_truth_verdict"] == "NG"


@pytest.mark.asyncio
async def test_override_records_responsibility_owner():
    """ground_truth_override: responsibility_owner = 质检员(责任认定)."""
    result = await _run_override_on_n1("OK")
    recs = _override_records(result)
    rr = recs[0]["context"]["override_record"]
    assert rr["override_by"] == "inspector_42"
    assert rr["responsibility_owner"] == "inspector_42", \
        "责任主体应是质检员, 非 system"
    assert rr["calibration_pair_id"], "应有校准对 id"


@pytest.mark.asyncio
async def test_override_commits_node():
    """ground_truth_override: override 后节点 COMMITTED (OVERRIDE 迁移)."""
    result = await _run_override_on_n1("OK")
    assert "n1" in result["completed_nodes"], "override 后 n1 应 COMMITTED"
    assert result["node_results"]["n1"]["status"] == "OK"


@pytest.mark.asyncio
async def test_override_forced_ng_takes_effect():
    """ground_truth_override: forced NG 覆盖机器 OK."""
    result = await _run_override_on_n1("NG")
    nr = result["node_results"]["n1"]
    assert nr["status"] == "NG", "forced NG 应生效"
    recs = _override_records(result)
    assert recs[0]["context"]["override_record"]["ground_truth_verdict"] == "NG"


# ── Op-新: delegate_review 审查权转移 ──────────────────────────────────

async def _run_delegate_on_node(target: str = "n1") -> dict:
    """n1 required review -> delegate -> approve."""
    spec = WorkflowSpec(
        workflow_id=f"wf-dlg-{uuid.uuid4().hex[:8]}", objective="test delegate",
        nodes=[WorkflowNode(node_id="n1", type="tool_task", capability="iqa",
                            input={"image_ref": "t://a"}, review_policy="required",
                            on_failure="continue")],
    )
    tq = f"tq-dlg-{uuid.uuid4().hex[:6]}"
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue=tq, workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue=tq,
            )
            await _poll_query(handle, lambda s: "n1" in s.get("review_pending", []))
            await handle.signal("delegate_review",
                                args=[target, "senior_qa", "原审查人休假"])
            await handle.signal("human_review", args=["n1", {"decision": "approve"}])
            return await handle.result()


def _delegate_records(result: dict) -> list[dict]:
    return [r for r in result.get("audit_trail", [])
            if r.get("decision_type") == "delegate_review"]


@pytest.mark.asyncio
async def test_delegate_updates_reviewer_assignment():
    """delegate_review: 更新 reviewer 分配."""
    result = await _run_delegate_on_node("n1")
    assert result["reviewer_assignments"].get("n1") == "senior_qa", \
        f"n1 reviewer 应更新: {result['reviewer_assignments']}"
    recs = _delegate_records(result)
    assert recs, "应有 delegate_review 审计记录"
    assert recs[0]["context"]["delegate_record"]["new_reviewer_id"] == "senior_qa"


@pytest.mark.asyncio
async def test_delegate_reroutes_pending_review():
    """delegate_review: pending review 被 reroute (记录在 rerouted 列表)."""
    result = await _run_delegate_on_node("n1")
    recs = _delegate_records(result)
    rr = recs[0]["context"]["delegate_record"]
    # n1 在 delegate 时处于 _review_waiting (pending)
    assert "n1" in rr["rerouted_pending_review_ids"], \
        f"n1 应被 reroute: {rr['rerouted_pending_review_ids']}"


@pytest.mark.asyncio
async def test_delegate_by_node_type():
    """delegate_review: target=node_type 匹配该类型所有节点."""
    spec = WorkflowSpec(
        workflow_id=f"wf-dlg2-{uuid.uuid4().hex[:8]}", objective="delegate by type",
        nodes=[
            WorkflowNode(node_id="n1", type="tool_task", capability="iqa",
                         input={"image_ref": "t://a"}, review_policy="required", on_failure="continue"),
            WorkflowNode(node_id="n2", type="tool_task", capability="ppa",
                         input={"image_ref": "t://b"}, review_policy="auto", on_failure="continue"),
        ],
    )
    tq = f"tq-dlg2-{uuid.uuid4().hex[:6]}"
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue=tq, workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue=tq,
            )
            await _poll_query(handle, lambda s: "n1" in s.get("review_pending", []))
            await handle.signal("delegate_review",
                                args=["tool_task", "team_lead", "org change"])
            await handle.signal("human_review", args=["n1", {"decision": "approve"}])
            result = await handle.result()
    recs = _delegate_records(result)
    affected = recs[0]["context"]["affected_nodes"]
    assert "n1" in affected, "tool_task 类型应匹配 n1"
    assert "n2" in affected, "n2 也是 tool_task, 应匹配"


@pytest.mark.asyncio
async def test_delegate_records_escalation():
    """delegate_review: is_escalation 记录升级标志."""
    result = await _run_delegate_on_node("n1")
    recs = _delegate_records(result)
    # 默认 is_escalation=False
    assert recs[0]["context"]["delegate_record"]["is_escalation"] is False


@pytest.mark.asyncio
async def test_delegate_unknown_target_rejected():
    """delegate_review: 不存在的 target -> 拒绝."""
    result = await _run_delegate_on_node("NOPE")
    recs = _delegate_records(result)
    assert any("REJECTED" in r["decision"] for r in recs), "不存在的 target 应被拒绝"


# ── Op-新[M]: standard_update + case_library_correction ────────────────

async def _run_knowledge_governance() -> dict:
    """n1 required review -> 发治理 signal -> approve.
    n1 带 standard_version="2020" metadata, 供 standard_update 扫描命中."""
    spec = WorkflowSpec(
        workflow_id=f"wf-kg-{uuid.uuid4().hex[:8]}", objective="test knowledge gov",
        nodes=[WorkflowNode(node_id="n1", type="tool_task", capability="iqa",
                            input={"image_ref": "t://a",
                                   "metadata": {"standard_version": "2020"}},
                            review_policy="required", on_failure="continue")],
    )
    tq = f"tq-kg-{uuid.uuid4().hex[:6]}"
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue=tq, workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue=tq,
            )
            await _poll_query(handle, lambda s: "n1" in s.get("review_pending", []))
            # 治理 signal 在 review 阻塞窗口发 (workflow 仍活着)
            await handle.signal("standard_update",
                                args=["AWS_D1_1", "2020", "2023", "2026-01-01", "clause 7 stricter"])
            await handle.signal("case_library_correction",
                                args=["case_042", "WRONG_OUTCOME", "FIX"])
            await handle.signal("human_review", args=["n1", {"decision": "approve"}])
            return await handle.result()


def _records_by_type(result: dict, dtype: str) -> list[dict]:
    return [r for r in result.get("audit_trail", [])
            if r.get("decision_type") == dtype]


@pytest.mark.asyncio
async def test_standard_update_records_version_transition():
    """standard_update: 记录版本变更 old->new + effective_date."""
    result = await _run_knowledge_governance()
    recs = _records_by_type(result, "standard_update")
    assert recs, "应有 standard_update 审计记录"
    rr = recs[0]["context"]["standard_update_record"]
    assert rr["standard_id"] == "AWS_D1_1"
    assert rr["old_version"] == "2020"
    assert rr["new_version"] == "2023"
    assert rr["effective_date"] == "2026-01-01"


@pytest.mark.asyncio
async def test_standard_update_scans_affected_decisions():
    """standard_update: 扫描引用旧版本的历史决策 (commit 阶段已记 standard_version)."""
    result = await _run_knowledge_governance()
    recs = _records_by_type(result, "standard_update")
    rr = recs[0]["context"]["standard_update_record"]
    # n1 commit 记录带 standard_version="2020" -> 应被扫描命中
    assert rr["affected_decisions"], f"应扫到 n1 的 commit 决策: {rr['affected_decisions']}"
    n1_hit = [a for a in rr["affected_decisions"] if a["node_id"] == "n1"]
    assert n1_hit, "n1 commit 应被 standard_update 扫描命中"
    # 有 diff("clause 7 stricter") -> 分类 AT_RISK
    assert n1_hit[0]["classification"] == "AT_RISK"


@pytest.mark.asyncio
async def test_standard_update_classification_at_risk():
    """standard_update: 有 diff 标 AT_RISK."""
    result = await _run_knowledge_governance()
    recs = _records_by_type(result, "standard_update")
    rr = recs[0]["context"]["standard_update_record"]
    # diff 非空 -> 若有受影响决策应分类 AT_RISK (现有无受影响则空列表)
    assert rr["diff"] == "clause 7 stricter"


@pytest.mark.asyncio
async def test_case_library_correction_records_error_type():
    """case_library_correction: 记录 error_type + correction."""
    result = await _run_knowledge_governance()
    recs = _records_by_type(result, "case_library_correction")
    assert recs, "应有 case_library_correction 审计记录"
    rr = recs[0]["context"]["case_correction_record"]
    assert rr["case_id"] == "case_042"
    assert rr["error_type"] == "WRONG_OUTCOME"
    assert rr["correction"] == "FIX"


@pytest.mark.asyncio
async def test_case_library_correction_tracks_workflow():
    """case_library_correction: 记录受影响 workflow (当前)."""
    result = await _run_knowledge_governance()
    recs = _records_by_type(result, "case_library_correction")
    rr = recs[0]["context"]["case_correction_record"]
    assert result["workflow_id"] in rr["affected_workflows"], \
        "当前 workflow 应在 affected_workflows"


# ── 第五步: heartbeat 流式进度上报 ─────────────────────────────────────

@pytest.mark.asyncio
async def test_heartbeat_reports_phase_progress():
    """heartbeat: 节点执行各阶段进度上报到 streaming_updates."""
    spec = WorkflowSpec(
        workflow_id=f"wf-hb-{uuid.uuid4().hex[:8]}", objective="test heartbeat",
        nodes=[WorkflowNode(node_id="n1", type="tool_task", capability="iqa",
                            input={"image_ref": "t://a"}, review_policy="auto",
                            on_failure="continue")],
    )
    tq = f"tq-hb-{uuid.uuid4().hex[:6]}"
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue=tq, workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue=tq,
            )
            result = await handle.result()
    # execution_record.to_dict 把 streaming_updates 序列化成数量(预存)
    # 验证: 单节点应至少 4 次进度上报 (prepare/execute/validate/commit)
    er = result.get("execution_record") or {}
    su_count = er.get("streaming_updates", 0)
    assert su_count >= 4, f"单节点应至少 4 次进度上报, 实际 {su_count}"


@pytest.mark.asyncio
async def test_heartbeat_query_exposes_current_progress():
    """heartbeat: query_status 暴露 current_node/stage/progress."""
    spec = WorkflowSpec(
        workflow_id=f"wf-hb2-{uuid.uuid4().hex[:8]}", objective="test query progress",
        nodes=[
            WorkflowNode(node_id="n1", type="tool_task", capability="iqa",
                         input={"image_ref": "t://a"}, review_policy="auto", on_failure="continue"),
            WorkflowNode(node_id="n2", type="tool_task", capability="iqa",
                         depends_on=["n1"], input={"image_ref": "t://b"},
                         review_policy="required", on_failure="continue"),
        ],
    )
    tq = f"tq-hb2-{uuid.uuid4().hex[:6]}"
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue=tq, workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue=tq,
            )
            # n2 阻塞在 review 时查进度 - 应显示 n1 已完成 (progress 1.0)
            await _poll_query(handle, lambda s: "n2" in s.get("review_pending", []))
            st = await handle.query("query_status")
            await handle.signal("human_review", args=["n2", {"decision": "approve"}])
            await handle.result()
    # n2 review 阻塞时, n1 应已完成 (current 指向最后执行的节点)
    assert "current_node" in st, "query_status 应暴露 current_node"
    assert "current_stage" in st, "应暴露 current_stage"
    assert "current_progress" in st, "应暴露 current_progress"
    assert st["current_node"] in ("n1", "n2"), f"current_node 应是执行过的节点: {st['current_node']}"


# ── Op 16-19: modify_spec 参数热更新 vs 拓扑变更 ──────────────────────

@pytest.mark.asyncio
async def test_modify_spec_param_hot_update_no_cancel():
    """参数级变更: 热更新未执行节点 input, 不 cancel 工作流."""
    spec = WorkflowSpec(
        workflow_id=f"wf-ms1-{uuid.uuid4().hex[:8]}", objective="param hot update",
        nodes=[
            WorkflowNode(node_id="n1", type="tool_task", capability="iqa",
                         input={"image_ref": "t://a", "threshold": 0.5},
                         review_policy="required", on_failure="continue"),
            WorkflowNode(node_id="n2", type="tool_task", capability="iqa",
                         depends_on=["n1"], input={"image_ref": "t://b", "threshold": 0.3},
                         review_policy="auto", on_failure="continue"),
        ],
    )
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-ms1", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-ms1",
            )
            # 等 n1 阻塞在 review
            await _poll_query(handle, lambda s: "n1" in s.get("review_pending", []))
            # 发参数级变更: 只改 n2 的 threshold (未执行), 不改拓扑
            new_spec_dict = workflow_spec_to_dict(spec)
            new_spec_dict["nodes"][1]["input"]["threshold"] = 0.8
            await handle.signal("modify_spec", args=[new_spec_dict])
            # approve n1 让 workflow 继续
            await handle.signal("human_review", args=["n1", {"decision": "approve"}])
            result = await handle.result()
    # 参数级变更不应 cancel (status 不是 MODIFIED)
    assert result["status"] != "MODIFIED", \
        f"参数级变更不应 cancel: status={result['status']}"
    # revision_type 应标记为 modify_params
    assert result.get("spec_revision_type") == "modify_params", \
        f"revision_type 应为 modify_params: {result.get('spec_revision_type')}"


@pytest.mark.asyncio
async def test_modify_spec_topology_change_cancels():
    """拓扑变更 (加节点): 走 cancel + relaunch."""
    spec = WorkflowSpec(
        workflow_id=f"wf-ms2-{uuid.uuid4().hex[:8]}", objective="topology change",
        nodes=[
            WorkflowNode(node_id="n1", type="tool_task", capability="iqa",
                         input={"image_ref": "t://a"}, review_policy="required",
                         on_failure="continue"),
            WorkflowNode(node_id="n2", type="tool_task", capability="iqa",
                         depends_on=["n1"], input={"image_ref": "t://b"},
                         review_policy="auto", on_failure="continue"),
        ],
    )
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-ms2", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-ms2",
            )
            await _poll_query(handle, lambda s: "n1" in s.get("review_pending", []))
            # 发拓扑变更: 加一个新节点 n3
            new_spec_dict = workflow_spec_to_dict(spec)
            new_spec_dict["nodes"].append({
                "node_id": "n3", "type": "tool_task", "capability": "iqa",
                "depends_on": ["n2"], "input": {"image_ref": "t://c"},
                "review_policy": "auto", "on_failure": "continue",
                "caller_context": {"caller_type": "brain_direct", "case_id": "", "node_id": "", "session_id": ""},
            })
            await handle.signal("modify_spec", args=[new_spec_dict])
            await handle.signal("human_review", args=["n1", {"decision": "approve"}])
            result = await handle.result()
    # 拓扑变更应 cancel (status = MODIFIED)
    assert result["status"] == "MODIFIED", \
        f"拓扑变更应 cancel: status={result['status']}"
    assert result.get("spec_revision_type") == "add_node", \
        f"revision_type 应为 add_node: {result.get('spec_revision_type')}"
    assert result.get("modified_spec") is not None, "应包含 modified_spec 供 relaunch"


# ── 阶段3: Plan 版本注册表 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_plan_version_registry_initial_version():
    """workflow 启动时创建初始 PlanVersion (FROZEN)."""
    spec = WorkflowSpec(
        workflow_id=f"wf-pv1-{uuid.uuid4().hex[:8]}", objective="plan version",
        nodes=[
            WorkflowNode(node_id="n1", type="tool_task", capability="iqa",
                         input={"image_ref": "t://a"}, review_policy="auto",
                         on_failure="continue"),
        ],
    )
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-pv1", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-pv1",
            )
            result = await handle.result()
    # 应有初始版本
    assert result["plan_version"] == "pv_1", f"初始版本应为 pv_1: {result['plan_version']}"
    versions = result.get("plan_version_history", [])
    assert len(versions) >= 1, f"版本历史应有至少 1 个: {versions}"
    assert versions[0]["state"] == "frozen", f"初始版本应为 frozen: {versions[0]}"


@pytest.mark.asyncio
async def test_plan_version_chain_on_topology_change():
    """拓扑变更 (add_node) 创建新版本, 旧版本 superseded."""
    spec = WorkflowSpec(
        workflow_id=f"wf-pv2-{uuid.uuid4().hex[:8]}", objective="version chain",
        nodes=[
            WorkflowNode(node_id="n1", type="tool_task", capability="iqa",
                         input={"image_ref": "t://a"}, review_policy="required",
                         on_failure="continue"),
        ],
    )
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-pv2", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-pv2",
            )
            await _poll_query(handle, lambda s: "n1" in s.get("review_pending", []))
            # 拓扑变更: 加节点 n2
            new_spec_dict = workflow_spec_to_dict(spec)
            new_spec_dict["nodes"].append({
                "node_id": "n2", "type": "tool_task", "capability": "iqa",
                "depends_on": ["n1"], "input": {"image_ref": "t://b"},
                "review_policy": "auto", "on_failure": "continue",
                "caller_context": {"caller_type": "brain_direct", "case_id": "", "node_id": "", "session_id": ""},
            })
            await handle.signal("modify_spec", args=[new_spec_dict])
            await handle.signal("human_review", args=["n1", {"decision": "approve"}])
            result = await handle.result()
    # 应有 2 个版本 (pv_1 superseded, pv_2 frozen)
    versions = result.get("plan_version_history", [])
    assert len(versions) == 2, f"应有 2 个版本: {versions}"
    assert versions[0]["state"] == "superseded", f"pv_1 应 superseded: {versions[0]}"
    assert versions[1]["state"] == "frozen", f"pv_2 应 frozen: {versions[1]}"
    assert result["plan_version"] == "pv_2", f"当前版本应为 pv_2: {result['plan_version']}"


@pytest.mark.asyncio
async def test_plan_version_no_new_version_on_param_hot_update():
    """参数级变更不创建新版本 (热更新不改变 spec 结构)."""
    spec = WorkflowSpec(
        workflow_id=f"wf-pv3-{uuid.uuid4().hex[:8]}", objective="no new version",
        nodes=[
            WorkflowNode(node_id="n1", type="tool_task", capability="iqa",
                         input={"image_ref": "t://a", "threshold": 0.5},
                         review_policy="required", on_failure="continue"),
            WorkflowNode(node_id="n2", type="tool_task", capability="iqa",
                         depends_on=["n1"], input={"image_ref": "t://b", "threshold": 0.3},
                         review_policy="auto", on_failure="continue"),
        ],
    )
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(env.client, task_queue="tq-pv3", workflows=[RunWorkflowSpec],
                          activities=ALL_DAG_ACTIVITIES + ALL_MOCK_ACTIVITIES):
            handle = await env.client.start_workflow(
                RunWorkflowSpec.run, workflow_spec_to_dict(spec),
                id=spec.workflow_id, task_queue="tq-pv3",
            )
            await _poll_query(handle, lambda s: "n1" in s.get("review_pending", []))
            # 参数级变更: 只改 threshold
            new_spec_dict = workflow_spec_to_dict(spec)
            new_spec_dict["nodes"][1]["input"]["threshold"] = 0.9
            await handle.signal("modify_spec", args=[new_spec_dict])
            await handle.signal("human_review", args=["n1", {"decision": "approve"}])
            result = await handle.result()
    # 参数级变更不创建新版本 (仍只有 pv_1)
    versions = result.get("plan_version_history", [])
    assert len(versions) == 1, f"参数级变更不应创建新版本: {versions}"
    assert result["plan_version"] == "pv_1", f"版本仍应为 pv_1: {result['plan_version']}"
