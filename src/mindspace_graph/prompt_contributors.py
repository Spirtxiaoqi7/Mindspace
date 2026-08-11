"""Concrete contributors for deterministic prompt-message assembly."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from mindspace_graph.prompt_blocks import PromptBlock, PromptCompiler
from mindspace_graph.prompt_templates import build_history_time_index_template

PromptMessage = Mapping[str, object]
PromptEvent = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class StaticPrefixContributor:
    persona: str
    contract: str

    def contribute(self, compiler: PromptCompiler) -> PromptCompiler:
        return compiler.extend(
            (
                PromptBlock(
                    block_id="stable:persona",
                    role="system",
                    content=self.persona,
                    phase="stable_prefix",
                    order=0,
                    cache_boundary="provider_stable_prefix",
                    audit_metadata=(("source", "persona"),),
                ),
                PromptBlock(
                    block_id="stable:contract",
                    role="system",
                    content=self.contract,
                    phase="stable_prefix",
                    order=1,
                    cache_boundary="provider_stable_prefix",
                    audit_metadata=(("source", "contract"),),
                ),
            )
        )


@dataclass(frozen=True, slots=True)
class PrefixContributor:
    messages: tuple[PromptMessage, ...]
    from_context_ledger: bool

    def contribute(self, compiler: PromptCompiler) -> PromptCompiler:
        source = "context_ledger" if self.from_context_ledger else "fallback"
        cache_boundary = "context_ledger_prefix" if self.from_context_ledger else "provider_stable_prefix"
        return compiler.extend_messages(
            self.messages,
            id_prefix="prefix",
            phase="stable_prefix",
            cache_boundary=cache_boundary,
            audit_metadata=(("source", source),),
        )


@dataclass(frozen=True, slots=True)
class RetrievalContributor:
    events: tuple[PromptEvent, ...]

    def contribute(self, compiler: PromptCompiler) -> PromptCompiler:
        return compiler.extend_messages(
            ({"role": str(event["role"]), "content": str(event["content"])} for event in self.events),
            id_prefix="retrieval",
            phase="retrieval_context",
            cache_boundary="dynamic_tail",
            audit_metadata=(("source", "pending_event"),),
        )


@dataclass(frozen=True, slots=True)
class HistoryTimeIndexContributor:
    rendered_index: str

    def contribute(self, compiler: PromptCompiler) -> PromptCompiler:
        if not self.rendered_index:
            return compiler
        return compiler.extend_messages(
            ({"role": "system", "content": build_history_time_index_template(self.rendered_index)},),
            id_prefix="history-time",
            phase="history_time_index",
            cache_boundary="dynamic_history",
            audit_metadata=(("source", "physical_time_index"),),
        )


@dataclass(frozen=True, slots=True)
class RecentHistoryContributor:
    messages: tuple[PromptMessage, ...]

    def contribute(self, compiler: PromptCompiler) -> PromptCompiler:
        return compiler.extend_messages(
            self.messages,
            id_prefix="history",
            phase="recent_history",
            cache_boundary="dynamic_history",
            audit_metadata=(("source", "direct_history"),),
        )


@dataclass(frozen=True, slots=True)
class DynamicTailContributor:
    events: tuple[PromptEvent, ...]

    def contribute(self, compiler: PromptCompiler) -> PromptCompiler:
        return compiler.extend_messages(
            ({"role": str(event["role"]), "content": str(event["content"])} for event in self.events),
            id_prefix="tail",
            phase="dynamic_tail",
            cache_boundary="dynamic_tail",
            audit_metadata=(("source", "pending_event"),),
        )


def build_static_prompt_messages(persona: str, contract: str) -> list[dict[str, str]]:
    compiler = StaticPrefixContributor(persona=persona, contract=contract).contribute(PromptCompiler())
    return compiler.render()


def compile_prompt_messages(
    *,
    prefix_messages: Iterable[PromptMessage],
    prefix_from_context_ledger: bool,
    retrieval_events: Iterable[PromptEvent],
    history_time_index: str,
    history_messages: Iterable[PromptMessage],
    tail_events: Iterable[PromptEvent],
) -> list[dict[str, str]]:
    contributors = (
        PrefixContributor(tuple(prefix_messages), prefix_from_context_ledger),
        RetrievalContributor(tuple(retrieval_events)),
        HistoryTimeIndexContributor(history_time_index),
        RecentHistoryContributor(tuple(history_messages)),
        DynamicTailContributor(tuple(tail_events)),
    )
    compiler = PromptCompiler()
    for contributor in contributors:
        compiler = contributor.contribute(compiler)
    return compiler.render()
