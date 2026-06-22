"""ArchiveMemoryTool — agent-controlled memory archival.

Source: 7-plane redesign spec §10 lines 2043-2061.
Inspired by Letta archival_memory_insert — agent controls what to archive.
"""

from cognitiveplane.control.tools import BrainTool, ToolResult
from cognitiveplane.shared.ports.memory import MemoryWritePort


class ArchiveMemoryTool(BrainTool):
    """Archive knowledge to long-term memory. Agent-controlled, no auto-promotion."""

    def __init__(self, port: MemoryWritePort) -> None:
        self._port = port

    @property
    def name(self) -> str:
        return "archive_memory"

    @property
    def description(self) -> str:
        return (
            "Archive content to long-term memory. Agent-controlled: the Brain decides "
            "what to store, no automatic promotion. Use to persist important findings, "
            "decisions, or learned patterns for future reference."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Content to archive",
                },
                "memory_type": {
                    "type": "string",
                    "description": "Type of memory (approved_decision, experience, knowledge)",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for categorization and retrieval",
                },
            },
            "required": ["content", "memory_type"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        from cognitiveplane.shared.ports.memory import MemoryWriteInput
        from cognitiveplane.shared.dto_memory import MemoryContent
        from cognitiveplane.shared.enums import MemoryType
        from uuid import uuid4

        memory_type_str = kwargs["memory_type"]
        try:
            memory_type = MemoryType(memory_type_str)
        except ValueError:
            memory_type = MemoryType.EXPERIENCE

        content = MemoryContent(
            summary=kwargs["content"][:200],
            details={"full_content": kwargs["content"], "tags": kwargs.get("tags", [])},
            feature_vector=[0.0],
        )

        try:
            from cognitiveplane.shared.types import DecisionId
            output = await self._port.write(MemoryWriteInput(
                memory_type=memory_type,
                content=content,
                source_decision_id=DecisionId(value=uuid4()),
            ))
            return ToolResult(output={
                "memory_id": str(output.memory_id) if hasattr(output, 'memory_id') else "archived",
                "memory_type": memory_type.value,
                "status": "archived",
            })
        except Exception as e:
            return ToolResult(error=str(e))
