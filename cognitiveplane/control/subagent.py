"""声明式 Subagent 系统 — 借鉴 Codex TOML + Claude Code SKILL.md。

架构：
  - 统一声明加载器：DeclarationLoader 加载 .weldevent/agents/*.md 声明文件
  - SubAgentRunner: 复用 ReActEngine 跑子任务（工具子集 + 独立 session）
  - DelegateTool: 主 agent 调 delegate(subagent, task) 委派任务

设计约束：
  - 子 agent 只允许声明中列出的工具（tools 字段），防止越权
  - 子 agent 独立 session，不影响主 agent 上下文
  - 子 agent 结果追加到主 agent session 的 messages 中
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4
from typing import Any, TYPE_CHECKING

from cognitiveplane.control.declaration import DeclarationLoader, Declaration

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from cognitiveplane.control.deps import CognitiveDependencies

# 默认 agents 目录
AGENTS_DIR = Path(".weldevent/agents")


class SubAgentRunner:
    """复用 ReActEngine 跑子 agent 任务。

    用法：
        loader = DeclarationLoader()
        runner = SubAgentRunner(deps, loader, tool_registry, image_store, event_log)
        result = await runner.run("weld-explorer", "列出所有数据集")
    """

    MAX_SUBAGENT_RESULT_CHARS = 4000  # 子 agent 结果截断上限

    def __init__(
        self,
        deps: "CognitiveDependencies",
        declaration_loader: DeclarationLoader,
        tool_registry: "Any",
        image_store: "Any | None" = None,
        event_log: "Any | None" = None,
        trajectory_store: "Any | None" = None,
    ) -> None:
        self._deps = deps
        self._loader = declaration_loader
        self._tool_registry = tool_registry
        self._image_store = image_store
        self._event_log = event_log
        self._trajectory_store = trajectory_store
        # Op 8.6: Capability matcher for dynamic subagent selection
        from cognitiveplane.control.planner.capability_match import CapabilityMatcher
        from cognitiveplane.control.planner.advanced import ParallelSpecialistRunner
        from cognitiveplane.control.planner.ledger import SpecialistRole
        self._capability_matcher = CapabilityMatcher(
            llm_provider=deps.capability.llm_provider if deps and hasattr(deps, 'capability') else None,
        )
        # Op 35: Parallel specialist runner for independent sub-tasks
        self._parallel_runner = ParallelSpecialistRunner()

    async def run_parallel(
        self,
        tasks: list[tuple[str, str]],
        parent_session_id: str = "",
        event_callback: Any | None = None,
        parent_agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Op 35: Run multiple independent subagents in parallel.

        Source: Anthropic orchestrator-workers pattern.

        Args:
            tasks: list of (subagent_name, task_text) pairs
        Returns aggregated results from all subagents.
        """
        from cognitiveplane.control.planner.ledger import SpecialistRole
        import asyncio

        async def _run_one(name: str, task_text: str) -> dict[str, Any]:
            return await self.run(name, task_text, parent_session_id,
                                  event_callback, parent_agent_id)

        coros = [
            (SpecialistRole.EXECUTOR, _run_one(name, task_text))
            for name, task_text in tasks
        ]
        results = await self._parallel_runner.run_parallel(coros)
        return {
            "success": all(r.get("success", False) for r in results.values()),
            "results": results,
            "parallel_count": len(tasks),
        }

    async def run(
        self,
        subagent_name: str,
        task: str,
        parent_session_id: str = "",
        event_callback: Any | None = None,
        parent_agent_id: str | None = None,
    ) -> dict[str, Any]:
        """运行子 agent 任务。

        Args:
            subagent_name: 子 agent 名称（如 weld-explorer）
            task: 委派的任务描述
            parent_session_id: 主 agent 的 session_id

        Returns:
            {
                "success": bool,
                "result": str,
                "error": str | None,
                "tools_used": list[str],
                "agent_id": str,
            }
        """
        agent_id = f"subagent_{uuid4().hex[:10]}"
        decl = self._loader.get_agent(subagent_name)
        if decl is None:
            # Op 8.6: Try capability-based matching before giving up
            available_agents = self._loader.load_agents()
            if available_agents:
                # Register all available agents with the matcher
                for name, d in available_agents.items():
                    self._capability_matcher.register_from_dict({
                        "agent_id": name,
                        "name": name,
                        "description": getattr(d, 'description', name),
                        "capabilities": [getattr(d, 'description', name)],
                        "tools": getattr(d, 'allowed_tools', []),
                    })
                # Try to match by task description
                try:
                    import asyncio
                    matches = await self._capability_matcher.match(task, top_k=1)
                    if matches:
                        subagent_name = matches[0].agent_id
                        decl = self._loader.get_agent(subagent_name)
                except Exception:
                    pass
            if decl is None:
                available = list(available_agents.keys())
                return {
                    "success": False,
                    "result": "",
                    "error": f"Subagent '{subagent_name}' not found. Available: {available}",
                    "tools_used": [],
                    "agent_id": agent_id,
                }

        from cognitiveplane.control.engine.react import ReActEngine
        from cognitiveplane.shared.dto.context import ContextSnapshot
        from cognitiveplane.shared.enums import EventType, NoveltyLevel
        from cognitiveplane.shared.types import CaseId
        from datetime import datetime, timezone

        # 构建子 agent 的 ReActEngine（工具子集 + 独立 session）
        subagent_session_id = f"{parent_session_id}-{subagent_name}"
        async def emit(event_type: str, payload: dict[str, Any]) -> None:
            if event_callback is None:
                return
            event_name = {
                "thinking": "subagent_thinking",
                "tool_call": "subagent_tool_call",
                "tool_result": "subagent_tool_result",
                "final": "subagent_final",
            }.get(event_type)
            if event_name is None:
                return
            await event_callback(event_name, {
                "agent_id": agent_id,
                "parent_agent_id": parent_agent_id,
                "subagent": subagent_name,
                "task": task,
                **payload,
            })

        engine = ReActEngine(
            self._deps,
            tool_registry=self._tool_registry,
            max_iterations=decl.max_iterations,
            image_store=self._image_store,
            event_log=self._event_log,
            trajectory_store=self._trajectory_store,
            event_callback=emit,
        )

        context = ContextSnapshot(
            case_id=CaseId(value=f"subagent-{subagent_name}"),
            event_type=EventType.WORKFLOW_ENTERED,
            workflow_state={},
            case_data={"subagent": subagent_name, "parent_session": parent_session_id},
            measurements=[],
            memory_match_confidence=0.5,
            knowledge_coverage=0.5,
            event_novelty=NoveltyLevel.PARTIAL,
            validation_critical_count=0,
            timestamp=datetime.now(timezone.utc),
        )

        # 注入子 agent 专属 system prompt + 工具白名单
        session: dict[str, Any] = {
            "session_id": subagent_session_id,
            "_parent_agent_id": agent_id,
            "subagent": {
                "name": subagent_name,
                "allowed_tools": frozenset(decl.tools),
                "system_prompt_override": decl.build_system_prompt(),
            },
        }

        try:
            if event_callback is not None:
                await event_callback("subagent_started", {
                    "agent_id": agent_id,
                    "parent_agent_id": parent_agent_id,
                    "subagent": subagent_name,
                    "task": task,
                    "allowed_tools": list(decl.tools),
                })
            response = await engine.run(
                user_input=f"[Subagent {subagent_name}] {task}",
                context=context,
                session=session,
            )
            result = response.text_reply or ""
            if len(result) > self.MAX_SUBAGENT_RESULT_CHARS:
                result = result[:self.MAX_SUBAGENT_RESULT_CHARS] + "\n\n... (截断)"

            if event_callback is not None:
                await event_callback("subagent_completed", {
                    "agent_id": agent_id,
                    "parent_agent_id": parent_agent_id,
                    "subagent": subagent_name,
                    "task": task,
                    "success": response.error is None,
                    "result": result,
                    "tools_used": response.tools_used,
                })

            return {
                "success": response.error is None,
                "result": result,
                "error": response.error,
                "tools_used": response.tools_used,
                "agent_id": agent_id,
            }
        except Exception as e:
            logger.exception("SubAgentRunner failed for %s", subagent_name)
            if event_callback is not None:
                await event_callback("subagent_failed", {
                    "agent_id": agent_id,
                    "parent_agent_id": parent_agent_id,
                    "subagent": subagent_name,
                    "task": task,
                    "error": str(e),
                })
            return {
                "success": False,
                "result": "",
                "error": str(e),
                "tools_used": [],
                "agent_id": agent_id,
            }
