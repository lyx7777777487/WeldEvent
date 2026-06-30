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
    """

    phase: int = 1

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
