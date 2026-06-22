"""test_annotate_tool — mock MCP 下 annotate_tool 入参出参正确。"""

import os

import pytest

os.environ.setdefault("MCP_TRANSPORT", "mock")

from shared.mcp_tools import annotate, McpBusinessError, McpInfraError  # noqa: E402
from shared.mcp_tools import mock_mcp_server  # noqa: E402


@pytest.fixture(autouse=True)
def reset_mock():
    mock_mcp_server.reset()
    yield
    mock_mcp_server.reset()


async def test_annotate_happy_path():
    """完整作业流程: create_job → create_task → trigger_agent → 轮询 → list_tasks"""
    result = await annotate(
        dataset_id="ds-001",
        job_name="焊缝标注作业",
        labels=["气孔", "裂纹"],
    )
    assert result["status"] == "completed"
    assert result["job_id"].startswith("job-")
    assert result["total_count"] >= 1
    assert result["completed_count"] >= 1
    assert len(result["tasks"]) >= 1
    assert result["tasks"][0]["status"] == "COMPLETED"


async def test_annotate_passes_labels_as_array():
    result = await annotate(
        dataset_id="ds-001",
        job_name="测试作业",
        labels=["气孔", "裂纹"],
    )
    # mock server 应记录 labels（存为逗号分隔字符串，和真实 API 一致）
    job = mock_mcp_server._jobs[result["job_id"]]
    assert "气孔" in job["labels"]
    assert "裂纹" in job["labels"]


async def test_annotate_business_failure():
    mock_mcp_server.set_failure_mode("business", "标注 server 拒绝：数据集不存在")
    with pytest.raises(McpBusinessError):
        await annotate(dataset_id="bad-ds", job_name="x")


async def test_annotate_infra_failure():
    mock_mcp_server.set_failure_mode("infra", "连接超时")
    with pytest.raises(McpInfraError):
        await annotate(dataset_id="ds-001", job_name="x")


async def test_annotate_poll_completes():
    """trigger_agent 后 mock 自动完成，轮询应立即返回 completed"""
    result = await annotate(
        dataset_id="ds-001",
        job_name="轮询测试",
        poll_interval=0.1,
        poll_timeout=5,
    )
    assert result["status"] == "completed"
