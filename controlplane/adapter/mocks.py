"""Mock Temporal activities — 用于 L3 activity 未实现时的降级。

P0-1 fix (2026-07-06 audit): 所有 mock activity 必须返回 MARGINAL + mock=True，
绝不能返回 OK — 否则 dag_runner 会把假数据当成真实质检结果，造成工业质检
"假合格"安全风险。

iqa/ppa/annotation 已有真实实现（executionplane/activities/），但保留 mock
用于 L3 不可用时的降级测试。mea/rda/vda/rva/mta/hca 仍为纯 mock。
"""

from temporalio import activity

from controlplane.domain.activity import ActivityInput, ActivityOutput, ActivityStatus


def _mock_output(capability: str, reason: str = "capability not implemented") -> dict:
    """构造统一的 mock 降级返回值 — MARGINAL + mock=True。

    MARGINAL 让 dag_runner 把节点标记为 _mocked_nodes（而非 _completed_nodes），
    后续可区分真实结果 vs 降级结果。mock=True 让调用方能程序化检测降级。
    """
    output = ActivityOutput(
        status=ActivityStatus.MARGINAL,
        data={
            "mock": True,
            "mock_reason": reason,
            "capability": capability,
        },
    )
    return {"status": output.status.value, "data": output.data, "error": output.error}


@activity.defn(name="iqa_activity")
async def iqa_activity(input: ActivityInput) -> dict:
    return _mock_output("iqa", "IQA real impl exists; mock fallback for degraded mode")


@activity.defn(name="ppa_activity")
async def ppa_activity(input: ActivityInput) -> dict:
    return _mock_output("ppa", "PPA real impl exists; mock fallback for degraded mode")


@activity.defn(name="mea_activity")
async def mea_activity(input: ActivityInput) -> dict:
    return _mock_output("mea", "MEA (defect detection) not yet implemented")


@activity.defn(name="rda_activity")
async def rda_activity(input: ActivityInput) -> dict:
    return _mock_output("rda", "RDA (defect recognition) not yet implemented")


@activity.defn(name="vda_activity")
async def vda_activity(input: ActivityInput) -> dict:
    return _mock_output("vda", "VDA not yet implemented")


@activity.defn(name="rva_activity")
async def rva_activity(input: ActivityInput) -> dict:
    return _mock_output("rva", "RVA not yet implemented")


@activity.defn(name="mta_activity")
async def mta_activity(input: ActivityInput) -> dict:
    return _mock_output("mta", "MTA not yet implemented")


@activity.defn(name="hca_activity")
async def hca_activity(input: ActivityInput) -> dict:
    return _mock_output("hca", "HCA not yet implemented")


ALL_MOCK_ACTIVITIES = [
    iqa_activity, ppa_activity, mea_activity, rda_activity,
    vda_activity, rva_activity, mta_activity, hca_activity,
]
