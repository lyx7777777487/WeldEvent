"""test_annotation_activity — Activity 层调 annotate_tool 的异常分流。"""

import os

import pytest

os.environ.setdefault("MCP_TRANSPORT", "mock")

from controlplane.domain.activity import ActivityInput, ActivityStatus  # noqa: E402
from executionplane.activities.annotation.activity import annotation_activity  # noqa: E402
from shared.mcp_tools import mock_mcp_server  # noqa: E402


@pytest.fixture(autouse=True)
def reset_mock():
    mock_mcp_server.reset()
    yield
    mock_mcp_server.reset()


async def test_activity_happy_path():
    input = ActivityInput(
        control_point_id="cp1",
        workflow_context={},
        params={
            "dataset_id": "ds-001",
            "job_name": "测试作业",
            "labels": ["气孔", "裂纹"],
        },
    )
    result = await annotation_activity(input)
    assert result["status"] == "OK"
    assert result["data"]["job_id"].startswith("job-")
    assert result["data"]["status"] == "completed"
    assert result["data"]["total_count"] >= 1


async def test_activity_business_failure_returns_error_not_raises():
    """业务失败 → 转 ERROR output，不抛异常"""
    mock_mcp_server.set_failure_mode("business", "数据集不存在")
    input = ActivityInput(
        control_point_id="cp1",
        workflow_context={},
        params={"dataset_id": "bad-ds", "job_name": "x"},
    )
    result = await annotation_activity(input)
    assert result["status"] == "ERROR"
    assert "MCP 业务失败" in result["error"]


async def test_activity_infra_failure_raises():
    """基础设施失败 → 抛 ApplicationError（触发 Temporal 重试）"""
    from temporalio.exceptions import ApplicationError

    mock_mcp_server.set_failure_mode("infra", "连接超时")
    input = ActivityInput(
        control_point_id="cp1",
        workflow_context={},
        params={"dataset_id": "ds-001", "job_name": "x"},
    )
    with pytest.raises(ApplicationError):
        await annotation_activity(input)


async def test_activity_missing_params():
    input = ActivityInput(
        control_point_id="cp1",
        workflow_context={},
        params={"dataset_id": ""},  # 缺 job_name
    )
    result = await annotation_activity(input)
    assert result["status"] == "ERROR"
    assert "dataset_id" in result["error"] or "job_name" in result["error"]
