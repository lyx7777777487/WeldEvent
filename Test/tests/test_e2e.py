"""test_e2e — 端到端: mock LLM + mock MCP + validator + runner。

验证 design → validate → run 全流程，不连真 LLM / 真 MCP / 真 Temporal。
"""

import os

import pytest

os.environ.setdefault("MCP_TRANSPORT", "mock")

from l1_agent.design.workflow_designer import design, LlmClient  # noqa: E402
from l1_agent.design.template_validator import validate  # noqa: E402
from l1_agent.execution.workflow_runner import run_workflow  # noqa: E402
from shared.mcp_tools import mock_mcp_server  # noqa: E402


@pytest.fixture(autouse=True)
def reset_mock():
    mock_mcp_server.reset()
    yield
    mock_mcp_server.reset()


class MockLlm(LlmClient):
    async def chat_with_tool(
        self, system: str, user: str, tools: list[dict], tool_choice: dict
    ) -> dict:
        return {
            "template": {
                "id": "weld_annotation_v1",
                "version": "1",
                "entry_point": "cp1",
                "control_points": [
                    {
                        "id": "cp1",
                        "name": "annotation",
                        "activity_binding": {"activity_name": "annotation"},
                        "execution_policy": {},
                        "params": {
                            "dataset_id": "ds-001",
                            "job_name": "E2E 测试作业",
                            "labels": ["气孔", "裂纹"],
                        },
                    }
                ],
                "transitions": [],
            }
        }


async def test_e2e_design_validate_run():
    """完整流程: LLM 设计 → 验证 → 执行"""
    # 1. Design
    llm = MockLlm()
    template = await design(
        llm=llm,
        requirement="对焊缝图像做气孔和裂纹检测",
        dataset_summary={"id": "ds-001", "name": "焊缝数据集"},
    )
    assert template["id"] == "weld_annotation_v1"

    # 2. Validate
    result = validate(template)
    assert result.ok is True

    # 3. Run
    results = await run_workflow(template)
    assert len(results) == 1
    cp_id, output = results[0]
    assert cp_id == "cp1"
    assert output["status"] == "OK"
    assert output["data"]["job_id"].startswith("job-")
    assert output["data"]["status"] == "completed"
    assert output["data"]["total_count"] >= 1
    assert output["data"]["tasks"][0]["status"] == "COMPLETED"
