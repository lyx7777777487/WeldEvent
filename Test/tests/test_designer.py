"""test_designer — mock LLM 下 designer 产出正确 template dict。"""

import pytest

from l1_agent.design.workflow_designer import design, LlmDesignError, LlmClient


class MockLlm(LlmClient):
    """mock LLM — 不调真 API，按预设返回 template。"""

    def __init__(self, template: dict | None = None, error: str | None = None):
        self._template = template
        self._error = error

    async def chat_with_tool(
        self, system: str, user: str, tools: list[dict], tool_choice: dict
    ) -> dict:
        if self._error:
            return {"error": self._error}
        return {"template": self._template or _default_template()}


def _default_template() -> dict:
    return {
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
                    "job_name": "焊缝标注作业",
                    "labels": ["气孔", "裂纹"],
                },
            }
        ],
        "transitions": [],
    }


async def test_design_happy_path():
    llm = MockLlm()
    result = await design(
        llm=llm,
        requirement="对焊缝图像做气孔和裂纹检测",
        dataset_summary={"id": "ds-001", "name": "焊缝数据集"},
    )
    assert result["id"] == "weld_annotation_v1"
    assert result["entry_point"] == "cp1"
    assert len(result["control_points"]) == 1
    cp = result["control_points"][0]
    assert cp["activity_binding"]["activity_name"] == "annotation"
    assert cp["params"]["dataset_id"] == "ds-001"
    assert cp["params"]["job_name"] == "焊缝标注作业"
    assert cp["params"]["labels"] == ["气孔", "裂纹"]


async def test_design_llm_error_raises():
    llm = MockLlm(error="LLM 未调用工具")
    with pytest.raises(LlmDesignError):
        await design(
            llm=llm,
            requirement="x",
            dataset_summary={},
        )


async def test_design_with_history():
    """带反馈历史的设计应把反馈拼进 user message"""
    llm = MockLlm()
    result = await design(
        llm=llm,
        requirement="标注焊缝",
        dataset_summary={"id": "ds-001"},
        history=[
            {"role": "validator", "content": "params 缺 dataset_id"},
            {"role": "user", "content": "请补上 dataset_id=ds-001"},
        ],
    )
    assert result["id"] == "weld_annotation_v1"
