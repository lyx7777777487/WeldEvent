from temporalio import activity

from controlplane.domain.activity import ActivityInput, ActivityOutput, ActivityStatus


def _to_dict(output: ActivityOutput) -> dict:
    return {"status": output.status.value, "data": output.data, "error": output.error}


@activity.defn(name="iqa_activity")
async def iqa_activity(input: ActivityInput) -> dict:
    return _to_dict(ActivityOutput(status=ActivityStatus.OK, data={"mock": "iqa_result"}))


@activity.defn(name="ppa_activity")
async def ppa_activity(input: ActivityInput) -> dict:
    return _to_dict(ActivityOutput(status=ActivityStatus.OK, data={"mock": "ppa_result"}))


@activity.defn(name="mea_activity")
async def mea_activity(input: ActivityInput) -> dict:
    return _to_dict(ActivityOutput(status=ActivityStatus.OK, data={"mock": "mea_result"}))


@activity.defn(name="rda_activity")
async def rda_activity(input: ActivityInput) -> dict:
    return _to_dict(ActivityOutput(status=ActivityStatus.OK, data={"mock": "rda_result"}))


@activity.defn(name="vda_activity")
async def vda_activity(input: ActivityInput) -> dict:
    return _to_dict(ActivityOutput(status=ActivityStatus.OK, data={"mock": "vda_result"}))


@activity.defn(name="rva_activity")
async def rva_activity(input: ActivityInput) -> dict:
    return _to_dict(ActivityOutput(status=ActivityStatus.OK, data={"mock": "rva_result"}))


@activity.defn(name="mta_activity")
async def mta_activity(input: ActivityInput) -> dict:
    return _to_dict(ActivityOutput(status=ActivityStatus.OK, data={"mock": "mta_result"}))


@activity.defn(name="hca_activity")
async def hca_activity(input: ActivityInput) -> dict:
    return _to_dict(ActivityOutput(status=ActivityStatus.OK, data={"mock": "hca_result"}))


ALL_MOCK_ACTIVITIES = [
    iqa_activity, ppa_activity, mea_activity, rda_activity,
    vda_activity, rva_activity, mta_activity, hca_activity,
]
