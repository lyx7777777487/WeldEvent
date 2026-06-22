"""Block-based working memory — inspired by Letta Block.

Source: 7-plane redesign spec §10 Block-Based Working Memory.
Phase 2: migrate to ORM with SQLAlchemy version_id_col for optimistic locking.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class MemoryBlock:
    """A tagged block of working memory.

    Inspired by Letta Block (schemas/block.py:13-90).
    Phase 1: application-layer version tracking.
    Phase 2: replace with ORM version_id_col.
    """
    label: str
    value: str
    limit: int
    read_only: bool = False
    description: str = ""
    tags: list[str] = field(default_factory=list)
    _version: int = field(default=0, repr=False)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class BlockVersion:
    """Snapshot of a block at a point in time — enables undo/redo.

    Modeled after Letta BlockHistory (orm/block_history.py:11-47).
    Checkpoint is explicit (not automatic on every update).
    """
    version: int
    value: str
    timestamp: datetime
    change_reason: str | None = None


class BlockManager:
    """Manages working memory blocks with version history.

    Inspired by Letta BlockManager (services/block_manager.py).
    """

    def __init__(self) -> None:
        self._blocks: dict[str, MemoryBlock] = {}
        self._history: dict[str, list[BlockVersion]] = {}

    def create_block(
        self,
        label: str,
        value: str = "",
        limit: int = 2000,
        read_only: bool = False,
        description: str = "",
        tags: list[str] | None = None,
    ) -> MemoryBlock:
        """Create a new block."""
        block = MemoryBlock(
            label=label,
            value=value,
            limit=limit,
            read_only=read_only,
            description=description,
            tags=tags or [],
        )
        self._blocks[label] = block
        return block

    def compile_to_prompt(self) -> str:
        """Compile all blocks into XML system prompt.

        Modeled after Letta _render_memory_blocks_standard (schemas/memory.py:131-188):
        handwritten XML via string concatenation, not template engine.
        """
        parts = ["<memory_blocks>"]
        for label, block in sorted(self._blocks.items()):
            escaped = block.value.replace("<", "&lt;").replace(">", "&gt;")
            desc = block.description.replace("<", "&lt;").replace(">", "&gt;") if block.description else ""
            parts.append(f"<{label}>")
            if desc:
                parts.append(f"<description>{desc}</description>")
            parts.append("<metadata>")
            if block.read_only:
                parts.append("- read_only=true")
            parts.append(f"- chars_current={len(block.value)}")
            parts.append(f"- chars_limit={block.limit}")
            parts.append("</metadata>")
            parts.append("<value>")
            parts.append(escaped)
            parts.append("</value>")
            parts.append(f"</{label}>")
        parts.append("</memory_blocks>")
        return "\n".join(parts)

    def update(self, label: str, new_value: str, reason: str | None = None) -> MemoryBlock:
        """Update block value. Does NOT auto-checkpoint (consistent with Letta)."""
        block = self._blocks.get(label)
        if not block:
            raise KeyError(f"No block: {label}")
        if block.read_only:
            raise PermissionError(f"Block {label} is read-only")
        if len(new_value) > block.limit:
            new_value = new_value[:block.limit]

        block._version += 1
        block.value = new_value
        block.updated_at = datetime.now(timezone.utc)
        return block

    def checkpoint(self, label: str, reason: str | None = None) -> None:
        """Explicitly checkpoint a block's current state.

        Modeled after Letta checkpoint_block_async — checkpoints are explicit,
        not automatic on every update.
        """
        block = self._blocks.get(label)
        if not block:
            raise KeyError(f"No block: {label}")
        self._history.setdefault(label, []).append(BlockVersion(
            version=block._version,
            value=block.value,
            timestamp=datetime.now(timezone.utc),
            change_reason=reason,
        ))

    def undo(self, label: str) -> MemoryBlock:
        """Revert block to previous checkpointed version.

        Modeled after Letta undo_checkpoint_block (block_manager.py:952).
        """
        history = self._history.get(label, [])
        if not history:
            raise ValueError(f"No checkpoint history for block: {label}")
        last = history.pop()
        block = self._blocks[label]
        block.value = last.value
        block._version = last.version
        block.updated_at = datetime.now(timezone.utc)
        return block

    def get_block(self, label: str) -> MemoryBlock | None:
        return self._blocks.get(label)

    def query_by_tags(self, tags: list[str]) -> list[MemoryBlock]:
        return [b for b in self._blocks.values() if any(t in b.tags for t in tags)]

    def list_blocks(self) -> list[str]:
        return list(self._blocks.keys())
