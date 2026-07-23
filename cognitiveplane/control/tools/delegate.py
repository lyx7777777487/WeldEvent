"""DelegateTool — 委派子任务到专用 subagent。

借鉴 Codex sub-agent 委派 + Claude Code feature-dev plugin 的 3 种专用 agent。
主 agent 调 delegate(subagent_name, task) 将子任务委派给 explorer/architect/reviewer 之一。
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from cognitiveplane.control.tools import BrainTool, ToolResult
from cognitiveplane.control.declaration import DeclarationLoader

if TYPE_CHECKING:
    from cognitiveplane.control.subagent import SubAgentRunner


class DelegateTool(BrainTool):
    """将子任务委派给专用 subagent 执行。

    适用场景：
      - 搜索信息（用 weld-explorer 并行搜索标准/案例/数据集）
      - 设计工作流（用 weld-architect 编排 IQA/PPA/标注流程）
      - 审查结果（用 weld-reviewer 检查标注质量）

    子 agent 只允许预声明的工具白名单，防止越权。
    """

    phase = 3  # L3 执行层工具
    # delegate 不再 always_available - 当 skill 锁定时，delegate 受白名单约束。
    # 这防止 LLM 在缺工具时走 delegate 绕路而非切换 skill 或直接调可用工具。
    # 需要委派的场景（多步骤探索/复杂设计）应在 skill 白名单中显式包含 delegate。

    def __init__(self, runner: "SubAgentRunner") -> None:
        self._runner = runner

    @property
    def name(self) -> str:
        return "delegate"

    @property
    def description(self) -> str:
        # 动态加载 subagent 声明，避免硬编码工具列表
        loader = DeclarationLoader()
        agents = loader.load_agents()

        parts = [
            "委派子任务到专用 subagent。可用 subagent：",
        ]
        for name, decl in sorted(agents.items()):
            parts.append(f"- {name}: {decl.description}（工具: {', '.join(decl.tools[:5])}）")

        parts.append(
            "\n使用示例：\n"
            "- delegate('weld-explorer', '列出所有可用的数据集')\n"
            "- delegate('weld-architect', '设计一个焊缝质量检测工作流，先做 IQA 再做 PPA')\n"
            "- delegate('weld-reviewer', '检查作业的标注质量')\n"
            "- delegate('data-understanding', '分析数据集质量')\n\n"
            "注意：子 agent 独立执行，不会影响主 agent 的上下文。执行完成后返回结果。"
        )
        return "\n".join(parts)

    @property
    def parameters_schema(self) -> dict[str, Any]:
        loader = DeclarationLoader()
        agents = loader.load_agents()
        agent_names = sorted(agents.keys()) if agents else ["weld-explorer", "weld-architect", "weld-reviewer", "data-understanding"]
        return {
            "type": "object",
            "properties": {
                "subagent": {
                    "type": "string",
                    "description": "委派的 subagent 名称",
                    "enum": agent_names,
                },
                "task": {
                    "type": "string",
                    "description": "委派给子 agent 的具体任务描述",
                },
            },
            "required": ["subagent", "task"],
        }

    async def execute(
        self,
        subagent: str = "",
        task: str = "",
        session_id: str = "",
        event_callback: Any = None,
        parent_agent_id: str | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        """执行子 agent 委派。

        Args:
            subagent: 委派的 subagent 名称
            task: 委派给子 agent 的具体任务描述
            session_id: 主 agent 的 session_id（由 ToolExecutor 注入）
            event_callback: 子 agent 事件回调（由 ToolExecutor 注入）
            parent_agent_id: 父 agent ID（由 ToolExecutor 注入）
        """
        subagent_name = subagent
        task_text = task
        parent_session_id = session_id

        if not subagent_name or not task_text:
            return ToolResult(
                output={},
                error="Missing required parameters: subagent and task",
                error_type="state",
            )

        result = await self._runner.run(
            subagent_name,
            task_text,
            parent_session_id,
            event_callback=event_callback,
            parent_agent_id=parent_agent_id,
        )

        return ToolResult(
            output={
                "agent_id": result.get("agent_id"),
                "subagent": subagent_name,
                "task": task_text,
                "result": result["result"],
                "tools_used": result["tools_used"],
                "success": result["success"],
            },
            error=result.get("error"),
            error_type="state" if result.get("error") else None,
        )
