"""Ports isolate workflow decisions from storage, retrieval, and model vendors."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from mindspace_graph.context_ledger import ContextLedger
from mindspace_graph.entity_registry import EntityRegistry
from mindspace_graph.models import (
    ApiConfig,
    ChatRequest,
    DeletionEvent,
    JsonUpdatePlan,
    JsonWriteReceipt,
    ModelUsage,
    ProfileBundle,
    RetrievedChunk,
    RoleValidation,
)
from mindspace_graph.product_database import ProductDatabase


class RetrieverPort(Protocol):
    def search_knowledge(self, query: str, k: int, **kwargs: Any) -> list[RetrievedChunk]: ...

    def search_chat(
        self, query: str, session_id: str, k: int, **kwargs: Any
    ) -> list[RetrievedChunk]: ...

    def record_retrieval(
        self,
        candidates: list[RetrievedChunk],
        selected: list[RetrievedChunk],
        current_round: int,
    ) -> None: ...


class StructuredMemoryPort(Protocol):
    def record_turn(
        self,
        request: ChatRequest,
        reply: str,
        *,
        persisted: dict[str, str],
        write_receipt: JsonWriteReceipt,
    ) -> dict[str, int]: ...


class ProfileRepositoryPort(Protocol):
    def load_bundle(self) -> ProfileBundle: ...

    def apply_json_update(
        self, plan: JsonUpdatePlan, *, request: ChatRequest
    ) -> JsonWriteReceipt: ...


class SessionRepositoryPort(Protocol):
    def load_recent(self, session_id: str, limit: int = 10) -> list[dict[str, Any]]: ...

    def load_all(self, session_id: str) -> list[dict[str, Any]]: ...

    def load_pending_deletions(self, session_id: str) -> list[DeletionEvent]: ...

    def resolve_deletions(self, event_ids: list[str]) -> None: ...

    def persist_turn(
        self,
        request: ChatRequest,
        reply: str,
        *,
        replace_round: bool,
        write_receipt: JsonWriteReceipt,
    ) -> dict[str, str]: ...


class LanguageModelPort(Protocol):
    def generate(self, messages: list[dict[str, str]], config: ApiConfig) -> str: ...

    def repair(
        self,
        messages: list[dict[str, str]],
        raw_output: str,
        errors: list[str],
        config: ApiConfig,
    ) -> str: ...

    def stream(self, messages: list[dict[str, str]], config: ApiConfig) -> Iterator[str]: ...

    def stream_repair(
        self,
        messages: list[dict[str, str]],
        raw_output: str,
        errors: list[str],
        config: ApiConfig,
    ) -> Iterator[str]: ...

    def compact(self, messages: list[dict[str, str]], config: ApiConfig) -> str: ...

    def audit_role(self, messages: list[dict[str, str]], config: ApiConfig) -> str: ...

    def plan_capabilities(self, messages: list[dict[str, str]], config: ApiConfig) -> str: ...

    def preflight(
        self,
        messages: list[dict[str, str]],
        config: ApiConfig,
        *,
        timeout_seconds: float,
    ) -> str: ...

    def extract_memory(
        self,
        messages: list[dict[str, str]],
        config: ApiConfig,
        *,
        timeout_seconds: float,
    ) -> str: ...

    def review_research(
        self,
        messages: list[dict[str, str]],
        config: ApiConfig,
        *,
        timeout_seconds: float,
    ) -> str: ...

    def take_usage(self) -> ModelUsage | None: ...


class RolePolicyPort(Protocol):
    def validate(
        self,
        response: str,
        *,
        request: ChatRequest,
        history: list[dict[str, Any]],
    ) -> RoleValidation: ...


class AuditPort(Protocol):
    def record(self, event: str, payload: dict[str, Any]) -> None: ...


class CancellationPort(Protocol):
    def is_cancelled(self, request_id: str) -> bool: ...


class EmotionPort(Protocol):
    """Stable extension point for an optional, out-of-band emotion provider."""

    def enabled(self) -> bool: ...

    def previous_for_round(self, session_id: str, round_num: int) -> Any | None: ...

    def schedule_post_turn(self, *args: Any, **kwargs: Any) -> Any: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class Dependencies:
    retriever: RetrieverPort
    profiles: ProfileRepositoryPort
    sessions: SessionRepositoryPort
    llm: LanguageModelPort
    role_policy: RolePolicyPort
    audit: AuditPort
    cancellation: CancellationPort | None = None
    memory: StructuredMemoryPort | None = None
    context: ContextLedger | None = None
    database: ProductDatabase | None = None
    role_audit_enabled: bool = True
    entities: EntityRegistry | None = None
    capabilities: Any | None = None
    emotion: EmotionPort | None = None
    prompt_inspector: Any | None = None
