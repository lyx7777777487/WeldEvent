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

    def to_json(self) -> dict[str, Any]:
        result: dict[str, Any] = {"output": self.output}
        if self.error is not None:
            result["error"] = self.error
        return result


class BrainTool(ABC):
    """Base class for all Brain tools. Each tool wraps one or more ports."""

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
