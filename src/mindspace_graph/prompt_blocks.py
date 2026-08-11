"""Immutable prompt blocks and deterministic final-message compilation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

_PHASE_ORDER = {
    "stable_prefix": 0,
    "retrieval_context": 10,
    "history_time_index": 20,
    "recent_history": 30,
    "dynamic_tail": 40,
}


@dataclass(frozen=True, slots=True)
class PromptBlock:
    """One immutable model-visible message with non-rendered audit metadata."""

    block_id: str
    role: str
    content: str
    phase: str
    order: int
    cache_boundary: str
    audit_metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.block_id:
            raise ValueError("prompt block id cannot be empty")
        if not self.role:
            raise ValueError("prompt block role cannot be empty")
        object.__setattr__(
            self,
            "audit_metadata",
            tuple((str(key), str(value)) for key, value in self.audit_metadata),
        )

    def render(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}

    def audit_record(self) -> dict[str, object]:
        return {
            "block_id": self.block_id,
            "role": self.role,
            "phase": self.phase,
            "order": self.order,
            "cache_boundary": self.cache_boundary,
            "content_length": len(self.content),
            "metadata": dict(self.audit_metadata),
        }


@dataclass(frozen=True, slots=True)
class PromptCompiler:
    """Build a deterministic prompt without changing model-visible messages."""

    blocks: tuple[PromptBlock, ...] = ()

    def add(self, block: PromptBlock) -> PromptCompiler:
        return PromptCompiler((*self.blocks, block))

    def extend(self, blocks: Iterable[PromptBlock]) -> PromptCompiler:
        return PromptCompiler((*self.blocks, *tuple(blocks)))

    def extend_messages(
        self,
        messages: Iterable[Mapping[str, object]],
        *,
        id_prefix: str,
        phase: str,
        cache_boundary: str,
        audit_metadata: tuple[tuple[str, str], ...] = (),
    ) -> PromptCompiler:
        start = 1 + max(
            (block.order for block in self.blocks if block.phase == phase),
            default=-1,
        )
        additions = (
            PromptBlock(
                block_id=f"{id_prefix}:{index}",
                role=str(message["role"]),
                content=str(message["content"]),
                phase=phase,
                order=start + index,
                cache_boundary=cache_boundary,
                audit_metadata=audit_metadata,
            )
            for index, message in enumerate(messages)
        )
        return self.extend(additions)

    def compiled_blocks(self) -> tuple[PromptBlock, ...]:
        indexed = list(enumerate(self.blocks))
        indexed.sort(
            key=lambda item: (
                _PHASE_ORDER.get(item[1].phase, len(_PHASE_ORDER) * 10),
                item[1].order,
                item[0],
            )
        )
        unique: list[PromptBlock] = []
        seen: dict[str, PromptBlock] = {}
        for _, block in indexed:
            previous = seen.get(block.block_id)
            if previous is None:
                seen[block.block_id] = block
                unique.append(block)
                continue
            if previous != block:
                raise ValueError(f"conflicting prompt block id: {block.block_id}")
        return tuple(unique)

    def render(self) -> list[dict[str, str]]:
        return [block.render() for block in self.compiled_blocks()]

    def audit_records(self) -> list[dict[str, object]]:
        return [block.audit_record() for block in self.compiled_blocks()]
