"""BrainTool ABC and ToolResult — base class for all Brain tools.

Source: 7-plane redesign spec §7 BrainTool ABC.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Result of a tool execution."""
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    error_type: str | None = None  # "vision_unavailable" | "vision_transient" | "invalid_image" | None
    # 中间进度事件（如 workflow node 逐步完成），由 ReActEngine 转发为 SSE 事件
    progress_events: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        result: dict[str, Any] = {"output": self.output}
        if self.error is not None:
            result["error"] = self.error
        if self.error_type is not None:
            result["error_type"] = self.error_type
        return result


class BrainTool(ABC):
    """Base class for all Brain tools. Each tool wraps one or more ports.

    Phase gate (boundary-pinning 2026-06-25): subclass 可声明 class attribute `phase`
    控制该工具对 LLM 可见的最早系统阶段. ToolRegistry.current_phase < tool.phase 时,
    工具仍注册 (execute() 可被显式调用) 但不出现在 get_llm_tool_definitions() 里,
    LLM 看不到也调不到. 默认 phase=1 表示从阶段 1 起就可见.

    subagent_only (对齐 Codex sub-agent 工具隔离): 设为 True 时，工具只对声明了
    tools 白名单的 subagent 可见，主 agent 永远看不到。用于把专业化工具封装
    在 subagent 内部，不让主 agent 直接调用黑盒。

    always_available (基础工具层): 设为 True 时，工具豁免 skill allowed_tools 白名单
    限制——无论当前锁定哪个 skill，该工具对 LLM 始终可见可调。用于知识检索/只读/
    询问类"基础能力"工具（search_standards/search_cases/search_vision_knowledge/
    search_reasoning_knowledge/web_search/read_weldmap/request_confirmation 等），
    对齐先进 agent 的"知识层 always-on"理念——检索能力不该被场景化 skill 锁死。
    仍受 phase + subagent_only 限制。
    """

    phase: int = 1
    subagent_only: bool = False
    always_available: bool = False

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters_schema(self) -> dict: ...

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult: ...

    def to_function_definition(self) -> dict:
        """Convert to LLM Function Calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            }
        }

    def to_embedding_description(self) -> str:
        """Text description for semantic matching (Layer 3 fallback)."""
        return f"{self.name}: {self.description}"
