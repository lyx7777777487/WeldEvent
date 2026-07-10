"""ManagePlanTool — LLM 自主任务拆解 (借鉴 Claude Code TodoWrite).

Plan §3.1 line 452: "manage_plan 任务清单管理 (LLM 自主决定要不要拆任务)"
Plan §7 line 1193: "阶段 2 — LLM 任务拆解 (借鉴 Claude Code TodoWrite，多图批量场景必需)"
Plan §13.1 line 2536: "任务管理 | manage_plan 工具 | 借鉴 Claude Code TodoWrite"

设计要点 (plan §0.1 规则 2 line 153):
  - 架构不替 LLM 决定"要不要拆任务"——LLM 自己判断
  - 单图场景 LLM 可能完全不调本工具, 直接走 ReAct → 这是合法行为
  - 批量图场景 LLM 自主调用 create → update_status 跟踪进度
  - 不持久化 (Phase 2 内存态; Phase 4+ Memory 持久化时再迁移)

状态作用域: 进程级单 plan. Phase 2 单操作员 MVP 够用.
Phase 3 AgentLoop 引入后, 改为 per-session (plan §3.2 AgentLoop session 隔离).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cognitiveplane.control.tools import BrainTool, ToolResult


_VALID_ACTIONS = frozenset({"create", "append", "update_status", "list", "clear"})
_VALID_STATUSES = frozenset({"pending", "in_progress", "completed"})


class ManagePlanTool(BrainTool):
    """LLM-controlled task list — TodoWrite-style.

    单 plan 进程级状态. LLM 自主决定何时 create / update / clear.
    架构不主动催促 LLM 使用, 也不替 LLM 决定任务粒度.
    """

    phase = 3

    def __init__(self) -> None:
        # 进程级单 plan: list of {id, subject, description, status, created_at, updated_at}
        self._todos: list[dict[str, Any]] = []
        self._next_id: int = 1

    @property
    def name(self) -> str:
        return "manage_plan"

    @property
    def description(self) -> str:
        return (
            "管理当前会话的任务列表（TodoWrite 风格）。\n"
            "**何时使用**：复杂多步骤任务需要跟踪进度时。简单单步任务不需要。\n"
            "**用法**：action='create' 替换全部任务列表；"
            "action='update_status' 更新单个任务状态（pending/in_progress/completed）；"
            "action='append' 追加新任务；"
            "action='list' 列出当前任务；"
            "action='clear' 清空列表。"
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": sorted(_VALID_ACTIONS),
                    "description": "What to do with the task list.",
                },
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "subject": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["subject"],
                    },
                    "description": "Required for 'create' and 'append'. New todos to add (status defaults to pending).",
                },
                "todo_id": {
                    "type": "integer",
                    "description": "Required for 'update_status'. ID of the todo to update.",
                },
                "status": {
                    "type": "string",
                    "enum": sorted(_VALID_STATUSES),
                    "description": "Required for 'update_status'. New status.",
                },
            },
            "required": ["action"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        action = kwargs.get("action", "")
        if action not in _VALID_ACTIONS:
            return ToolResult(
                error=f"Invalid action: {action!r}. Must be one of {sorted(_VALID_ACTIONS)}",
            )

        if action == "create":
            return self._create(kwargs.get("todos"))
        if action == "append":
            return self._append(kwargs.get("todos"))
        if action == "update_status":
            return self._update_status(kwargs.get("todo_id"), kwargs.get("status"))
        if action == "list":
            return self._list()
        if action == "clear":
            return self._clear()
        return ToolResult(error=f"Unhandled action: {action}")

    # ── actions ──

    def _create(self, todos: Any) -> ToolResult:
        if not isinstance(todos, list) or not todos:
            return ToolResult(error="'create' requires non-empty 'todos' array")
        self._todos = []
        self._next_id = 1
        return self._add_todos(todos)

    def _append(self, todos: Any) -> ToolResult:
        if not isinstance(todos, list) or not todos:
            return ToolResult(error="'append' requires non-empty 'todos' array")
        return self._add_todos(todos)

    def _add_todos(self, todos: list[Any]) -> ToolResult:
        added: list[dict[str, Any]] = []
        for item in todos:
            if not isinstance(item, dict):
                return ToolResult(error=f"Todo item must be object, got {type(item).__name__}")
            subject = item.get("subject")
            if not subject or not isinstance(subject, str):
                return ToolResult(error="Each todo requires non-empty 'subject' string")
            now = datetime.now(timezone.utc).isoformat()
            todo = {
                "id": self._next_id,
                "subject": subject,
                "description": item.get("description", ""),
                "status": "pending",
                "created_at": now,
                "updated_at": now,
            }
            self._todos.append(todo)
            self._next_id += 1
            added.append(todo)
        return ToolResult(output={
            "action": "added",
            "added": added,
            "total": len(self._todos),
            "plan": self._snapshot(),
        })

    def _update_status(self, todo_id: Any, status: Any) -> ToolResult:
        if not isinstance(todo_id, int):
            return ToolResult(error=f"'todo_id' must be integer, got {todo_id!r}")
        if status not in _VALID_STATUSES:
            return ToolResult(
                error=f"Invalid status: {status!r}. Must be one of {sorted(_VALID_STATUSES)}",
            )
        for todo in self._todos:
            if todo["id"] == todo_id:
                todo["status"] = status
                todo["updated_at"] = datetime.now(timezone.utc).isoformat()
                return ToolResult(output={
                    "action": "updated",
                    "todo": todo,
                    "plan": self._snapshot(),
                })
        return ToolResult(error=f"todo_id {todo_id} not found in current plan")

    def _list(self) -> ToolResult:
        return ToolResult(output={
            "action": "list",
            "plan": self._snapshot(),
        })

    def _clear(self) -> ToolResult:
        cleared = len(self._todos)
        self._todos = []
        self._next_id = 1
        return ToolResult(output={
            "action": "cleared",
            "cleared_count": cleared,
            "plan": [],
        })

    def _snapshot(self) -> list[dict[str, Any]]:
        """Return a shallow copy of current plan (no internal timestamps leaked beyond what LLM needs)."""
        return [
            {
                "id": t["id"],
                "subject": t["subject"],
                "description": t["description"],
                "status": t["status"],
            }
            for t in self._todos
        ]
