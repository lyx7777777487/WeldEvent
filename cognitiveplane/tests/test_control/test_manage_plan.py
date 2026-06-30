"""Tests for ManagePlanTool — plan §3.1 line 452 + §7 line 1193.

LLM 自主任务拆解 (借鉴 Claude Code TodoWrite). Phase 2 内存态.
"""

from __future__ import annotations

import pytest

from cognitiveplane.control.tools.manage_plan import ManagePlanTool


class TestManagePlanCreate:
    """create action: replaces entire list."""

    @pytest.mark.asyncio
    async def test_create_with_todos(self) -> None:
        tool = ManagePlanTool()
        result = await tool.execute(
            action="create",
            todos=[
                {"subject": "分析图1", "description": "角焊缝图1"},
                {"subject": "分析图2"},
            ],
        )
        assert result.error is None
        assert result.output["action"] == "added"
        assert result.output["total"] == 2
        plan = result.output["plan"]
        assert plan[0]["id"] == 1
        assert plan[0]["subject"] == "分析图1"
        assert plan[0]["status"] == "pending"
        assert plan[1]["id"] == 2

    @pytest.mark.asyncio
    async def test_create_replaces_existing(self) -> None:
        tool = ManagePlanTool()
        await tool.execute(action="create", todos=[{"subject": "旧任务1"}])
        result = await tool.execute(action="create", todos=[{"subject": "新任务1"}])
        assert result.output["total"] == 1
        assert result.output["plan"][0]["subject"] == "新任务1"
        assert result.output["plan"][0]["id"] == 1  # ID counter reset on create

    @pytest.mark.asyncio
    async def test_create_requires_todos(self) -> None:
        tool = ManagePlanTool()
        result = await tool.execute(action="create")
        assert result.error is not None
        assert "todos" in result.error

    @pytest.mark.asyncio
    async def test_create_rejects_empty_todos(self) -> None:
        tool = ManagePlanTool()
        result = await tool.execute(action="create", todos=[])
        assert result.error is not None


class TestManagePlanAppend:
    """append action: adds to existing list."""

    @pytest.mark.asyncio
    async def test_append_to_empty(self) -> None:
        tool = ManagePlanTool()
        result = await tool.execute(action="append", todos=[{"subject": "A"}])
        assert result.output["total"] == 1

    @pytest.mark.asyncio
    async def test_append_increments_id(self) -> None:
        tool = ManagePlanTool()
        await tool.execute(action="create", todos=[{"subject": "A"}, {"subject": "B"}])
        result = await tool.execute(action="append", todos=[{"subject": "C"}])
        assert result.output["plan"][-1]["id"] == 3
        assert result.output["total"] == 3


class TestManagePlanUpdateStatus:
    """update_status action: transitions one todo."""

    @pytest.mark.asyncio
    async def test_update_pending_to_in_progress(self) -> None:
        tool = ManagePlanTool()
        await tool.execute(action="create", todos=[{"subject": "A"}])
        result = await tool.execute(action="update_status", todo_id=1, status="in_progress")
        assert result.error is None
        assert result.output["todo"]["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_update_to_completed(self) -> None:
        tool = ManagePlanTool()
        await tool.execute(action="create", todos=[{"subject": "A"}])
        result = await tool.execute(action="update_status", todo_id=1, status="completed")
        assert result.output["todo"]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_update_unknown_id(self) -> None:
        tool = ManagePlanTool()
        await tool.execute(action="create", todos=[{"subject": "A"}])
        result = await tool.execute(action="update_status", todo_id=99, status="completed")
        assert result.error is not None
        assert "99" in result.error

    @pytest.mark.asyncio
    async def test_update_invalid_status(self) -> None:
        tool = ManagePlanTool()
        await tool.execute(action="create", todos=[{"subject": "A"}])
        result = await tool.execute(action="update_status", todo_id=1, status="done")
        assert result.error is not None
        assert "status" in result.error.lower()

    @pytest.mark.asyncio
    async def test_update_non_integer_id(self) -> None:
        tool = ManagePlanTool()
        result = await tool.execute(action="update_status", todo_id="1", status="completed")
        assert result.error is not None


class TestManagePlanListClear:
    """list + clear actions."""

    @pytest.mark.asyncio
    async def test_list_empty(self) -> None:
        tool = ManagePlanTool()
        result = await tool.execute(action="list")
        assert result.output["plan"] == []

    @pytest.mark.asyncio
    async def test_list_after_create(self) -> None:
        tool = ManagePlanTool()
        await tool.execute(action="create", todos=[{"subject": "A"}, {"subject": "B"}])
        result = await tool.execute(action="list")
        assert len(result.output["plan"]) == 2

    @pytest.mark.asyncio
    async def test_clear_empties_list(self) -> None:
        tool = ManagePlanTool()
        await tool.execute(action="create", todos=[{"subject": "A"}])
        result = await tool.execute(action="clear")
        assert result.output["plan"] == []
        assert result.output["cleared_count"] == 1

    @pytest.mark.asyncio
    async def test_clear_resets_id_counter(self) -> None:
        tool = ManagePlanTool()
        await tool.execute(action="create", todos=[{"subject": "A"}, {"subject": "B"}])
        await tool.execute(action="clear")
        result = await tool.execute(action="append", todos=[{"subject": "C"}])
        assert result.output["plan"][0]["id"] == 1


class TestManagePlanValidation:
    """Input validation."""

    @pytest.mark.asyncio
    async def test_invalid_action(self) -> None:
        tool = ManagePlanTool()
        result = await tool.execute(action="delete")
        assert result.error is not None
        assert "delete" in result.error

    @pytest.mark.asyncio
    async def test_todo_missing_subject(self) -> None:
        tool = ManagePlanTool()
        result = await tool.execute(action="create", todos=[{"description": "no subject"}])
        assert result.error is not None
        assert "subject" in result.error

    @pytest.mark.asyncio
    async def test_todo_non_dict_item(self) -> None:
        tool = ManagePlanTool()
        result = await tool.execute(action="create", todos=["not a dict"])
        assert result.error is not None

    def test_parameters_schema_matches_plan(self) -> None:
        """plan §3.5 工具参数原则: enum 优于自由文本. action/status 必须用 enum."""
        tool = ManagePlanTool()
        schema = tool.parameters_schema
        assert schema["properties"]["action"]["enum"] == ["append", "clear", "create", "list", "update_status"]
        assert schema["properties"]["status"]["enum"] == ["completed", "in_progress", "pending"]
        assert schema["required"] == ["action"]

    def test_to_function_definition_format(self) -> None:
        tool = ManagePlanTool()
        d = tool.to_function_definition()
        assert d["type"] == "function"
        assert d["function"]["name"] == "manage_plan"